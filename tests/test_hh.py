import httpx
import pytest
import respx
from pydantic import ValidationError

from app.services.hh import (
    HHClient,
    HHInvalidResponse,
    HHNotFound,
    HHRequestRejected,
    HHSearch,
    HHUnavailable,
)


def vacancy(id="1"):
    return {
        "id": id,
        "name": "Python Developer",
        "employer": {"name": "Example"},
        "alternate_url": "https://hh.ru/vacancy/" + id,
        "salary": None,
    }


def page(items, number=0, pages=1):
    return {"items": items, "found": 10, "page": number, "pages": pages, "per_page": 20}


def test_search_headers_filters_and_nullable_fields(settings):
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(200, json=page([vacancy()]))
        result = HHClient(settings).search(HHSearch(text="Python", area="1", salary=100000))
    request = route.calls[0].request
    assert request.headers["HH-User-Agent"] == settings.hh_user_agent
    assert request.url.params["area"] == "1"
    assert request.url.params["salary"] == "100000"
    assert result.items[0].salary is None
    assert result.items[0].key_skills is None


def test_pagination_deduplicates_and_stops(settings):
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies")
        route.side_effect = [
            httpx.Response(200, json=page([vacancy("1")], 0, 2)),
            httpx.Response(200, json=page([vacancy("1"), vacancy("2")], 1, 2)),
        ]
        result = HHClient(settings).search_all(HHSearch(text="Python"), max_pages=3)
        assert [item.id for item in result] == ["1", "2"]
        assert route.call_count == 2
        assert route.calls[1].request.url.params["page"] == "1"


def test_details_and_no_retry_on_missing_or_forbidden(settings):
    with respx.mock as mock:
        detail = dict(vacancy(), key_skills=[{"name": "Python"}], description="Backend")
        route = mock.get(settings.hh_url + "/vacancies/1").respond(200, json=detail)
        assert HHClient(settings).vacancy("1").key_skills[0].name == "Python"
        route.respond(404)
        with pytest.raises(HHNotFound):
            HHClient(settings).vacancy("1")
        route.respond(403)
        with pytest.raises(HHRequestRejected):
            HHClient(settings).vacancy("1")
        assert route.call_count == 3


def test_retries_rate_limit_and_server_error(settings):
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(503, headers={"Retry-After": "Thu, 01 Jan 1970 00:00:00 GMT"}),
            httpx.Response(200, json=page([])),
        ]
        assert HHClient(settings).search(HHSearch(text="Python")).items == []
        assert route.call_count == 3
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies")
        route.side_effect = httpx.ReadTimeout("upstream timeout")
        with pytest.raises(HHUnavailable):
            HHClient(settings).search(HHSearch(text="Python"))
        assert route.call_count == 3


def test_invalid_response_is_not_retried(settings):
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(200, json={"bad": "data"})
        with pytest.raises(HHInvalidResponse):
            HHClient(settings).search(HHSearch(text="Python"))
        assert route.call_count == 1


def test_search_limits_and_path_validation(settings):
    with pytest.raises(ValidationError):
        HHSearch(text="Python", per_page=101)
    with respx.mock:
        with pytest.raises(ValueError, match="2000"):
            HHClient(settings).search(HHSearch(text="Python", page=20, per_page=100))
        with pytest.raises(ValueError, match="2000"):
            HHClient(settings).search_all(HHSearch(text="Python", page=20, per_page=100))
        with pytest.raises(ValueError):
            HHClient(settings).vacancy("../me")
