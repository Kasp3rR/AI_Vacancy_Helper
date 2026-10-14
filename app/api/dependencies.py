from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import RefreshToken, User
from app.security import decode_token, unauthorized

Db = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
bearer = HTTPBearer(auto_error=False)


def current_user(
    db: Db,
    settings: Config,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise unauthorized()
    claims = decode_token(credentials.credentials, "access", settings)
    user = db.scalar(
        select(User)
        .join(RefreshToken, RefreshToken.user_id == User.id)
        .where(
            User.id == str(claims.sub),
            RefreshToken.jti == str(claims.sid),
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    if user is None:
        raise unauthorized()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
