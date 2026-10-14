import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.celery_app import celery_app
from app.config import Settings
from app.models import Job, Resume
from app.schemas import SearchSpec

logger = logging.getLogger(__name__)


def enqueue_resume(db: Session, resume: Resume) -> Job:
    db.scalar(select(Resume).where(Resume.id == resume.id).with_for_update())
    job = db.scalar(
        select(Job).where(
            Job.resume_id == resume.id,
            Job.kind == "resume",
            Job.status.in_(["queued", "running"]),
        )
    )
    if job is None:
        job = Job(resume_id=resume.id, kind="resume")
        db.add(job)
        db.flush()
    return job


class ActiveSearchConflict(Exception):
    pass


def enqueue_search(db: Session, resume: Resume, spec: SearchSpec) -> Job:
    db.scalar(select(Resume).where(Resume.id == resume.id).with_for_update())
    params = spec.model_dump(mode="json")
    job = db.scalar(
        select(Job).where(
            Job.resume_id == resume.id, Job.kind == "search", Job.status.in_(["queued", "running"])
        )
    )
    if job is not None:
        if job.params != params:
            raise ActiveSearchConflict()
        return job
    job = Job(resume_id=resume.id, kind="search", params=params)
    db.add(job)
    db.flush()
    return job


def publish_job(job_id: str, kind: str = "resume") -> bool:
    try:
        task = {"resume": "app.tasks.process_resume", "search": "app.tasks.process_search"}[kind]
        celery_app.send_task(task, args=[job_id], task_id=job_id, retry=False)
        return True
    except Exception:
        logger.warning("Task publication deferred: %s", job_id)
        return False


def recover_jobs(sessions: sessionmaker[Session], settings: Settings) -> int:
    now = datetime.now(UTC)
    with sessions() as db:
        db.execute(
            update(Job)
            .where(
                Job.kind.in_(["resume", "search"]),
                Job.status == "running",
                Job.updated_at < now - timedelta(seconds=settings.job_time_limit + 60),
            )
            .values(status="failed", error="Worker stopped or processing time limit exceeded")
        )
        pending = list(
            db.execute(
                select(Job.id, Job.kind)
                .where(
                    Job.kind.in_(["resume", "search"]),
                    Job.status == "queued",
                    Job.updated_at < now - timedelta(seconds=30),
                )
                .order_by(Job.updated_at)
                .limit(100)
            )
        )
        db.commit()
    published = 0
    for job_id, kind in pending:
        if publish_job(job_id, kind):
            with sessions() as db:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(updated_at=datetime.now(UTC))
                )
                db.commit()
            published += 1
    return published
