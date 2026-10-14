from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import fakeredis
import httpx
import pytest
import respx
from sqlalchemy import select, update

from app.models import Job, Recommendation, Resume
from app.services.hh import HHClient, Vacancy
from app.services.hh_cache import HHCache
from app.services.jobs import recover_jobs
from app.services.search import SearchProcessingFailed, process_search
from tests.conftest import bearer, register
from tests.test_hh import page, vacancy
from tests.test_resumes import upload


@pytest.fixture
def ready(api, pdf_bytes, profile_data):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    with api.sessions() as db:
        db.get(Resume, result["resume"]["id"]).profile = profile_data
        db.get(Job, result["job"]["id"]).status = "succeeded"
        db.commit()
    return bearer(tokens), "/resumes/" + result["resume"]["id"] + "/search"


def test_search_prerequisites_and_ownership(api, pdf_bytes):
    tokens = register(api)
    result = upload(api, tokens, pdf_bytes).json()
    url = "/resumes/" + result["resume"]["id"] + "/search"
    assert api.request("POST", url, json={}, headers=bearer(tokens)).status_code == 409
    other = bearer(register(api, "other@example.com"))
    assert api.request("POST", url, json={}, headers=other).status_code == 404
    assert api.request("POST", url, json={}).status_code == 401


def test_search_concurrency_snapshot_and_conflict(api, ready):
    headers, url = ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: api.request("POST", url, json={}, headers=headers), range(2))
        )
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    job_id = responses[0].json()["id"]
    with api.sessions() as db:
        params = db.get(Job, job_id).params
        assert params["query"]["text"] == "Python Developer"
        assert params["profile"]["skills"] == ["postgresql", "python"]
    assert api.request("POST", url, json={"area": "1"}, headers=headers).status_code == 409
    assert api.request("GET", f"/jobs/{job_id}/results", headers=headers).status_code == 409


def test_search_persistence_pagination_cache_and_duplicate_delivery(api, ready, settings, mocker):
    headers, url = ready
    client = HHClient(settings, HHCache(settings, fakeredis.FakeRedis()))
    mocker.patch("app.services.search.HHClient", return_value=client)
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(
            200, json=page([vacancy("2"), vacancy("1")])
        )
        for _ in range(2):
            job_id = api.request("POST", url, json={}, headers=headers).json()["id"]
            assert process_search(job_id, api.sessions, settings) == "succeeded"
            assert process_search(job_id, api.sessions, settings) == "ignored"
        assert route.call_count == 1
    response = api.request("GET", f"/jobs/{job_id}/results?offset=1&limit=1", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2 and data["ranked"] is False
    assert data["items"][0]["vacancy"]["id"] == "1"
    assert data["items"][0]["score"] is None and data["items"][0]["position"] == 1
    other = bearer(register(api, "other@example.com"))
    assert api.request("GET", f"/jobs/{job_id}/results", headers=other).status_code == 404


def test_failed_second_page_has_no_partial_results(api, ready, settings, mocker):
    headers, url = ready
    job_id = api.request("POST", url, json={"max_pages": 2}, headers=headers).json()["id"]
    mocker.patch("app.services.search.HHClient", return_value=HHClient(settings, use_cache=False))
    with respx.mock as mock:
        mock.get(settings.hh_url + "/vacancies").side_effect = [
            httpx.Response(200, json=page([vacancy()], pages=2)),
            httpx.Response(403),
        ]
        with pytest.raises(SearchProcessingFailed, match="403"):
            process_search(job_id, api.sessions, settings)
    with api.sessions() as db:
        assert db.get(Job, job_id).status == "failed"
        assert list(db.scalars(select(Recommendation))) == []
    assert api.request("POST", url, json={}, headers=headers).json()["id"] != job_id


def test_search_recovery_and_stale_result_guard(api, ready, settings, mocker):
    headers, url = ready
    job_id = api.request("POST", url, json={}, headers=headers).json()["id"]
    with api.sessions() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        db.commit()
    publish = mocker.patch("app.services.jobs.publish_job", return_value=True)
    assert recover_jobs(api.sessions, settings) == 1
    publish.assert_called_once_with(job_id, "search")

    def search(*args, **kwargs):
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
        return [Vacancy.model_validate(vacancy())]

    mocker.patch("app.services.search.HHClient").return_value.search_all.side_effect = search
    assert process_search(job_id, api.sessions, settings) == "ignored"
    with api.sessions() as db:
        assert db.get(Job, job_id).status == "failed"
        assert list(db.scalars(select(Recommendation))) == []


def test_empty_search_succeeds(api, ready, settings, mocker):
    headers, url = ready
    job_id = api.request("POST", url, json={}, headers=headers).json()["id"]
    mocker.patch("app.services.search.HHClient").return_value.search_all.return_value = []
    assert process_search(job_id, api.sessions, settings) == "succeeded"
    result = api.request("GET", f"/jobs/{job_id}/results", headers=headers).json()
    assert result["total"] == 0 and result["items"] == []
