from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.dependencies import CurrentUser, Db
from app.models import Job, Recommendation, Resume
from app.schemas import JobResponse, RankingDetails, SearchResult, SearchResultsResponse
from app.services.hh import Vacancy

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, user: CurrentUser, db: Db) -> Job:
    job = db.scalar(
        select(Job).join(Resume).where(Job.id == str(job_id), Resume.user_id == user.id)
    )
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{job_id}/results", response_model=SearchResultsResponse)
def get_results(
    job_id: UUID,
    user: CurrentUser,
    db: Db,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchResultsResponse:
    job = get_job(job_id, user, db)
    if job.kind != "search":
        raise HTTPException(409, "Only search jobs have vacancy results")
    if job.status != "succeeded":
        raise HTTPException(409, "Search results are not ready; check the job status")
    total = (
        db.scalar(
            select(func.count()).select_from(Recommendation).where(Recommendation.job_id == job.id)
        )
        or 0
    )
    rows = db.scalars(
        select(Recommendation)
        .where(Recommendation.job_id == job.id)
        .order_by(Recommendation.position, Recommendation.id)
        .offset(offset)
        .limit(limit)
    )
    return SearchResultsResponse(
        job=JobResponse.model_validate(job),
        ranked=job.params.get("ranking_version") == 1,
        total=total,
        offset=offset,
        limit=limit,
        items=[
            SearchResult(
                vacancy=Vacancy.model_validate(row.data),
                position=row.position,
                score=row.score,
                matched_skills=row.data.get("_ranking", {}).get("matched_skills", []),
                missing_skills=row.data.get("_ranking", {}).get("missing_skills", []),
                breakdown=RankingDetails.model_validate(row.data["_ranking"])
                if "_ranking" in row.data
                else None,
            )
            for row in rows
        ],
    )
