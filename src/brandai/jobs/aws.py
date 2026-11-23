from __future__ import annotations

import ipaddress
import os
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock, Timer
from typing import Protocol
from uuid import uuid4

import httpx

from brandai.adapters.workers import WorkerLease


class EC2Gateway(Protocol):
    def launch(self, request_token: str) -> str: ...
    def ready_endpoint(self, instance_id: str) -> str | None: ...
    def terminate(self, instance_id: str) -> None: ...


class WorkerState(StrEnum):
    IDLE = "IDLE"
    LAUNCHING = "LAUNCHING"
    RUNNING_BATCH = "RUNNING_BATCH"
    DRAINING = "DRAINING"
    TERMINATED = "TERMINATED"


class AWSEC2OnDemandGPUWorker:
    """One queue-owned instance, one active GPU lease, bounded idle and wall time."""

    def __init__(
        self, ec2: EC2Gateway, *, idle_seconds: float = 90, startup_seconds: float = 300,
        max_runtime_seconds: float = 3600, poll_seconds: float = 2, auto_idle: bool = True,
        monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ):
        if not 0 < idle_seconds < max_runtime_seconds or not 0 < poll_seconds <= startup_seconds < max_runtime_seconds:
            raise ValueError("invalid EC2 worker timing limits")
        self.ec2, self.idle, self.startup, self.max_runtime = ec2, idle_seconds, startup_seconds, max_runtime_seconds
        self.poll, self.clock, self.sleep, self.auto_idle = poll_seconds, monotonic, sleep, auto_idle
        self.state = WorkerState.IDLE
        self.instance_id: str | None = None
        self._endpoint: str | None = None
        self._token: str | None = None
        self._started: float | None = None
        self._drained: float | None = None
        self._active = False
        self._closing = False
        self._expiry: Timer | None = None
        self._lock = RLock()

    def _cancel_timer(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None

    def _timer(self, seconds: float) -> None:
        self._cancel_timer()
        if self.auto_idle:
            self._expiry = Timer(max(.05, seconds), self._idle_tick)
            self._expiry.daemon = True
            self._expiry.start()

    def _idle_tick(self) -> None:
        with self._lock:
            try:
                self.tick(queued=False)
            except Exception:
                # Keep the instance ID and try termination again after transient EC2 failures.
                self._timer(self.poll)

    def _terminate(self) -> None:
        self.state = WorkerState.DRAINING
        if self.instance_id is not None:
            self.ec2.terminate(self.instance_id)
        self._cancel_timer()
        self.instance_id = self._endpoint = self._token = None
        self._started = self._drained = None
        self.state = WorkerState.TERMINATED

    def tick(self, *, queued: bool) -> WorkerState:
        with self._lock:
            now = self.clock()
            if self.state in {WorkerState.IDLE, WorkerState.TERMINATED}:
                if not queued or self._closing:
                    return self.state
                self.state = WorkerState.LAUNCHING
                self._token = str(uuid4())
                self._started = now
            if self.state == WorkerState.LAUNCHING:
                if not queued or self._closing or now - self._started >= self.startup:
                    # Recover an ID even when the first EC2 response was ambiguous.
                    if self.instance_id is None:
                        self.instance_id = self.ec2.launch(self._token)
                    self._terminate()
                    return self.state
                if self.instance_id is None:
                    self._timer(self.startup)
                    self.instance_id = self.ec2.launch(self._token)
                if endpoint := self.ec2.ready_endpoint(self.instance_id):
                    self._cancel_timer()
                    self._endpoint, self.state = endpoint, WorkerState.RUNNING_BATCH
                    self._timer(self.max_runtime - (now - self._started))
                return self.state

            expired = now - self._started >= self.max_runtime
            if self._active:
                if expired or self._closing:
                    self.state = WorkerState.DRAINING
                return self.state
            if expired or self._closing:
                self._terminate()
            elif queued:
                self.state, self._drained = WorkerState.RUNNING_BATCH, None
                self._timer(self.max_runtime - (now - self._started))
            else:
                self.state = WorkerState.DRAINING
                if self._drained is None:
                    self._drained = now
                remaining = min(self.idle - (now - self._drained), self.max_runtime - (now - self._started))
                if remaining <= 0:
                    self._terminate()
                else:
                    self._timer(remaining)
            return self.state

    @contextmanager
    def lease(self, capability: str):
        capabilities = frozenset({"llm", "image_generation", "foreground_segmentation"})
        if capability not in capabilities:
            raise ValueError("EC2 GPU worker does not provide this capability")
        deadline = self.clock() + self.startup
        while True:
            with self._lock:
                if self._closing:
                    raise RuntimeError("the GPU worker is closing")
                state = self.tick(queued=True)
                if state == WorkerState.RUNNING_BATCH and not self._active:
                    self._active = True
                    lease = WorkerLease(self._endpoint, self.instance_id, capabilities)
                    break
                if self.clock() >= deadline or state == WorkerState.TERMINATED:
                    raise TimeoutError("the GPU worker did not become available before its deadline")
            self.sleep(self.poll)
        try:
            yield lease
        finally:
            with self._lock:
                self._active = False
                self.tick(queued=False)

    def close(self) -> None:
        with self._lock:
            self._closing = True
            self.tick(queued=False)


@dataclass(frozen=True)
class EC2Config:
    region: str
    launch_template_id: str
    launch_template_version: str
    deployment: str
    port: int = 8080
    max_runtime_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.region not in {"us-east-2", "us-west-2"}:
            raise ValueError("configure EC2 in us-east-2 or us-west-2")
        if not re.fullmatch(r"lt-[0-9a-f]+", self.launch_template_id) or not re.fullmatch(r"[1-9][0-9]*", self.launch_template_version):
            raise ValueError("pin a numeric version of an EC2 launch template")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", self.deployment):
            raise ValueError("invalid EC2 deployment tag")
        if not 1024 <= self.port <= 65535 or not 900 <= self.max_runtime_seconds <= 7200:
            raise ValueError("invalid EC2 port or absolute runtime cap")


class BotoEC2Gateway:
    def __init__(self, client, config: EC2Config, health: Callable[[str], bool] | None = None):
        self.client, self.config = client, config
        self.health = health or self._health
        self._deadlines: dict[str, str] = {}

    @staticmethod
    def _health(endpoint: str) -> bool:
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                return client.get(f"{endpoint}/health").status_code == 200
        except httpx.HTTPError:
            return False

    def launch(self, request_token: str) -> str:
        config = self.config
        # RunInstances also compares tag parameters for an idempotent retry.
        deadline = self._deadlines.setdefault(request_token, (
            datetime.now(UTC) + timedelta(seconds=config.max_runtime_seconds + 600)
        ).isoformat())
        result = self.client.run_instances(
            LaunchTemplate={"LaunchTemplateId": config.launch_template_id, "Version": config.launch_template_version},
            InstanceType="g7.2xlarge", MinCount=1, MaxCount=1, ClientToken=request_token,
            InstanceInitiatedShutdownBehavior="terminate", DisableApiTermination=False,
            MetadataOptions={"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1},
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "BrandAIWorker", "Value": config.deployment}, {"Key": "TerminateAfter", "Value": deadline},
            ]}],
        )
        return result["Instances"][0]["InstanceId"]

    def ready_endpoint(self, instance_id: str) -> str | None:
        try:
            result = self.client.describe_instances(InstanceIds=[instance_id])
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"InvalidInstanceID.NotFound", "RequestLimitExceeded", "Throttling", "ServiceUnavailable"}:
                return None
            raise
        rows = [instance for reservation in result["Reservations"] for instance in reservation["Instances"]]
        if not rows or rows[0]["State"]["Name"] in {"pending", "stopping"}:
            return None
        instance = rows[0]
        if instance["State"]["Name"] != "running" or instance.get("PublicIpAddress"):
            raise RuntimeError("the provisioned EC2 worker is unavailable or has a public address")
        address = ipaddress.IPv4Address(instance["PrivateIpAddress"])
        private_blocks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if not any(address in ipaddress.IPv4Network(block) for block in private_blocks):
            raise RuntimeError("the EC2 worker is outside the private VPC address range")
        endpoint = f"http://{address}:{self.config.port}"
        return endpoint if self.health(endpoint) else None

    def terminate(self, instance_id: str) -> None:
        self.client.terminate_instances(InstanceIds=[instance_id])


