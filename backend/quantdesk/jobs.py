from datetime import timedelta
from sqlalchemy import select
from .db import Job, now
from .core import lock, event


def enqueue(s, kind, payload, key):
    lock(s)
    old = s.scalar(select(Job).where(Job.key == key))
    if old:
        return old
    j = Job(kind=kind, payload=payload, key=key)
    s.add(j); s.flush()
    return j


def claim(s, owner):
    job = s.scalar(select(Job).where(Job.status == 'pending', Job.run_at <= now())
                   .order_by(Job.run_at, Job.id).with_for_update(skip_locked=True).limit(1))
    if job:
        job.status, job.owner = 'running', owner
        job.attempts += 1
        job.lease_until = now() + timedelta(seconds=120)
    return job


def recover(s):
    lock(s)
    expired = list(s.scalars(select(Job).where(Job.status == 'running', Job.lease_until < now())))
    for job in expired:
        if job.kind == 'ai':
            job.status = 'cancelled'  # never retry a possibly already-executed decision
        else:
            job.status = 'pending'
            job.run_at = now() + timedelta(seconds=min(300, 2 ** min(job.attempts, 8)))
        job.owner = None
        event(s, 'job.recovered', {'job_id':job.id,'kind':job.kind,'status':job.status})


def complete(s, id, owner, error=None):
    j = s.get(Job, id, with_for_update=True)
    if j.status != 'running' or j.owner != owner:
        return
    if error:
        j.error = error[:200]
        if j.kind == 'ai' or j.attempts >= 5:
            j.status = 'failed'
        else:
            j.status = 'pending'
            j.run_at = now() + timedelta(seconds=min(300, 2 ** j.attempts))
    else:
        j.status = 'done'
    j.owner, j.lease_until = None, None
