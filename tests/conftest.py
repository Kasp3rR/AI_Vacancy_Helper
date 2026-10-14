import asyncio
import os
from uuid import uuid4

import httpx
import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("JWT_SECRET", "test-only-secret-never-used-in-production-12345")

from app.config import Settings, get_settings  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import create_app  # noqa: E402


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


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        jwt_secret="test-only-secret-abcdefghijklmnopqrstuvwxyz",
        upload_dir=tmp_path / "uploads",
    )


@pytest.fixture
def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (72, 72), "Python Developer. Skills: Python, PostgreSQL. Experience: 3 years."
        )
        return doc.tobytes()


@pytest.fixture
def profile_data():
    return {
        "title": "Python Developer",
        "skills": ["Python", "PostgreSQL"],
        "keywords": ["backend"],
        "experience_years": 3.0,
        "salary": None,
        "currency": None,
        "region": None,
    }


class APIClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def call():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(call())


@pytest.fixture
def api(settings, mocker):
    mocker.patch("app.api.resumes.publish_job", return_value=True)
    mocker.patch("app.api.search.publish_job", return_value=True)
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL API tests")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        sessions = sessionmaker(engine, expire_on_commit=False)
        app = create_app()

        def db_dependency():
            with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = db_dependency
        app.dependency_overrides[get_settings] = lambda: settings
        client = APIClient(app)
        client.sessions = sessions
        yield client
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def register(api, email="user@example.com"):
    response = api.request(
        "POST", "/auth/register", json={"email": email, "password": "safe-password-123"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def bearer(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}
