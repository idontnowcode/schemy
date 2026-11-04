"""Background jobs for slow EDA work. One worker: exports run one at a time, in order."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from .errors import ApiError
from .schemas import Job
from .store import ProjectStore, now_iso

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aics-job")
        self._futures: dict[str, Future] = {}

    def submit(self, project_id: str, kind: str, fn: Callable[[str], dict]) -> Job:
        """Queue ``fn(job_id)``; its return value becomes the job result."""
        job = self.store.create_job(project_id, kind)
        self._futures[job.id] = self._pool.submit(self._run, job.id, fn)
        return job

    def _run(self, job_id: str, fn: Callable[[str], dict]) -> None:
        self.store.update_job(job_id, status="running", started_at=now_iso())
        try:
            result = fn(job_id)
        except ApiError as exc:
            self.store.update_job(job_id, status="failed", finished_at=now_iso(), error=exc.to_dict())
        except Exception as exc:  # noqa: BLE001 - a job must never take the worker down
            log.exception("job %s crashed", job_id)
            self.store.update_job(job_id, status="failed", finished_at=now_iso(), error={
                "error_code": "INTERNAL_ERROR", "message": str(exc),
                "details": traceback.format_exc(limit=5),
            })
        else:
            self.store.update_job(job_id, status="succeeded", finished_at=now_iso(), result=result)
        finally:
            self._futures.pop(job_id, None)

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """Block until a job submitted by this runner finishes (tests and CLI use)."""
        fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)
        return self.store.get_job(job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
