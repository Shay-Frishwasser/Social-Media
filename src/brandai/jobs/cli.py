from __future__ import annotations

import argparse
import os
import signal
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from sqlalchemy import select

from brandai import RuntimeSettings
from brandai.api import create_app
from brandai.db.global_models import Brand
from brandai.jobs.aws import WorkerState


@contextmanager
def _worker_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "job-worker.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit("Another BrandAI queue worker owns this runtime.") from exc
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain due BrandAI jobs from local brand databases.")
    parser.add_argument("--once", action="store_true", help="Run one worker tick for each active brand.")
    parser.add_argument("--poll-seconds", type=float, default=3)
    args = parser.parse_args()
    if not 0.2 <= args.poll_seconds <= 30:
        parser.error("--poll-seconds must be between 0.2 and 30")
    stopped = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    settings = RuntimeSettings.from_env()
    with _worker_lock(settings.root):
        _drain(settings, stopped, once=args.once, poll_seconds=args.poll_seconds)


def _drain(settings: RuntimeSettings, stopped: Event, *, once: bool, poll_seconds: float) -> None:
    app = create_app(settings, run_background_jobs=False, worker_process=True)
    store, queue, runner, worker = app.state.store, app.state.queue, app.state.job_runner, app.state.aws_worker
    try:
        while not stopped.is_set():
            with store.GlobalSession() as session:
                brands = list(session.scalars(select(Brand.id).where(Brand.status == "ACTIVE").order_by(Brand.id)))
            ran = False
            for brand_id in brands:
                if stopped.is_set():
                    break
                ran |= runner.run_next(brand_id) is not None
            if worker is not None and worker.state not in {WorkerState.IDLE, WorkerState.TERMINATED}:
                worker.tick(queued=any(queue.gpu_pending(brand_id) for brand_id in brands))
            if once:
                break
            if not ran:
                stopped.wait(poll_seconds)
    finally:
        if worker:
            worker.close()


if __name__ == "__main__":
    main()
