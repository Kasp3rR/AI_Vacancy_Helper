from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import Config, CurrentUser, Db
from app.models import RefreshToken, User
from app.schemas import Credentials, RefreshRequest, TokenPair, UserResponse
from app.security import DUMMY_HASH, decode_token, issue_tokens, passwords, unauthorized

router = APIRouter(prefix="/auth", tags=["auth"])


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/register", response_model=TokenPair, status_code=201)
def register(data: Credentials, db: Db, settings: Config, response: Response) -> TokenPair:
    user = User(
        email=str(data.email), password_hash=passwords.hash(data.password.get_secret_value())
    )
    db.add(user)
    try:
        db.flush()
        tokens = issue_tokens(db, user, settings)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Email already registered") from exc
    no_store(response)
    return tokens


@router.post("/login", response_model=TokenPair)
def login(data: Credentials, db: Db, settings: Config, response: Response) -> TokenPair:
    user = db.scalar(select(User).where(User.email == str(data.email)))
    password_hash = DUMMY_HASH
    if user is not None:
        password_hash = user.password_hash
    verified = passwords.verify(data.password.get_secret_value(), password_hash)
    if not verified or user is None:
        raise unauthorized()
    tokens = issue_tokens(db, user, settings)
    db.commit()
    no_store(response)
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(data: RefreshRequest, db: Db, settings: Config, response: Response) -> TokenPair:
    claims = decode_token(data.refresh_token, "refresh", settings)
    user_id = db.scalar(
        update(RefreshToken)
        .where(
            RefreshToken.jti == str(claims.jti),
            RefreshToken.user_id == str(claims.sub),
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        .values(revoked=True)
        .returning(RefreshToken.user_id)
    )
    if user_id is None:
        db.rollback()
        raise unauthorized()
    user = db.get(User, user_id)
    if user is None:
        db.rollback()
        raise unauthorized()
    tokens = issue_tokens(db, user, settings)
    db.commit()
    no_store(response)
    return tokens


@router.post("/logout", status_code=204)
def logout(data: RefreshRequest, db: Db, settings: Config) -> Response:
    claims = decode_token(data.refresh_token, "refresh", settings)
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.jti == str(claims.jti),
            RefreshToken.user_id == str(claims.sub),
        )
        .values(revoked=True)
    )
    db.commit()
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    return user
