import json

import httpx
import pytest
import respx

from app.schemas import Profile
from app.services.ollama import InvalidProfile, OllamaTimeout, OllamaUnavailable, extract_profile


def test_ollama_structured_profile(settings, profile_data):
    with respx.mock as mock:
        route = mock.post(settings.ollama_url + "/api/chat").respond(
            200, json={"done": True, "message": {"content": json.dumps(profile_data)}}
        )
        result = extract_profile("Python Developer", settings)
    assert result.skills == ["postgresql", "python"]
    request = json.loads(route.calls[0].request.content)
    assert request["format"] == Profile.model_json_schema()
    assert request["model"] == "qwen3:4b-instruct"
    assert request["stream"] is False


def test_ollama_rejects_invalid_and_incomplete_output(settings, profile_data):
    for payload in [
        {"done": True, "message": {"content": "not JSON"}},
        {"done": False, "message": {"content": json.dumps(profile_data)}},
        {"done": True, "done_reason": "length", "message": {"content": json.dumps(profile_data)}},
        {"done": True, "message": {"content": json.dumps(dict(profile_data, unexpected=True))}},
        {"done": True, "message": {"content": json.dumps(dict(profile_data, experience_years=-1))}},
        {"done": True, "message": {"content": json.dumps(dict(profile_data, skills="Python"))}},
        [],
    ]:
        with respx.mock as mock:
            route = mock.post(settings.ollama_url + "/api/chat").respond(200, json=payload)
            with pytest.raises(InvalidProfile):
                extract_profile("Resume text", settings)
            assert route.call_count == 1


def test_ollama_retries_transient_errors(settings, profile_data):
    with respx.mock as mock:
        route = mock.post(settings.ollama_url + "/api/chat")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(
                200, json={"done": True, "message": {"content": json.dumps(profile_data)}}
            ),
        ]
        assert extract_profile("Resume text", settings).title
        assert route.call_count == 2


def test_ollama_missing_model_is_unavailable_without_retry(settings):
    with respx.mock as mock:
        route = mock.post(settings.ollama_url + "/api/chat").respond(
            404, json={"error": "not found"}
        )
        with pytest.raises(OllamaUnavailable):
            extract_profile("Resume text", settings)
        assert route.call_count == 1


def test_ollama_timeout_is_not_retried(settings):
    with respx.mock as mock:
        route = mock.post(settings.ollama_url + "/api/chat")
        route.side_effect = httpx.ReadTimeout("timeout")
        with pytest.raises(OllamaTimeout):
            extract_profile("Resume text", settings)
        assert route.call_count == 1


def test_ollama_rejects_empty_or_oversized_input(settings):
    with respx.mock:
        for text in [" ", "x" * (settings.max_text_chars + 1)]:
            with pytest.raises(InvalidProfile):
                extract_profile(text, settings)
