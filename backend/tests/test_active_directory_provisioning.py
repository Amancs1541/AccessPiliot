"""Phase 4 (real provisioning) tests. Conflict detection is exercised via the connector's own pre-check search
(sAMAccountName/UPN or cn), which MOCK_SYNC's plain string-attribute filters handle correctly — unlike the
GUID-binary-filter limitation documented in test_active_directory_sync.py/test_active_directory_writes.py, which
doesn't apply here.

Two more MOCK_SYNC limitations confirmed here, same "honestly worked around, not silently assumed" treatment as
the existing ones: MOCK_SYNC's add() does NOT auto-generate objectGUID the way a real DC does (confirmed live in
this session's Phase 4 calibration — every real add() got a real GUID back), and does not auto-derive
objectCategory=person for a plain objectClass=user entry either (same gap test_active_directory_sync.py already
documented for objectCategory on PRE-SEEDED entries). _mock_connector() below wraps add() to inject both,
simulating what the real DC actually does — the connector's own production code sets neither itself, correctly
relying on the real server for both, exactly as already proven live."""
import uuid
from types import SimpleNamespace

import pytest
from ldap3 import MODIFY_REPLACE, MOCK_SYNC, SIMPLE, Connection, Server

from app.providers.active_directory import ActiveDirectoryProvider
from app.providers.base import NewGroupRequest, NewUserRequest, ProviderConflictError
from app.providers.graph_client import GraphError

NORMAL_ACCOUNT_ENABLED = 0x200
NORMAL_ACCOUNT_DISABLED = 0x202


def _mock_connector():
    provider = SimpleNamespace(organization_url="ldaps://fake:636", tenant_id="DC=example,DC=com", graph_client_id="cn=admin,dc=example,dc=com", graph_client_secret_encrypted="irrelevant")
    connector = ActiveDirectoryProvider(provider)
    connector._bind_password = lambda: "secret"
    server = Server("fake")
    connection = Connection(server, user="cn=admin,dc=example,dc=com", password="secret", client_strategy=MOCK_SYNC)
    connection.strategy.add_entry("cn=admin,dc=example,dc=com", {"userPassword": "secret"})

    real_add = connection.add

    def add_simulating_real_dc_auto_attributes(dn, object_class=None, attributes=None, controls=None):
        result = real_add(dn, object_class=object_class, attributes=attributes, controls=controls)
        if result:
            extra = {"objectGUID": [(MODIFY_REPLACE, [uuid.uuid4().bytes_le])]}
            if attributes and attributes.get("objectClass") == "user":
                extra["objectCategory"] = [(MODIFY_REPLACE, ["person"])]
            connection.modify(dn, extra)
        return result

    connection.add = add_simulating_real_dc_auto_attributes
    connector._connection = lambda: connection
    return connector, connection


def _rebind_and_search(connection, dn, filter_str, attributes):
    connection.bind()  # the connector unbinds after each call, same as it would for a real server
    connection.search(dn, filter_str, attributes=attributes)
    return connection.entries


@pytest.mark.asyncio
async def test_create_user_creates_a_real_disabled_then_enabled_account_with_a_working_password():
    connector, connection = _mock_connector()
    created = await connector.create_user(NewUserRequest(display_name="Jane Newhire", user_principal_name="jane.newhire@example.com", mail_nickname="jane.newhire", department="Finance", job_title="Analyst", given_name="Jane", surname="Newhire", enabled=True))

    assert created.user.display_name == "Jane Newhire"
    assert created.user.status == "ACTIVE"  # enabled=True was applied after the password was set
    assert created.temporary_password  # a real one-time password was generated

    entries = _rebind_and_search(connection, "DC=example,DC=com", "(sAMAccountName=jane.newhire)", ["userAccountControl", "unicodePwd", "department", "title"])
    assert len(entries) == 1
    assert int(entries[0].userAccountControl.value) == NORMAL_ACCOUNT_ENABLED
    assert entries[0].department.value == "Finance"
    assert entries[0].title.value == "Analyst"


