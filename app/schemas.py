from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class Credentials(BaseModel):
    email: EmailStr = Field(max_length=320)
    password: SecretStr = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    created_at: datetime


Term = Annotated[str, Field(min_length=1, max_length=100)]


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str | None = Field(max_length=200)
    skills: list[Term] = Field(max_length=100)
    keywords: list[Term] = Field(max_length=50)
    experience_years: float | None = Field(ge=0, le=80)
    salary: int | None = Field(ge=0)
    currency: Literal["RUR", "USD", "EUR"] | None
    region: str | None = Field(max_length=200)

    @field_validator("skills", "keywords")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        return sorted({v.strip().casefold() for v in values if v.strip()})


class ResumeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    profile: Profile | None
    created_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    resume_id: UUID
    kind: Literal["resume", "search"]
    status: Literal["queued", "running", "succeeded", "failed"]
    error: str | None
    created_at: datetime
    updated_at: datetime


class UploadResponse(BaseModel):
    resume: ResumeResponse
    job: JobResponse
