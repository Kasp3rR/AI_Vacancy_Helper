from app.celery_app import celery_app
from app.config import get_settings
from app.db import get_session_factory
from app.services.jobs import recover_jobs as recover
from app.services.processing import process_resume as process


@celery_app.task(name="app.tasks.process_resume")  # type: ignore[untyped-decorator]
def process_resume(job_id: str) -> str:
    return process(job_id, get_session_factory(), get_settings())


@celery_app.task(name="app.tasks.recover_jobs")  # type: ignore[untyped-decorator]
def recover_jobs() -> int:
    return recover(get_session_factory(), get_settings())