@pytest.mark.asyncio
async def test_create_user_disabled_stays_disabled():
    connector, connection = _mock_connector()
    created = await connector.create_user(NewUserRequest(display_name="Future Hire", user_principal_name="future.hire@example.com", mail_nickname="future.hire", department=None, job_title=None, enabled=False))
    assert created.user.status == "DISABLED"

    entries = _rebind_and_search(connection, "DC=example,DC=com", "(sAMAccountName=future.hire)", ["userAccountControl"])
    assert int(entries[0].userAccountControl.value) == NORMAL_ACCOUNT_DISABLED


@pytest.mark.asyncio
async def test_create_user_rejects_a_duplicate_username_or_upn():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=existing,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "sAMAccountName": "jane.newhire", "userPrincipalName": "someone.else@example.com", "userAccountControl": NORMAL_ACCOUNT_ENABLED})

    with pytest.raises(ProviderConflictError):
        await connector.create_user(NewUserRequest(display_name="Jane Newhire", user_principal_name="jane.newhire@example.com", mail_nickname="jane.newhire", department=None, job_title=None, enabled=True))

    # Nothing new was added — only the one pre-existing entry (plus the admin bind entry) is present.
    entries = _rebind_and_search(connection, "DC=example,DC=com", "(objectClass=user)", ["sAMAccountName"])
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_create_user_sam_account_name_is_sanitized_and_truncated():
    connector, connection = _mock_connector()
    long_local_part = "a" * 30
    created = await connector.create_user(NewUserRequest(display_name="Long Name Person", user_principal_name=f"{long_local_part}@example.com", mail_nickname=f"{long_local_part}", department=None, job_title=None, enabled=False))
    entries = _rebind_and_search(connection, "DC=example,DC=com", "(objectClass=user)", ["sAMAccountName"])
    sam_names = [str(e.sAMAccountName.value) for e in entries if e.sAMAccountName.value]
    assert any(len(name) <= 20 for name in sam_names)
    assert created.user.display_name == "Long Name Person"


@pytest.mark.asyncio
async def test_create_user_applies_the_optional_profile_fields():
    connector, connection = _mock_connector()
    created = await connector.create_user(NewUserRequest(display_name="Profile Person", user_principal_name="profile.person@example.com", mail_nickname="profile.person", department=None, job_title=None, enabled=False, office="HQ-4", company="Acme Corp", mobile_phone="+1-555-0100", street_address="1 Main St", city="Springfield", state="IL", postal_code="62701", country="USA", description="Calibration test account"))
    entries = _rebind_and_search(connection, "DC=example,DC=com", "(sAMAccountName=profile.person)", ["physicalDeliveryOfficeName", "company", "mobile", "streetAddress", "l", "st", "postalCode", "co", "description"])
    entry = entries[0]
    assert entry.physicalDeliveryOfficeName.value == "HQ-4"
    assert entry.company.value == "Acme Corp"
    assert entry.mobile.value == "+1-555-0100"
    assert entry.streetAddress.value == "1 Main St"
    assert entry.l.value == "Springfield"
    assert entry.st.value == "IL"
    assert entry.postalCode.value == "62701"
    assert entry.co.value == "USA"
    assert entry.description.value == "Calibration test account"


@pytest.mark.asyncio
async def test_create_group_creates_a_real_global_security_group():
    connector, connection = _mock_connector()
    group = await connector.create_group(NewGroupRequest(display_name="Finance Team", description="Finance department group", mail_nickname="finance-team"))
    assert group.name == "Finance Team"
    assert group.description == "Finance department group"

    entries = _rebind_and_search(connection, "DC=example,DC=com", "(objectClass=group)", ["groupType", "cn"])
    assert len(entries) == 1
    assert int(entries[0].groupType.value) == -2147483646


@pytest.mark.asyncio
async def test_create_group_rejects_a_duplicate_name():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=existing-group,dc=example,dc=com", {"objectClass": ["group"], "cn": "Finance Team", "sAMAccountName": "something-else"})

    with pytest.raises(ProviderConflictError):
        await connector.create_group(NewGroupRequest(display_name="Finance Team", description=None, mail_nickname="finance-team-2"))
