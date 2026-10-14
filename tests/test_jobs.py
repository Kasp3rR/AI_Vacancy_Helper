from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import select, update

from app.models import Job, Resume
from app.schemas import Profile
from app.services.jobs import publish_job, recover_jobs
from app.services.processing import ResumeProcessingFailed, process_resume
from tests.conftest import bearer, register
from tests.test_resumes import upload


def test_worker_failure_keeps_previous_profile_and_allows_retry(
    api,
    settings,
    pdf_bytes,
    profile_data,
    mocker,
):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    record, job = result["resume"], result["job"]
    with api.sessions() as db:
        db.get(Resume, record["id"]).profile = profile_data
        db.commit()
    mocker.patch("app.services.processing.extract_profile", side_effect=ValueError("private data"))
    with pytest.raises(ResumeProcessingFailed, match="Resume processing failed"):
        process_resume(job["id"], api.sessions, settings)
    status = api.request("GET", "/jobs/" + job["id"], headers=bearer(tokens)).json()
    assert status["status"] == "failed"
    assert "private" not in status["error"]
    assert api.request("GET", "/resumes/" + record["id"], headers=bearer(tokens)).json()["profile"]
    next_job = api.request("POST", "/resumes/" + record["id"] + "/analyze", headers=bearer(tokens))
    assert next_job.status_code == 202
    assert next_job.json()["id"] != job["id"]


def test_pdf_parsing_is_deferred_to_worker(api, settings, mocker):
    tokens = register(api)
    model = mocker.patch("app.services.processing.extract_profile")
    result = upload(api, tokens, b"%PDF-1.7\ncorrupt")
    assert result.status_code == 202
    with pytest.raises(ResumeProcessingFailed, match="damaged"):
        process_resume(result.json()["job"]["id"], api.sessions, settings)
    model.assert_not_called()


def test_duplicate_delivery_and_concurrent_analyze(api, settings, pdf_bytes, profile_data, mocker):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    url = "/resumes/" + result["resume"]["id"] + "/analyze"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: api.request("POST", url, headers=bearer(tokens)), range(2))
        )
    assert {r.json()["id"] for r in responses} == {result["job"]["id"]}
    entered, release = Event(), Event()

    def profile(*args):
        entered.set()
        assert release.wait(10)
        return Profile.model_validate(profile_data)

    extract = mocker.patch("app.services.processing.extract_profile", side_effect=profile)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(process_resume, result["job"]["id"], api.sessions, settings)
        try:
            assert entered.wait(10)
            status = api.request(
                "GET", "/jobs/" + result["job"]["id"], headers=bearer(tokens)
            ).json()
            assert status["status"] == "running"
            assert process_resume(result["job"]["id"], api.sessions, settings) == "ignored"
        finally:
            release.set()
        assert future.result(timeout=10) == "succeeded"
    assert process_resume(result["job"]["id"], api.sessions, settings) == "ignored"
    assert extract.call_count == 1


def test_broker_failure_keeps_job_and_recovery_republishes(api, settings, pdf_bytes, mocker):
    tokens = register(api)
    mocker.patch("app.api.resumes.publish_job", return_value=False)
    result = upload(api, tokens, pdf_bytes)
    assert result.status_code == 202
    job_id = result.json()["job"]["id"]
    mocker.patch("app.services.jobs.celery_app.send_task", side_effect=ConnectionError("offline"))
    assert publish_job(job_id) is False
    with api.sessions() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        db.commit()
    publish = mocker.patch("app.services.jobs.publish_job", return_value=True)
    assert recover_jobs(api.sessions, settings) == 1
    publish.assert_called_once_with(job_id)
    assert recover_jobs(api.sessions, settings) == 0


def test_stale_worker_cannot_overwrite_recovered_job(
    api, settings, pdf_bytes, profile_data, mocker
):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    job_id = result["job"]["id"]

    def profile(*args):
        with api.sessions() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    updated_at=datetime.now(UTC) - timedelta(seconds=settings.job_time_limit + 120)
                )
            )
            db.commit()
        recover_jobs(api.sessions, settings)
        return Profile.model_validate(profile_data)

    mocker.patch("app.services.processing.extract_profile", side_effect=profile)
    assert process_resume(job_id, api.sessions, settings) == "ignored"
    with api.sessions() as db:
        assert db.get(Job, job_id).status == "failed"
        assert db.get(Resume, result["resume"]["id"]).profile is None


def test_missing_storage_fails_and_foreign_jobs_are_hidden(api, settings, pdf_bytes):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    (settings.upload_dir / (result["resume"]["id"] + ".pdf")).unlink()
    with pytest.raises(ResumeProcessingFailed, match="storage unavailable"):
        process_resume(result["job"]["id"], api.sessions, settings)
    assert api.request("GET", "/jobs/" + result["job"]["id"]).status_code == 401
    with api.sessions() as db:
        assert list(db.scalars(select(Job.status))) == ["failed"]
