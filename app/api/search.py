from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.dependencies import CurrentUser, Db
from app.models import Resume
from app.schemas import JobResponse, Profile, SearchRequest, SearchSpec
from app.services.hh import HHSearch
from app.services.jobs import ActiveSearchConflict, enqueue_search, publish_job

router = APIRouter(prefix="/resumes", tags=["search"])


@router.post("/{resume_id}/search", response_model=JobResponse, status_code=202)
def start_search(resume_id: UUID, data: SearchRequest, user: CurrentUser, db: Db) -> JobResponse:
    resume = db.scalar(
        select(Resume)
        .where(Resume.id == str(resume_id), Resume.user_id == user.id)
        .with_for_update()
    )
    if resume is None:
        raise HTTPException(404, "Resume not found")
    if resume.profile is None:
        raise HTTPException(409, "Analyze resume before searching for vacancies")
    try:
        profile = Profile.model_validate(resume.profile)
    except ValidationError:
        raise HTTPException(409, "Resume profile is invalid; analyze it again") from None
    text = data.text or (profile.title or "").strip() or " ".join(profile.skills[:5])[:200]
    if not text:
        raise HTTPException(422, "Provide search text; the profile has no title or skills")
    query = HHSearch(**(data.model_dump(exclude={"max_pages"}) | {"text": text}))
    spec = SearchSpec(query=query, max_pages=data.max_pages, profile=profile)
    try:
        job = enqueue_search(db, resume, spec)
    except ActiveSearchConflict:
        raise HTTPException(409, "A search with different parameters is already running") from None
    db.commit()
    result = JobResponse.model_validate(job)
    if job.status == "queued":
        publish_job(job.id, "search")
    return result
