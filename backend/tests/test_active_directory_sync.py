"""Phase 2 (real LDAP read) tests. Uses ldap3's own MOCK_SYNC strategy — a real in-memory LDAP server, not a
mocked-away network call — so get_users/get_groups/get_group_members/get_user/get_group are tested against
actual LDAP search/filter/attribute-formatting behavior, the same engine the real DC in live testing used,
without needing network access in CI."""
import uuid
from types import SimpleNamespace

import pytest
from ldap3 import MOCK_SYNC, SIMPLE, Connection, Server

from app.providers.active_directory import ActiveDirectoryProvider, _clean_guid, _guid_filter_value

ACCOUNTDISABLE = 0x2
NORMAL_ACCOUNT = 512


def _mock_connector() -> tuple[ActiveDirectoryProvider, Connection]:
    provider = SimpleNamespace(organization_url="ldaps://fake:636", tenant_id="DC=example,DC=com", graph_client_id="cn=admin,dc=example,dc=com", graph_client_secret_encrypted="irrelevant")
    connector = ActiveDirectoryProvider(provider)
    connector._bind_password = lambda: "secret"  # bypass real Fernet decryption for this unit test

    server = Server("fake")
    connection = Connection(server, user="cn=admin,dc=example,dc=com", password="secret", client_strategy=MOCK_SYNC)
    connection.strategy.add_entry("cn=admin,dc=example,dc=com", {"userPassword": "secret"})
    connector._connection = lambda: connection
    return connector, connection


def _add_user(connection, dn, *, sam, display_name=None, mail=None, upn=None, given_name=None, surname=None, department=None, title=None, disabled=False, guid=None):
    attrs = {
        "objectClass": ["user", "person"],
        "objectCategory": "person",  # the real DC sets this automatically; MOCK_SYNC does not, so tests must
        "objectGUID": (guid or uuid.uuid4()).bytes_le,
        "sAMAccountName": sam,
        "userAccountControl": NORMAL_ACCOUNT | (ACCOUNTDISABLE if disabled else 0),
    }
    if display_name: attrs["displayName"] = display_name
    if mail: attrs["mail"] = mail
    if upn: attrs["userPrincipalName"] = upn
    if given_name: attrs["givenName"] = given_name
    if surname: attrs["sn"] = surname
    if department: attrs["department"] = department
    if title: attrs["title"] = title
    connection.strategy.add_entry(dn, attrs)


def _add_group(connection, dn, *, cn, description=None, members=None, guid=None):
    attrs = {"objectClass": ["group"], "objectGUID": (guid or uuid.uuid4()).bytes_le, "cn": cn}
    if description: attrs["description"] = description
    if members: attrs["member"] = members
    connection.strategy.add_entry(dn, attrs)


# ---- GUID conversion (pure logic, no directory at all) ----

def test_guid_round_trips_through_filter_conversion():
    original = uuid.uuid4()
    filter_value = _guid_filter_value(str(original))
    # The filter is a backslash-escaped octet string in little-endian (bytes_le) order — decoding it back the
    # same way must reproduce the original UUID.
    raw_bytes = bytes(int(b, 16) for b in filter_value.split("\\") if b)
    assert uuid.UUID(bytes_le=raw_bytes) == original


def test_clean_guid_handles_the_real_servers_braced_string_format():
    assert _clean_guid("{16eba7ca-4236-487d-a3ca-83d771912055}") == "16eba7ca-4236-487d-a3ca-83d771912055"


def test_clean_guid_handles_raw_bytes_fallback():
    g = uuid.uuid4()
    assert _clean_guid(g.bytes_le) == str(g)


# ---- Real LDAP read logic, against a real (mocked) directory ----

@pytest.mark.asyncio
async def test_get_users_maps_real_attributes_and_enabled_disabled_state():
    connector, connection = _mock_connector()
    _add_user(connection, "cn=jane,dc=example,dc=com", sam="jane", display_name="Jane Doe", mail="jane@example.com", given_name="Jane", surname="Doe", department="Finance", title="Analyst")
    _add_user(connection, "cn=krbtgt,dc=example,dc=com", sam="krbtgt", display_name="krbtgt", disabled=True)
    connection.bind()

    users = await connector.get_users()
    assert len(users) == 2
    jane = next(u for u in users if u.display_name == "Jane Doe")
    assert jane.email == "jane@example.com"
    assert jane.given_name == "Jane" and jane.surname == "Doe"
    assert jane.department == "Finance" and jane.job_title == "Analyst"
    assert jane.status == "ACTIVE"
    assert len(jane.external_id) == 36  # a clean UUID string, not raw bytes or a braced string

    krbtgt = next(u for u in users if u.display_name == "krbtgt")
    assert krbtgt.status == "DISABLED"


