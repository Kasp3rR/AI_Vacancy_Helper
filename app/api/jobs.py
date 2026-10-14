from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.dependencies import CurrentUser, Db
from app.models import Job, Resume
from app.schemas import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, user: CurrentUser, db: Db) -> Job:
    job = db.scalar(
        select(Job).join(Resume).where(Job.id == str(job_id), Resume.user_id == user.id)
    )
    if job is None:
        raise HTTPException(404, "Job not found")
    return job
