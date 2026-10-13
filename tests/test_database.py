"""Run against PostgreSQL; each test uses a private, rolled-back schema."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Job, Recommendation, Resume, User

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL migration checks")
    engine = create_engine(url)
    if engine.dialect.name != "postgresql":
        pytest.fail("Integration tests require PostgreSQL")
    schema = "test_" + uuid4().hex
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                config = Config("alembic.ini")
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                yield connection, config, schema
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_migration_round_trip(migrated_database):
    connection, config, schema = migrated_database
    expected = {"users", "refresh_tokens", "resumes", "jobs", "recommendations", "alembic_version"}
    assert set(inspect(connection).get_table_names(schema=schema)) == expected
    command.downgrade(config, "base")
    assert inspect(connection).get_table_names(schema=schema) == ["alembic_version"]
    command.upgrade(config, "head")
    assert set(inspect(connection).get_table_names(schema=schema)) == expected


def test_models_persist_profile_and_results(migrated_database):
    connection, _, _ = migrated_database
    with Session(connection, join_transaction_mode="create_savepoint") as session:
        user = User(email="test@example.com", password_hash="placeholder")
        session.add(user)
        session.flush()
        resume = Resume(
            user_id=user.id,
            filename="cv.pdf",
            path="uploads/cv.pdf",
            profile={"skills": ["python"]},
        )
        session.add(resume)
        session.flush()
        job = Job(resume_id=resume.id, kind="search")
        session.add(job)
        session.flush()
        result = Recommendation(
            job_id=job.id, vacancy_id="123", score=85.5, data={"matched_skills": ["python"]}
        )
        session.add(result)
        session.commit()
        session.expire_all()
        assert session.get(Resume, resume.id).profile == {"skills": ["python"]}
        assert session.get(Job, job.id).status == "queued"
        assert session.get(Recommendation, result.id).score == 85.5
        assert session.get(User, user.id).created_at.tzinfo is not None


def test_database_rejects_invalid_references(migrated_database):
    connection, _, _ = migrated_database
    with Session(connection, join_transaction_mode="create_savepoint") as session:
        session.add(Resume(user_id=str(uuid4()), filename="cv.pdf", path="cv.pdf"))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_database_rejects_invalid_status_score_and_duplicates(migrated_database):
    connection, _, _ = migrated_database
    with Session(connection, join_transaction_mode="create_savepoint") as session:
        user = User(email="unique@example.com", password_hash="placeholder")
        session.add(user)
        session.flush()
        resume = Resume(user_id=user.id, filename="cv.pdf", path="cv.pdf")
        session.add(resume)
        session.flush()
        job = Job(resume_id=resume.id, kind="search")
        session.add(job)
        session.flush()
        session.add(Recommendation(job_id=job.id, vacancy_id="123", score=80, data={}))
        session.flush()
        invalid_rows = [
            User(email=user.email, password_hash="duplicate"),
            Job(resume_id=resume.id, kind="invalid"),
            Job(resume_id=resume.id, kind="search", status="invalid"),
            Recommendation(job_id=job.id, vacancy_id="other", score=101, data={}),
            Recommendation(job_id=job.id, vacancy_id="123", score=80, data={}),
        ]
        for row in invalid_rows:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(row)
                session.flush()