def worker_from_env() -> AWSEC2OnDemandGPUWorker:
    # The optional AWS SDK is imported only when the worker is explicitly requested.
    import boto3
    from botocore.config import Config

    config = EC2Config(
        region=os.environ.get("BRANDAI_AWS_REGION", "us-east-2"),
        launch_template_id=os.environ["BRANDAI_AWS_LAUNCH_TEMPLATE_ID"],
        launch_template_version=os.environ["BRANDAI_AWS_LAUNCH_TEMPLATE_VERSION"],
        deployment=os.environ["BRANDAI_AWS_DEPLOYMENT"],
        port=int(os.environ.get("BRANDAI_AWS_WORKER_PORT", "8080")),
        max_runtime_seconds=int(os.environ.get("BRANDAI_AWS_MAX_RUNTIME_SECONDS", "3600")),
    )
    client = boto3.client("ec2", region_name=config.region, config=Config(
        connect_timeout=5, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"},
    ))
    return AWSEC2OnDemandGPUWorker(
        BotoEC2Gateway(client, config),
        idle_seconds=float(os.environ.get("BRANDAI_AWS_IDLE_SECONDS", "90")),
        startup_seconds=float(os.environ.get("BRANDAI_AWS_STARTUP_SECONDS", "300")),
        max_runtime_seconds=config.max_runtime_seconds,
    )
