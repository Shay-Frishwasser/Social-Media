from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from brandai import BrandAIStore, RuntimeSettings
from brandai.api import create_app
from brandai.db.brand_models import BackgroundJob
from brandai.jobs.aws import AWSEC2OnDemandGPUWorker, BotoEC2Gateway, EC2Config, WorkerState
from brandai.jobs.models import RetryJobError
from brandai.jobs.queue import JobQueue


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 23, 8, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def test_job_api_idempotence_retry_dead_letter_and_restart(tmp_path):
    clock = Clock()
    calls = []

    def onboarding(job, progress):
        progress(45)
        calls.append(job)
        if job.payload["company_name"] == "Permanent failure" or job.attempt == 1:
            raise RetryJobError("transient_test_error", 22)
        return {"profile_id": "generated-by-stub"}

    settings = RuntimeSettings(tmp_path)
    app = create_app(settings, run_background_jobs=False, job_clock=clock, job_handlers={"onboarding_research": onboarding})
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Worker test"}).json()["id"]
        prefix = f"/api/brands/{brand}"
        root = f"{prefix}/jobs"
        payload = {"kind": "onboarding_research", "idempotency_key": "sample",
                   "payload": {"website_url": "https://example.org", "company_name": "Company"}}
        initial = client.post(root, json=payload)
        assert initial.status_code == 202, initial.text
        identifier = initial.json()["id"]
        assert client.post(root, json=payload).json()["id"] == identifier
        assert client.post(root, json={**payload, "max_attempts": 8}).status_code == 409
        assert client.post(root, json={**payload, "kind": "publish"}).status_code == 422
        first = client.post(f"{root}/run-next").json()
        assert first["status"] == "RETRY_WAIT" and first["progress"] == 45 and first["attempts"] == 1
        assert client.get(root, params={"status": "RETRY_WAIT"}).json() == [first]
        assert client.post(f"{root}/run-next").json() is None

    clock.advance(22)
    restarted = create_app(settings, run_background_jobs=False, job_clock=clock,
                          job_handlers={"onboarding_research": onboarding})
    with TestClient(restarted) as client:
        result = client.post(f"{root}/run-next").json()
        assert result["id"] == identifier and result["status"] == "SUCCEEDED"
        assert result["attempts"] == 2 and result["progress"] == 100
        failed = client.post(root, json={**payload, "idempotency_key": "exhaust", "max_attempts": 1,
                                       "payload": {**payload["payload"], "company_name": "Permanent failure"}}).json()
        final = client.post(f"{root}/run-next").json()
        assert final["id"] == failed["id"] and final["status"] == "DEAD_LETTER"
        assert client.get(f"{root}/{final['id']}").json() == final
        assert client.post(f"{root}/run-next").json() is None
        other = client.post("/api/brands", json={"name": "Other"}).json()["id"]
        assert client.get(f"/api/brands/{other}/jobs/{final['id']}").status_code == 404
        legacy = client.post(f"{prefix}/research", json=payload["payload"]).json()
        assert client.get(f"/api/jobs/{legacy['job_id']}").json()["status"] == "QUEUED"


