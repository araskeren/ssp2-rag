import time
import uuid

import jwt
import pytest

from ai_service.auth.apikey import match_api_key
from ai_service.auth.jwt import JwtError, verify_hs256

SECRET = "rahasia-tes"


def _token(**overrides):
    now = int(time.time())
    payload = {
        "sub": 42,
        "email": "a@b.id",
        "roleId": 4,
        "sessionId": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60,
    }
    payload.update(overrides)
    return jwt.encode(
        payload, overrides.pop("_secret", SECRET), algorithm=overrides.pop("_alg", "HS256")
    )


def test_valid_numeric_sub_normalized():
    claims = verify_hs256(_token(), SECRET)
    assert claims.sub == "42" and claims.role_id == 4 and claims.email == "a@b.id"


def test_string_sub_ok():
    assert verify_hs256(_token(sub="42"), SECRET).sub == "42"


def test_rejects_temp_token():
    with pytest.raises(JwtError, match="sementara"):
        verify_hs256(_token(isTemp=True), SECRET)


def test_rejects_missing_session():
    with pytest.raises(JwtError, match="sessionId"):
        verify_hs256(_token(sessionId=None), SECRET)


def test_rejects_expired():
    with pytest.raises(JwtError, match="kedaluwarsa"):
        verify_hs256(_token(exp=int(time.time()) - 5), SECRET)


def test_rejects_wrong_secret_and_alg():
    with pytest.raises(JwtError):
        verify_hs256(_token(), "salah")
    now = int(time.time())
    hs384 = jwt.encode({"sub": 1, "sessionId": "x", "exp": now + 60}, SECRET, algorithm="HS384")
    with pytest.raises(JwtError):
        verify_hs256(hs384, SECRET)
    with pytest.raises(JwtError):
        verify_hs256("bukan.token.jwt", SECRET)


def test_api_key_matching():
    assert match_api_key("k2", ["k1", "k2"]) is True
    assert match_api_key(" k2 ", ["k1", "k2"]) is True
    assert match_api_key("k3", ["k1", "k2"]) is False
    assert match_api_key(None, ["k1"]) is False
    assert match_api_key("", []) is False