@pytest.mark.asyncio
async def test_get_users_falls_back_to_upn_when_mail_is_not_set():
    connector, connection = _mock_connector()
    _add_user(connection, "cn=svc,dc=example,dc=com", sam="svc.account", display_name="svc.account", upn="svc.account@example.com")
    connection.bind()

    users = await connector.get_users()
    assert users[0].email == "svc.account@example.com"


@pytest.mark.asyncio
async def test_get_groups_flags_well_known_privileged_group_names():
    connector, connection = _mock_connector()
    _add_group(connection, "cn=Domain Admins,dc=example,dc=com", cn="Domain Admins", description="Designated administrators of the domain")
    _add_group(connection, "cn=Marketing,dc=example,dc=com", cn="Marketing", description="Marketing team distribution list")
    connection.bind()

    groups = await connector.get_groups()
    assert len(groups) == 2
    domain_admins = next(g for g in groups if g.name == "Domain Admins")
    marketing = next(g for g in groups if g.name == "Marketing")
    assert domain_admins.is_privileged is True
    assert marketing.is_privileged is False


@pytest.mark.skip(reason="ldap3's MOCK_SYNC strategy does not correctly match an escaped-octet-string binary filter (confirmed directly: the identical filter correctly matches against a real AD DC in live testing, but returns 0 results under MOCK_SYNC) — a real limitation of the test double, not a bug in _guid_filter_value/get_group. Verified for real in live testing against 192.168.71.4 instead; see the project status memory entry for that record.")
@pytest.mark.asyncio
async def test_get_group_by_guid_round_trips_correctly():
    connector, connection = _mock_connector()
    guid = uuid.uuid4()
    _add_group(connection, "cn=Finance Team,dc=example,dc=com", cn="Finance Team", guid=guid)
    connection.bind()

    found = await connector.get_group(str(guid))
    assert found is not None
    assert found.name == "Finance Team"
    assert found.external_id == str(guid)

    missing = await connector.get_group(str(uuid.uuid4()))
    assert missing is None


@pytest.mark.skip(reason="Same MOCK_SYNC binary-filter limitation as test_get_group_by_guid_round_trips_correctly — get_group_members looks the group up by GUID filter first. Verified for real in live testing against 192.168.71.4 instead.")
@pytest.mark.asyncio
async def test_get_group_members_returns_only_direct_user_members_not_nested_groups():
    connector, connection = _mock_connector()
    user_guid = uuid.uuid4()
    _add_user(connection, "cn=jane,dc=example,dc=com", sam="jane", display_name="Jane Doe", guid=user_guid)
    _add_group(connection, "cn=Nested,dc=example,dc=com", cn="Nested")
    group_guid = uuid.uuid4()
    _add_group(connection, "cn=Parent,dc=example,dc=com", cn="Parent", members=["cn=jane,dc=example,dc=com", "cn=Nested,dc=example,dc=com"], guid=group_guid)
    connection.bind()

    members = await connector.get_group_members(str(group_guid))
    # Only the real user is returned — the nested group DN doesn't match the user filter and is silently
    # excluded, matching the documented "direct membership only, no nested resolution" v1 scope.
    assert len(members) == 1
    assert members[0].display_name == "Jane Doe"


@pytest.mark.asyncio
async def test_get_roles_and_get_applications_return_empty_not_raise():
    connector, connection = _mock_connector()
    connection.bind()
    assert await connector.get_roles() == []
    assert await connector.get_applications() == []


@pytest.mark.asyncio
async def test_sync_dispatcher_is_not_implemented():
    """add_group_member/set_user_enabled became real writes in Phase 3 (see
    tests/test_active_directory_writes.py); create_user/create_group became real provisioning in Phase 4 (see
    tests/test_active_directory_provisioning.py). Only the dead sync() dispatcher (confirmed unused — the real
    sync path is app.services.directory_sync.run_sync()) remains a stub."""
    connector, connection = _mock_connector()
    connection.bind()
    with pytest.raises(NotImplementedError):
        await connector.sync()