def test_atomic_claim_and_expired_lease_recovery(tmp_path):
    clock = Clock()
    store = BrandAIStore(RuntimeSettings(tmp_path))
    brand = store.create_brand("Queue lease")
    queue = JobQueue(store, clock=clock, lease_seconds=9)
    enqueued = queue.enqueue(brand.id, "onboarding_research", "once", {"website_url": "https://example.org"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [task.result() for task in (pool.submit(queue.claim, brand.id), pool.submit(queue.claim, brand.id))]
    first, = [job for job in claims if job is not None]
    clock.advance(10)
    second = queue.claim(brand.id)
    assert second.id == first.id == str(enqueued.id) and second.attempt == 2
    assert not queue.heartbeat(first, 99)
    assert queue.finish(first, result={"duplicate": True}).status == "RUNNING"
    assert queue.finish(second, result={"done": True}).status == "SUCCEEDED"
    with store.brand_session(brand.id) as session:
        row = session.get(BackgroundJob, second.id)
        assert row.result == {"done": True} and row.lease_token is None


def test_ec2_batch_lifecycle_and_idle_shutdown():
    events = []
    now = [0.]

    class EC2:
        def launch(self, token):
            events.append(("launch", token))
            return "i-stub"

        def ready_endpoint(self, instance_id):
            assert instance_id == "i-stub"
            return "http://10.0.0.1:8080" if now[0] >= 2 else None

        def terminate(self, instance_id):
            events.append(("terminate", instance_id))

    def sleep(seconds):
        now[0] += seconds

    worker = AWSEC2OnDemandGPUWorker(
        EC2(), idle_seconds=10, startup_seconds=20, max_runtime_seconds=90, poll_seconds=2,
        auto_idle=False, monotonic=lambda: now[0], sleep=sleep,
    )
    assert worker.tick(queued=False) == WorkerState.IDLE and not events
    assert worker.tick(queued=True) == WorkerState.LAUNCHING
    with worker.lease("llm") as lease:
        assert lease.worker_id == "i-stub" and worker.state == WorkerState.RUNNING_BATCH
        assert worker.tick(queued=False) == WorkerState.RUNNING_BATCH
    assert worker.state == WorkerState.DRAINING and len(events) == 1
    now[0] += 4
    with worker.lease("image_generation"):
        assert worker.state == WorkerState.RUNNING_BATCH and len(events) == 1
    now[0] += 11
    assert worker.tick(queued=False) == WorkerState.TERMINATED
    assert [key for key, _ in events] == ["launch", "terminate"]
    assert worker.tick(queued=False) == WorkerState.TERMINATED
    worker.close()


def test_ec2_startup_timeout_and_run_instances_contract():
    calls = []

    class Client:
        def run_instances(self, **kwargs):
            calls.append(kwargs)
            return {"Instances": [{"InstanceId": "i-worker"}]}

        def describe_instances(self, **kwargs):
            return {"Reservations": [{"Instances": [{"State": {"Name": "pending"}}]}]}

        def terminate_instances(self, **kwargs):
            calls.append(kwargs)

    config = EC2Config("us-east-2", "lt-aabb", "7", "brandai-test")
    gateway = BotoEC2Gateway(Client(), config)
    now = [0.]
    worker = AWSEC2OnDemandGPUWorker(gateway, idle_seconds=10, startup_seconds=20, max_runtime_seconds=90,
                                    auto_idle=False, monotonic=lambda: now[0])
    assert worker.tick(queued=True) == WorkerState.LAUNCHING
    launch = calls[0]
    assert launch["InstanceType"] == "g7.2xlarge" and launch["MinCount"] == launch["MaxCount"] == 1
    assert launch["LaunchTemplate"]["Version"] == "7" and launch["MetadataOptions"]["HttpTokens"] == "required"
    assert launch["InstanceInitiatedShutdownBehavior"] == "terminate"
    now[0] += 21
    assert worker.tick(queued=True) == WorkerState.TERMINATED
    assert calls[-1]["InstanceIds"] == ["i-worker"]


def test_aws_api_is_queue_only_and_does_not_create_ec2_client(tmp_path, monkeypatch):
    def must_not_initialize():
        raise AssertionError("only the dedicated queue process may initialize EC2")

    monkeypatch.setenv("BRANDAI_CONTENT_PROVIDER", "aws")
    monkeypatch.setattr("brandai.api.app.worker_from_env", must_not_initialize)
    app = create_app(RuntimeSettings(tmp_path))
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "AWS control plane"}).json()["id"]
        base = f"/api/brands/{brand}"
        assert client.post(f"{base}/content/generate/from-slot/{uuid4()}").status_code == 409
        assert client.post(f"{base}/visual-assets/render", json={"content_id": str(uuid4())}).status_code == 409
        assert client.post(f"{base}/jobs/run-next").status_code == 409
        pending = client.post(f"{base}/jobs", json={
            "kind": "content_generation", "idempotency_key": "aws-job", "payload": {"slot_id": str(uuid4())},
        })
        assert pending.status_code == 202 and pending.json()["status"] == "QUEUED"
