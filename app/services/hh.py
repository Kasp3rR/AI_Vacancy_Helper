import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, HttpUrl, ValidationError
from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt

from app.config import Settings
from app.services.hh_cache import HHCache


class HHError(Exception):
    pass


class HHNotFound(HHError):
    pass


class HHUnavailable(HHError):
    pass


class HHInvalidResponse(HHError):
    pass


class HHRequestRejected(HHError):
    pass


class HHSearch(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    area: str | None = Field(default=None, pattern=r"^\d+$")
    salary: int | None = Field(default=None, ge=0)
    currency: Literal["RUR", "USD", "EUR"] = "RUR"
    experience: Literal["noExperience", "between1And3", "between3And6", "moreThan6"] | None = None
    page: int = Field(default=0, ge=0, le=1999)
    per_page: int = Field(default=20, ge=1, le=100)


class NamedEntity(BaseModel):
    id: str | None = None
    name: str


class Salary(BaseModel):
    lower: int | None = Field(default=None, alias="from")
    upper: int | None = Field(default=None, alias="to")
    currency: str
    gross: bool | None = None


class Skill(BaseModel):
    name: str


class Vacancy(BaseModel):
    id: str
    name: str
    employer: NamedEntity | None = None
    salary: Salary | None = None
    alternate_url: HttpUrl
    area: NamedEntity | None = None
    experience: NamedEntity | None = None
    snippet: dict[str, str | None] | None = None
    description: str | None = None
    key_skills: list[Skill] | None = None


class SearchPage(BaseModel):
    items: list[Vacancy]
    found: int = Field(ge=0)
    pages: int = Field(ge=0)
    page: int = Field(ge=0)
    per_page: int = Field(ge=1, le=100)


def transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def retry_delay(state: RetryCallState) -> float:
    exc = None
    if state.outcome is not None:
        exc = state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError):
        value = exc.response.headers.get("Retry-After")
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = 0
            return max(0, min(seconds, 10))
    return float(min(0.5 * 2 ** (state.attempt_number - 1), 2))


@retry(
    retry=retry_if_exception(transient), stop=stop_after_attempt(3), wait=retry_delay, reraise=True
)
def get_response(client: httpx.Client, path: str, params: dict[str, Any]) -> httpx.Response:
    response = client.get(path, params=params)
    response.raise_for_status()
    return response


class HHClient:
    def __init__(self, settings: Settings, cache: HHCache | None = None, *, use_cache: bool = True):
        self.settings = settings
        self.cache: HHCache | None = None
        if use_cache:
            if cache is None:
                cache = HHCache(settings)
            self.cache = cache

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            with httpx.Client(
                base_url=self.settings.hh_url,
                headers={
                    "HH-User-Agent": self.settings.hh_user_agent,
                    "User-Agent": self.settings.hh_user_agent,
                },
                timeout=httpx.Timeout(self.settings.hh_timeout_seconds, connect=5),
                trust_env=False,
            ) as client:
                response = get_response(client, path, params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HHNotFound("Vacancy not found") from None
            if exc.response.status_code == 429 or exc.response.status_code >= 500:
                raise HHUnavailable("hh.ru is temporarily unavailable") from None
            raise HHRequestRejected(
                f"hh.ru rejected request ({exc.response.status_code})"
            ) from None
        except httpx.HTTPError:
            raise HHUnavailable("hh.ru is temporarily unavailable") from None
        try:
            return response.json()
        except ValueError:
            raise HHInvalidResponse("hh.ru returned invalid JSON") from None

    def search(self, query: HHSearch) -> SearchPage:
        if (query.page + 1) * query.per_page > 2000:
            raise ValueError("hh.ru search depth cannot exceed 2000 results")
        params = query.model_dump(exclude_none=True)
        if self.cache is not None:
            cached = self.cache.get("/vacancies", params)
            if cached is not None:
                try:
                    result = SearchPage.model_validate(cached)
                    if result.page == query.page and result.per_page == query.per_page:
                        return result
                except ValidationError:
                    pass
        try:
            result = SearchPage.model_validate(self._get("/vacancies", params))
            if result.page != query.page or result.per_page != query.per_page:
                raise HHInvalidResponse("hh.ru returned an unexpected page")
        except ValidationError:
            raise HHInvalidResponse("hh.ru returned an invalid search response") from None
        if self.cache is not None:
            self.cache.set("/vacancies", params, result.model_dump(mode="json", by_alias=True))
        return result

    def search_all(self, query: HHSearch, max_pages: int = 3) -> list[Vacancy]:
        if not 1 <= max_pages <= 20:
            raise ValueError("max_pages must be between 1 and 20")
        if (query.page + 1) * query.per_page > 2000:
            raise ValueError("hh.ru search depth cannot exceed 2000 results")
        result: dict[str, Vacancy] = {}
        last_page = min(query.page + max_pages, 2000 // query.per_page)
        for page in range(query.page, last_page):
            page_query = query.model_copy(update={"page": page})
            data = self.search(page_query)
            for vacancy in data.items:
                if vacancy.id not in result:
                    result[vacancy.id] = vacancy
            if not data.items or page + 1 >= data.pages:
                break
        return list(result.values())

    def vacancy(self, vacancy_id: str) -> Vacancy:
        if not re.fullmatch(r"[0-9]+", vacancy_id):
            raise ValueError("Vacancy ID must contain only digits")
        path = f"/vacancies/{vacancy_id}"
        if self.cache is not None:
            cached = self.cache.get(path, {})
            if cached is not None:
                try:
                    result = Vacancy.model_validate(cached)
                    if result.id == vacancy_id:
                        return result
                except ValidationError:
                    pass
        try:
            result = Vacancy.model_validate(self._get(path, {}))
            if result.id != vacancy_id:
                raise HHInvalidResponse("hh.ru returned an unexpected vacancy")
        except ValidationError:
            raise HHInvalidResponse("hh.ru returned an invalid vacancy") from None
        if self.cache is not None:
            self.cache.set(path, {}, result.model_dump(mode="json", by_alias=True))
        return result
