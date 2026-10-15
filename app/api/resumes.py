import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy import select

from app.api.dependencies import Config, CurrentUser, Db
from app.models import Resume, new_id
from app.schemas import JobResponse, ResumeResponse, UploadResponse
from app.services.jobs import enqueue_resume, publish_job
from app.services.pdf import InvalidPDF, PDFTooLarge, read_bounded

router = APIRouter(prefix="/resumes", tags=["resumes"])


def owned_resume(resume_id: UUID, user_id: str, db: Db) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.id == str(resume_id), Resume.user_id == user_id))
    if resume is None:
        raise HTTPException(404, "Resume not found")
    return resume


@router.post("", response_model=UploadResponse, status_code=202)
def upload_resume(
    user: CurrentUser,
    db: Db,
    settings: Config,
    file: Annotated[UploadFile, File()],
) -> UploadResponse:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(415, "Only PDF uploads are supported")
    try:
        data = read_bounded(file.file, settings)
        if not data.startswith(b"%PDF-"):
            raise InvalidPDF("File is not a PDF")
    except PDFTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except InvalidPDF as exc:
        raise HTTPException(422, str(exc)) from exc
    resume_id = new_id()
    path = settings.upload_dir.resolve() / f"{resume_id}.pdf"
    filename = file.filename or "resume.pdf"
    filename = filename.replace("\\", "/")
    filename = filename.split("/")[-1]
    filename = filename[:255] or "resume.pdf"
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as output:
            output.write(data)
    except OSError as exc:
        if created:
            path.unlink(missing_ok=True)
        raise HTTPException(503, "Resume storage unavailable") from exc
    resume = Resume(id=resume_id, user_id=user.id, filename=filename, path=str(path))
    try:
        db.add(resume)
        db.flush()
        job = enqueue_resume(db, resume)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    result = UploadResponse(
        resume=ResumeResponse.model_validate(resume), job=JobResponse.model_validate(job)
    )
    publish_job(job.id)
    return result


@router.get("/{resume_id}", response_model=ResumeResponse)
def get_resume(resume_id: UUID, user: CurrentUser, db: Db) -> Resume:
    return owned_resume(resume_id, user.id, db)


@router.post("/{resume_id}/analyze", response_model=JobResponse, status_code=202)
def analyze_resume(resume_id: UUID, user: CurrentUser, db: Db) -> JobResponse:
    resume = owned_resume(resume_id, user.id, db)
    job = enqueue_resume(db, resume)
    db.commit()
    result = JobResponse.model_validate(job)
    if job.status == "queued":
        publish_job(job.id)
    return result
