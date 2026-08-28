import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

os.environ["ENTRA_TENANT_ID"] = "tenant-test"
os.environ["ENTRA_API_CLIENT_ID"] = "api-test"
os.environ["ENTRA_API_AUDIENCE"] = "api-test"
os.environ["ENTRA_AUTHORITY"] = "https://login.microsoftonline.com/tenant-test"
os.environ["ENTRA_TOKEN_ISSUER"] = "https://sts.windows.net/tenant-test/"

from app.core.config import get_settings
from app.main import app
from app.security import auth

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

class FakeJWKClient:
    def __init__(self, url: str): self.url = url
    def get_signing_key_from_jwt(self, token: str): return type("SigningKey", (), {"key": key.public_key()})()

def token(**overrides):
    claims = {"sub": "user-1", "name": "Jordan Lee", "preferred_username": "jordan@example.com", "tid": "tenant-test", "aud": "api-test", "iss": "https://sts.windows.net/tenant-test/", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "roles": ["AccessPilot.User"]}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")

@pytest.fixture(autouse=True)
def configure(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setattr(auth, "PyJWKClient", FakeJWKClient)

@pytest.mark.asyncio
async def test_valid_user_token() -> None:
    user = await auth.decode_access_token(token())
    assert user.subject == "user-1"
    assert user.roles == ("AccessPilot.User",)

@pytest.mark.asyncio
async def test_invalid_signature() -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(auth.AccessPilotError) as error:
        await auth.decode_access_token(jwt.encode({"sub":"user-1","tid":"tenant-test","aud":"api-test","iss":"https://sts.windows.net/tenant-test/","exp":datetime.now(timezone.utc)+timedelta(minutes=5),"roles":["AccessPilot.User"]}, other, algorithm="RS256"))
    assert error.value.code == "INVALID_TOKEN"

@pytest.mark.asyncio
async def test_expired_wrong_audience_issuer_tenant_and_missing_role() -> None:
    cases = [({"exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, "TOKEN_EXPIRED"), ({"aud": "other"}, "INVALID_AUDIENCE"), ({"iss": "https://sts.windows.net/other/"}, "INVALID_ISSUER"), ({"tid": "other"}, "INVALID_TENANT"), ({"roles": []}, "INVALID_TOKEN")]
    for changes, expected in cases:
        with pytest.raises(auth.AccessPilotError) as error: await auth.decode_access_token(token(**changes))
        assert error.value.code == expected

@pytest.mark.asyncio
async def test_missing_token_is_401_and_user_admin_endpoint_is_403() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/v1/me")
        user_admin = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {token()}"})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert user_admin.status_code == 403
    assert user_admin.json()["error"]["code"] == "ACCESS_DENIED"

@pytest.mark.asyncio
async def test_admin_access_and_me() -> None:
    admin_token = token(roles=["AccessPilot.Admin"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {admin_token}"})
        users = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert me.status_code == 200 and me.json()["roles"] == ["AccessPilot.Admin"]
    assert users.status_code == 200
