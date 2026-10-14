import hashlib
import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import Settings

logger = logging.getLogger(__name__)
HH_CACHE_TTL = 900


class HHCache:
    def __init__(self, settings: Settings, backend: Redis | None = None):
        self.settings = settings
        self.backend = (
            backend
            if backend is not None
            else Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        )

    def key(self, path: str, params: dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "url": self.settings.hh_url.rstrip("/"),
                "path": path,
                "params": params,
                "user_agent": self.settings.hh_user_agent,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "hh:v1:" + hashlib.sha256(payload.encode()).hexdigest()

    def get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            raw = self.backend.get(self.key(path, params))
            if isinstance(raw, (str, bytes)):
                return json.loads(raw)
        except (RedisError, ValueError, UnicodeError):
            logger.warning("hh.ru cache read unavailable or invalid")
        return None

    def set(self, path: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            self.backend.setex(
                self.key(path, params), HH_CACHE_TTL, json.dumps(payload, ensure_ascii=False)
            )
        except RedisError:
            logger.warning("hh.ru cache write unavailable")
