import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Application, Group, IdentityProvider, Role, User
from app.security.auth import AuthenticatedUser, require_authenticated_user


class TestSession:
    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_override():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async def override():
        async with database.factory() as session:
            yield session
    app.dependency_overrides[get_db] = override
    yield database
    app.dependency_overrides.clear()
    await database.engine.dispose()


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory) -> dict:
    async with factory() as session:
        entra = IdentityProvider(name="Contoso Entra", type="ENTRA", status="CONNECTED", tenant_id="t1")
        ad = IdentityProvider(name="Corp AD", type="ACTIVE_DIRECTORY", status="CONNECTED", tenant_id="DC=corp,DC=local")
        session.add_all([entra, ad])
        await session.flush()
        entra_user = User(provider_id=entra.id, external_id="e1", email="jane@contoso.com", display_name="Jane (Entra)", status="ACTIVE")
        ad_user = User(provider_id=ad.id, external_id="a1", email="john@corp.local", display_name="John (AD)", status="ACTIVE")
        entra_group = Group(provider_id=entra.id, external_id="eg1", name="Entra Group", status="ACTIVE", is_privileged=False)
        ad_group = Group(provider_id=ad.id, external_id="ag1", name="AD Group", status="ACTIVE", is_privileged=False)
        entra_role = Role(provider_id=entra.id, external_id="er1", name="Entra Role", role_type="DIRECTORY", is_privileged=False, status="ACTIVE")
        entra_app = Application(provider_id=entra.id, external_id="ea1", name="Entra App", status="ACTIVE")
        session.add_all([entra_user, ad_user, entra_group, ad_group, entra_role, entra_app])
        await session.commit()
        return {"entra_user_id": entra_user.id, "ad_user_id": ad_user.id, "entra_group_id": entra_group.id, "ad_group_id": ad_group.id, "entra_role_id": entra_role.id, "entra_app_id": entra_app.id}


@pytest.mark.asyncio
async def test_user_list_is_labeled_with_its_real_source_provider(db_override):
    ids = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/users")
    assert response.status_code == 200
    by_id = {u["id"]: u for u in response.json()}
    assert by_id[str(ids["entra_user_id"])]["provider_type"] == "ENTRA"
    assert by_id[str(ids["entra_user_id"])]["provider_name"] == "Contoso Entra"
    assert by_id[str(ids["ad_user_id"])]["provider_type"] == "ACTIVE_DIRECTORY"
    assert by_id[str(ids["ad_user_id"])]["provider_name"] == "Corp AD"


@pytest.mark.asyncio
async def test_user_detail_is_labeled_too(db_override):
    ids = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{ids['ad_user_id']}")
    assert response.status_code == 200
    assert response.json()["provider_type"] == "ACTIVE_DIRECTORY"
    assert response.json()["provider_name"] == "Corp AD"


@pytest.mark.asyncio
async def test_group_list_and_detail_are_labeled(db_override):
    ids = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/groups")
        detail = await client.get(f"/api/v1/groups/{ids['ad_group_id']}")
    by_id = {g["id"]: g for g in listed.json()}
    assert by_id[str(ids["entra_group_id"])]["provider_type"] == "ENTRA"
    assert by_id[str(ids["ad_group_id"])]["provider_type"] == "ACTIVE_DIRECTORY"
    assert detail.json()["provider_type"] == "ACTIVE_DIRECTORY"
    assert detail.json()["provider_name"] == "Corp AD"


@pytest.mark.asyncio
async def test_role_and_application_lists_are_labeled(db_override):
    """Extends the same provider-source labeling to Role/Application lists — used by the item picker in the
    Access Package / Business Role editors (src/App.tsx) to show which directory a candidate Role/Group comes
    from before it's added as an item."""
    ids = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        roles_response = await client.get("/api/v1/roles")
        apps_response = await client.get("/api/v1/applications")
    assert roles_response.status_code == 200 and apps_response.status_code == 200
    by_id = {r["id"]: r for r in roles_response.json()}
    assert by_id[str(ids["entra_role_id"])]["provider_type"] == "ENTRA"
    assert by_id[str(ids["entra_role_id"])]["provider_name"] == "Contoso Entra"
    by_id = {a["id"]: a for a in apps_response.json()}
    assert by_id[str(ids["entra_app_id"])]["provider_type"] == "ENTRA"
    assert by_id[str(ids["entra_app_id"])]["provider_name"] == "Contoso Entra"


@pytest.mark.asyncio
async def test_group_members_list_is_labeled(db_override):
    ids = await _seed(db_override.factory)
    async with db_override.factory() as session:
        from app.models import UserGroup
        session.add(UserGroup(user_id=ids["ad_user_id"], group_id=ids["ad_group_id"], source="SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/groups/{ids['ad_group_id']}/members")
    assert response.status_code == 200
    assert response.json()[0]["provider_type"] == "ACTIVE_DIRECTORY"
