import json

import pytest
import respx
from sqlalchemy import func, select

from app.models import Resume
from app.services.processing import process_resume
from tests.conftest import bearer, register


def upload(api, tokens, data, filename="cv.pdf", mime="application/pdf"):
    return api.request(
        "POST", "/resumes", headers=bearer(tokens), files={"file": (filename, data, mime)}
    )


def test_upload_analysis_and_owner_isolation(api, pdf_bytes, settings, profile_data):
    owner = register(api)
    other = register(api, "other@example.com")
    response = upload(api, owner, pdf_bytes, "../../cv.pdf")
    assert response.status_code == 202, response.text
    record, job = response.json()["resume"], response.json()["job"]
    assert record["filename"] == "cv.pdf"
    assert "path" not in record
    assert record["profile"] is None
    assert job["status"] == "queued"
    assert (settings.upload_dir / f"{record['id']}.pdf").read_bytes() == pdf_bytes
    url = "/resumes/" + record["id"]
    assert api.request("GET", url, headers=bearer(other)).status_code == 404
    assert api.request("POST", url + "/analyze", headers=bearer(other)).status_code == 404
    assert api.request("GET", "/jobs/" + job["id"], headers=bearer(other)).status_code == 404
    assert api.request("GET", url).status_code == 401
    assert api.request("POST", url + "/analyze", headers=bearer(owner)).json()["id"] == job["id"]
    with respx.mock as mock:
        mock.post(settings.ollama_url + "/api/chat").respond(
            200, json={"done": True, "message": {"content": json.dumps(profile_data)}}
        )
        assert process_resume(job["id"], api.sessions, settings) == "succeeded"
    result = api.request("GET", url, headers=bearer(owner))
    assert result.json()["profile"]["skills"] == ["postgresql", "python"]
    assert (
        api.request("GET", "/jobs/" + job["id"], headers=bearer(owner)).json()["status"]
        == "succeeded"
    )


def test_invalid_uploads_do_not_leave_files_or_records(api, settings, pdf_bytes):
    tokens = register(api)
    assert upload(api, tokens, b"invalid").status_code == 422
    assert upload(api, tokens, pdf_bytes, mime="text/plain").status_code == 415
    settings.max_pdf_bytes = 10
    assert upload(api, tokens, pdf_bytes).status_code == 413
    assert not settings.upload_dir.exists()
    with api.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Resume)) == 0


def test_failed_commit_cleans_uploaded_file(api, settings, pdf_bytes, mocker):
    tokens = register(api)
    mocker.patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("failed commit"))
    with pytest.raises(RuntimeError, match="failed commit"):
        upload(api, tokens, pdf_bytes)
    assert list(settings.upload_dir.glob("*.pdf")) == []
