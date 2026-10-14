from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import RefreshToken, User, new_id
from app.schemas import TokenPair

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("dummy-hash-used-only-for-missing-users")


class Claims(BaseModel):
    sub: UUID
    jti: UUID
    sid: UUID
    type: Literal["access", "refresh"]
    exp: int
    iat: int


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
    )


def decode_token(token: str, kind: str, settings: Settings) -> Claims:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="ai-vacancy-helper",
            audience="ai-vacancy-helper",
            options={"require": ["exp", "iat", "sub", "jti", "sid", "type", "iss", "aud"]},
        )
        claims = Claims.model_validate(data)
        if claims.type != kind or (kind == "refresh" and claims.sid != claims.jti):
            raise jwt.InvalidTokenError()
        return claims
    except (jwt.InvalidTokenError, ValidationError) as exc:
        raise unauthorized() from exc


def issue_tokens(db: Session, user: User, settings: Settings) -> TokenPair:
    now = datetime.now(UTC)
    session_id = new_id()
    refresh_expiry = now + timedelta(days=settings.refresh_days)
    db.add(RefreshToken(jti=session_id, user_id=user.id, expires_at=refresh_expiry))
    tokens = {}
    for kind, jti, expiry in (
        ("access", new_id(), now + timedelta(minutes=settings.access_minutes)),
        ("refresh", session_id, refresh_expiry),
    ):
        tokens[kind] = jwt.encode(
            {
                "sub": user.id,
                "jti": jti,
                "sid": session_id,
                "type": kind,
                "iat": now,
                "exp": expiry,
                "iss": "ai-vacancy-helper",
                "aud": "ai-vacancy-helper",
            },
            settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
    return TokenPair(
        access_token=tokens["access"],
        refresh_token=tokens["refresh"],
        expires_in=settings.access_minutes * 60,
    )
