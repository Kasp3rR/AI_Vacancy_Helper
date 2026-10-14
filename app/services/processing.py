from pathlib import Path

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Job, Resume
from app.services.ollama import InvalidProfile, OllamaTimeout, OllamaUnavailable, extract_profile
from app.services.pdf import InvalidPDF, load_text


class ResumeProcessingFailed(Exception):
    pass


def error_message(exc: Exception) -> str:
    if isinstance(exc, InvalidPDF):
        return str(exc)
    if isinstance(exc, (OllamaTimeout, OllamaUnavailable, InvalidProfile)):
        return str(exc)
    if isinstance(exc, SoftTimeLimitExceeded):
        return "Processing time limit exceeded"
    if isinstance(exc, OSError):
        return "Resume storage unavailable"
    return "Resume processing failed"


def process_resume(job_id: str, sessions: sessionmaker[Session], settings: Settings) -> str:
    with sessions() as db:
        resume_id = db.scalar(
            update(Job)
            .where(
                Job.id == job_id,
                Job.kind == "resume",
                Job.status == "queued",
            )
            .values(status="running", error=None)
            .returning(Job.resume_id)
        )
        db.commit()
        if resume_id is None:
            return "ignored"
    try:
        with sessions() as db:
            resume = db.get(Resume, resume_id)
            if resume is None:
                raise ResumeProcessingFailed("Resume not found")
            path = Path(resume.path)
        text = load_text(path, settings)
        profile = extract_profile(text, settings)
        with sessions() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.status != "running":
                return "ignored"
            db.execute(
                update(Resume)
                .where(Resume.id == resume_id)
                .values(profile=profile.model_dump(mode="json"))
            )
            job.status = "succeeded"
            job.error = None
            db.commit()
        return "succeeded"
    except Exception as exc:
        message = error_message(exc)
        with sessions() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "running")
                .values(status="failed", error=message)
            )
            db.commit()
        raise ResumeProcessingFailed(message) from None
