from concurrent.futures import ThreadPoolExecutor

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Job, Recommendation
from app.schemas import SearchSpec
from app.services.hh import HHClient, HHError, HHNotFound, Vacancy
from app.services.ranking import rank_vacancies


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
        client = HHClient(settings)
        vacancies = client.search_all(spec.query, max_pages=spec.max_pages)

        def details(vacancy: Vacancy) -> Vacancy | None:
            try:
                return client.vacancy(vacancy.id)
            except HHNotFound:
                return None

        with ThreadPoolExecutor(max_workers=5) as pool:
            cards = []
            for card in pool.map(details, vacancies):
                if card is not None:
                    cards.append(card)
        ranked = rank_vacancies(spec.profile, cards)
        with sessions() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.status != "running":
                return "ignored"
            db.execute(delete(Recommendation).where(Recommendation.job_id == job_id))
            for position, (vacancy, ranking) in enumerate(ranked):
                data = vacancy.model_dump(mode="json", by_alias=True)
                data["_ranking"] = ranking.model_dump(mode="json")
                recommendation = Recommendation(
                    job_id=job_id,
                    vacancy_id=vacancy.id,
                    position=position,
                    score=ranking.score,
                    data=data,
                )
                db.add(recommendation)
            updated_params = job.params.copy()
            updated_params["ranking_version"] = 1
            job.params = updated_params
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
