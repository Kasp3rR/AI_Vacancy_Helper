import fakeredis
import pytest
import respx
from redis.exceptions import ConnectionError

from app.services.hh import HHClient, HHInvalidResponse, HHRequestRejected, HHSearch
from app.services.hh_cache import HHCache
from tests.test_hh import page, vacancy


def test_cache_hit_ttl_expiry_and_salary(settings):
    backend = fakeredis.FakeRedis(decode_responses=True)
    cache = HHCache(settings, backend)
    client = HHClient(settings, cache)
    query = HHSearch(text="Python")
    key = cache.key("/vacancies", query.model_dump(exclude_none=True))
    item = vacancy()
    item["salary"] = {"from": 100000, "to": 200000, "currency": "RUR"}
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(200, json=page([item]))
        first = client.search(query)
        assert 899 <= backend.ttl(key) <= 900
        backend.expire(key, 100)
        assert client.search(query) == first
        assert first.items[0].salary.lower == 100000
        assert backend.ttl(key) <= 100
        assert route.call_count == 1
        backend.expire(key, 0)
        client.search(query)
        assert route.call_count == 2


def test_cache_keys_are_canonical_and_separate_filters(settings):
    cache = HHCache(settings, fakeredis.FakeRedis())
    assert cache.key("/vacancies", {"text": "Python", "page": 0}) == cache.key(
        "/vacancies", {"page": 0, "text": "Python"}
    )
    keys = {
        cache.key("/vacancies", HHSearch(**kwargs).model_dump(exclude_none=True))
        for kwargs in [
            {"text": "Python"},
            {"text": "Java"},
            {"text": "Python", "page": 1},
            {"text": "Python", "area": "1"},
        ]
    }
    assert len(keys) == 4
    assert cache.key("/vacancies/1", {}) != cache.key("/vacancies/2", {})


@pytest.mark.parametrize("corrupt", ["broken json", "{}", '{"items": []}'])
def test_invalid_cache_is_replaced(settings, corrupt):
    backend = fakeredis.FakeRedis(decode_responses=True)
    cache = HHCache(settings, backend)
    query = HHSearch(text="Python")
    key = cache.key("/vacancies", query.model_dump(exclude_none=True))
    backend.set(key, corrupt)
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(200, json=page([]))
        assert HHClient(settings, cache).search(query).items == []
        assert route.call_count == 1
    assert 899 <= backend.ttl(key) <= 900


def test_cache_failure_falls_back_to_http(settings, mocker):
    backend = fakeredis.FakeRedis()
    mocker.patch.object(backend, "get", side_effect=ConnectionError())
    mocker.patch.object(backend, "setex", side_effect=ConnectionError())
    with respx.mock as mock:
        mock.get(settings.hh_url + "/vacancies").respond(200, json=page([]))
        assert (
            HHClient(settings, HHCache(settings, backend)).search(HHSearch(text="Python")).items
            == []
        )


def test_errors_are_never_cached(settings):
    backend = fakeredis.FakeRedis()
    client = HHClient(settings, HHCache(settings, backend))
    with respx.mock as mock:
        route = mock.get(settings.hh_url + "/vacancies").respond(403)
        with pytest.raises(HHRequestRejected):
            client.search(HHSearch(text="Python"))
        route.respond(200, json={"invalid": True})
        with pytest.raises(HHInvalidResponse):
            client.search(HHSearch(text="Python"))
    assert backend.dbsize() == 0
