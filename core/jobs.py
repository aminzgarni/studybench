"""Tiny background job runner. One worker: local models and Whisper share one machine."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=1)
JOBS = {}
_lock = threading.Lock()


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, label, meta):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.label, self.meta = kind, label, meta
        self.status = "queued"       # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = ""
        self.cancelled = False
        self.created = time.time()

    def set(self, p=None, msg=None):
        if p is not None:
            self.progress = max(0.0, min(1.0, p))
        if msg is not None:
            self.message = msg

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def out(self):
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message,
                "error": self.error, "result": self.result, "meta": self.meta}


def _run(job, fn):
    if job.cancelled:
        job.status = "cancelled"
        return
    job.status = "running"
    try:
        job.result = fn(job)
        job.status = "cancelled" if job.cancelled else "done"
        job.progress = 1.0 if job.status == "done" else job.progress
    except Cancelled:
        job.status = "cancelled"
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        job.status = "error"
        job.error = str(e) or e.__class__.__name__


def submit(kind, label, fn, **meta):
    job = Job(kind, label, meta)
    with _lock:
        JOBS[job.id] = job
        for jid in sorted(JOBS, key=lambda i: JOBS[i].created)[:-60]:
            if JOBS[jid].status in ("done", "error", "cancelled"):
                JOBS.pop(jid, None)
    _pool.submit(_run, job, fn)
    return job


def listing():
    with _lock:
        return [j.out() for j in sorted(JOBS.values(), key=lambda j: j.created)]


def cancel(jid):
    j = JOBS.get(jid)
    if j:
        j.cancelled = True
    return bool(j)


def wait(job, timeout=60):
    t0 = time.time()
    while job.status in ("queued", "running") and time.time() - t0 < timeout:
        time.sleep(0.05)
    return job
