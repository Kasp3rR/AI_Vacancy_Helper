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
        if claims.type != kind:
            raise jwt.InvalidTokenError()
        if kind == "refresh" and claims.sid != claims.jti:
            raise jwt.InvalidTokenError()
        return claims
    except (jwt.InvalidTokenError, ValidationError) as exc:
        raise unauthorized() from exc


def encode_token(
    user_id: str,
    token_id: str,
    session_id: str,
    kind: str,
    issued_at: datetime,
    expires_at: datetime,
    settings: Settings,
) -> str:
    payload = {
        "sub": user_id,
        "jti": token_id,
        "sid": session_id,
        "type": kind,
        "iat": issued_at,
        "exp": expires_at,
        "iss": "ai-vacancy-helper",
        "aud": "ai-vacancy-helper",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def issue_tokens(db: Session, user: User, settings: Settings) -> TokenPair:
    now = datetime.now(UTC)
    session_id = new_id()
    access_expiry = now + timedelta(minutes=settings.access_minutes)
    refresh_expiry = now + timedelta(days=settings.refresh_days)

    refresh_record = RefreshToken(jti=session_id, user_id=user.id, expires_at=refresh_expiry)
    db.add(refresh_record)
    access_token = encode_token(
        user_id=user.id,
        token_id=new_id(),
        session_id=session_id,
        kind="access",
        issued_at=now,
        expires_at=access_expiry,
        settings=settings,
    )
    refresh_token = encode_token(
        user_id=user.id,
        token_id=session_id,
        session_id=session_id,
        kind="refresh",
        issued_at=now,
        expires_at=refresh_expiry,
        settings=settings,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_minutes * 60,
    )
