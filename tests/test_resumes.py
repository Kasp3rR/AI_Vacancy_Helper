import json
from pathlib import Path

import pytest
import respx
from sqlalchemy import func, select

from app.models import Resume
from tests.conftest import bearer, register


def upload(api, tokens, data, filename="cv.pdf", mime="application/pdf"):
    return api.request(
        "POST", "/resumes", headers=bearer(tokens), files={"file": (filename, data, mime)}
    )


def test_upload_analysis_and_owner_isolation(api, pdf_bytes, settings, profile_data):
    owner = register(api)
    other = register(api, "other@example.com")
    response = upload(api, owner, pdf_bytes, "../../cv.pdf")
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["filename"] == "cv.pdf"
    assert "path" not in record
    assert record["profile"] is None
    assert (settings.upload_dir / f"{record['id']}.pdf").read_bytes() == pdf_bytes
    url = "/resumes/" + record["id"]
    assert api.request("GET", url, headers=bearer(other)).status_code == 404
    assert api.request("POST", url + "/analyze", headers=bearer(other)).status_code == 404
    assert api.request("GET", url).status_code == 401
    with respx.mock as mock:
        mock.post(settings.ollama_url + "/api/chat").respond(
            200, json={"done": True, "message": {"content": json.dumps(profile_data)}}
        )
        result = api.request("POST", url + "/analyze", headers=bearer(owner))
    assert result.status_code == 200, result.text
    assert result.json()["profile"]["skills"] == ["postgresql", "python"]
    assert api.request("GET", url, headers=bearer(owner)).json() == result.json()


def test_invalid_uploads_do_not_leave_files_or_records(api, settings, pdf_bytes):
    tokens = register(api)
    assert upload(api, tokens, b"invalid").status_code == 422
    assert upload(api, tokens, pdf_bytes, mime="text/plain").status_code == 415
    settings.max_pdf_bytes = 10
    assert upload(api, tokens, pdf_bytes).status_code == 413
    assert not settings.upload_dir.exists()
    with api.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Resume)) == 0


def test_analysis_errors_preserve_profile_and_failed_commit_cleans_file(
    api,
    settings,
    pdf_bytes,
    mocker,
    profile_data,
):
    tokens = register(api)
    record = upload(api, tokens, pdf_bytes).json()
    url = "/resumes/" + record["id"]
    for code, payload, expected in [(404, {}, 503), (200, {"done": True}, 502)]:
        with respx.mock as mock:
            mock.post(settings.ollama_url + "/api/chat").respond(code, json=payload)
            response = api.request("POST", url + "/analyze", headers=bearer(tokens))
            assert response.status_code == expected
        assert api.request("GET", url, headers=bearer(tokens)).json()["profile"] is None
    with api.sessions() as db:
        row = db.get(Resume, record["id"])
        stored_path = Path(row.path)
    stored_path.unlink()
    assert api.request("POST", url + "/analyze", headers=bearer(tokens)).status_code == 503
    mocker.patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("failed commit"))
    with pytest.raises(RuntimeError, match="failed commit"):
        upload(api, tokens, pdf_bytes)
    assert list(settings.upload_dir.glob("*.pdf")) == []
