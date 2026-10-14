from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select

from app.models import RefreshToken, User
from app.security import passwords
from tests.conftest import bearer, register


def test_registration_normalizes_email_and_hashes_password(api):
    tokens = register(api, "User@Example.com")
    me = api.request("GET", "/auth/me", headers=bearer(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"
    assert "password" not in me.text
    with api.sessions() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
        assert passwords.verify("safe-password-123", user.password_hash)
    assert (
        api.request(
            "POST",
            "/auth/register",
            json={"email": "USER@example.com", "password": "other-password"},
        ).status_code
        == 409
    )


def test_login_and_password_validation(api):
    register(api)
    for email, password in [
        ("user@example.com", "wrong-password"),
        ("missing@example.com", "safe-password-123"),
    ]:
        result = api.request("POST", "/auth/login", json={"email": email, "password": password})
        assert result.status_code == 401
    result = api.request(
        "POST", "/auth/login", json={"email": "USER@example.com", "password": "safe-password-123"}
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    bad = api.request("POST", "/auth/register", json={"email": "bad", "password": "pw-leak"})
    assert bad.status_code == 422
    assert "pw-leak" not in bad.text
    assert all("input" not in error for error in bad.json()["detail"])


def test_rotation_rejects_replay_and_revokes_old_access(api):
    original = register(api)
    result = api.request("POST", "/auth/refresh", json={"refresh_token": original["refresh_token"]})
    assert result.status_code == 200
    assert result.json()["refresh_token"] != original["refresh_token"]
    assert (
        api.request(
            "POST", "/auth/refresh", json={"refresh_token": original["refresh_token"]}
        ).status_code
        == 401
    )
    assert api.request("GET", "/auth/me", headers=bearer(original)).status_code == 401
    assert api.request("GET", "/auth/me", headers=bearer(result.json())).status_code == 200


def test_logout_revokes_both_tokens_and_is_idempotent(api):
    tokens = register(api)
    for _ in range(2):
        assert (
            api.request(
                "POST", "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
            ).status_code
            == 204
        )
    assert api.request("GET", "/auth/me", headers=bearer(tokens)).status_code == 401
    assert (
        api.request(
            "POST", "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_invalid_expired_and_wrong_type_tokens(api, settings):
    tokens = register(api)
    assert api.request("GET", "/auth/me").status_code == 401
    assert (
        api.request(
            "POST", "/auth/refresh", json={"refresh_token": tokens["access_token"]}
        ).status_code
        == 401
    )
    raw = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    expired = dict(
        raw,
        exp=datetime.now(UTC) - timedelta(minutes=1),
        iat=datetime.now(UTC) - timedelta(minutes=2),
    )
    malformed = dict(raw, sub="not-a-uuid")
    for token in [
        tokens["refresh_token"],
        "invalid",
        tokens["access_token"] + "x",
        jwt.encode(expired, settings.jwt_secret.get_secret_value(), algorithm="HS256"),
        jwt.encode(malformed, settings.jwt_secret.get_secret_value(), algorithm="HS256"),
    ]:
        assert (
            api.request("GET", "/auth/me", headers={"Authorization": "Bearer " + token}).status_code
            == 401
        )


def test_concurrent_refresh_consumes_token_once(api):
    tokens = register(api)

    def refresh():
        return api.request("POST", "/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: refresh(), range(2)))
    assert sorted(result.status_code for result in results) == [200, 401]
    with api.sessions() as db:
        assert (
            len(list(db.scalars(select(RefreshToken).where(RefreshToken.revoked.is_(False))))) == 1
        )
