from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Job, Recommendation
from app.schemas import SearchSpec
from app.services.hh import HHClient, HHError


class SearchProcessingFailed(Exception):
    pass


def process_search(job_id: str, sessions: sessionmaker[Session], settings: Settings) -> str:
    with sessions() as db:
        params = db.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.kind == "search",
                Job.status == "queued",
            )
            .values(status="running", error=None)
            .returning(Job.params)
        )
        db.commit()
        if params is None:
            return "ignored"
    try:
        spec = SearchSpec.model_validate(params)
        vacancies = HHClient(settings).search_all(spec.query, max_pages=spec.max_pages)
        with sessions() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.status != "running":
                return "ignored"
            db.execute(delete(Recommendation).where(Recommendation.job_id == job_id))
            for position, vacancy in enumerate(vacancies):
                db.add(
                    Recommendation(
                        job_id=job_id,
                        vacancy_id=vacancy.id,
                        position=position,
                        score=None,
                        data=vacancy.model_dump(mode="json", by_alias=True),
                    )
                )
            job.status = "succeeded"
            job.error = None
            db.commit()
        return "succeeded"
    except Exception as exc:
        message = "Vacancy search failed"
        if isinstance(exc, HHError):
            message = str(exc)
        elif isinstance(exc, SoftTimeLimitExceeded):
            message = "Processing time limit exceeded"
        with sessions() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "running")
                .values(status="failed", error=message)
            )
            db.commit()
        raise SearchProcessingFailed(message) from None
