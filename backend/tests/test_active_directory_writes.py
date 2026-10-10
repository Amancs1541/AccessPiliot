"""Phase 3 (real writes) tests. DN resolution itself (_resolve_user_dn/_resolve_group_dn, which search by an
escaped-octet-string objectGUID filter) is already covered by live verification against a real DC — MOCK_SYNC's
filter engine doesn't correctly match that binary filter syntax (same documented limitation as
test_active_directory_sync.py's two skipped GUID-lookup tests). These tests monkeypatch DN resolution to a known
value instead, isolating what MOCK_SYNC CAN validate correctly: that the right LDAP operation (MODIFY_ADD/
MODIFY_DELETE/MODIFY_REPLACE/delete) happens with the right semantics once a DN is known."""
import uuid
from types import SimpleNamespace

import pytest
from ldap3 import MOCK_SYNC, SIMPLE, Connection, Server

from app.providers.active_directory import ActiveDirectoryProvider
from app.providers.graph_client import GraphError

NORMAL_ACCOUNT = 512
ACCOUNTDISABLE = 0x2


def _mock_connector():
    provider = SimpleNamespace(organization_url="ldaps://fake:636", tenant_id="DC=example,DC=com", graph_client_id="cn=admin,dc=example,dc=com", graph_client_secret_encrypted="irrelevant")
    connector = ActiveDirectoryProvider(provider)
    connector._bind_password = lambda: "secret"

    server = Server("fake")
    connection = Connection(server, user="cn=admin,dc=example,dc=com", password="secret", client_strategy=MOCK_SYNC)
    connection.strategy.add_entry("cn=admin,dc=example,dc=com", {"userPassword": "secret"})
    connector._connection = lambda: connection
    return connector, connection


@pytest.mark.asyncio
async def test_add_group_member_performs_a_real_modify_add():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connection.strategy.add_entry("cn=finance,dc=example,dc=com", {"objectClass": ["group"], "objectGUID": uuid.uuid4().bytes_le, "cn": "finance", "member": []})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"

    result = await connector.add_group_member("finance-guid", "jane-guid")
    assert result is True

    connection.bind()  # the connector unbinds after each call, same as it would for a real server
    connection.search("cn=finance,dc=example,dc=com", "(objectClass=group)", attributes=["member"])
    assert "cn=jane,dc=example,dc=com" in connection.entries[0].member.values


@pytest.mark.asyncio
async def test_remove_group_member_performs_a_real_modify_delete():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connection.strategy.add_entry("cn=finance,dc=example,dc=com", {"objectClass": ["group"], "objectGUID": uuid.uuid4().bytes_le, "cn": "finance", "member": ["cn=jane,dc=example,dc=com"]})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"

    result = await connector.remove_group_member("finance-guid", "jane-guid")
    assert result is True

    connection.bind()
    connection.search("cn=finance,dc=example,dc=com", "(objectClass=group)", attributes=["member"])
    assert connection.entries[0].member.values == []


@pytest.mark.asyncio
async def test_remove_group_member_is_idempotent_when_the_group_or_user_cannot_be_found():
    """A group/user that's vanished (or a GUID that never matched) is treated as "already removed" rather than
    an error — matches the same idempotent-write convention the real AD-confirmed description-string branch uses."""
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"
    assert await connector.remove_group_member("finance-guid", "missing-user-guid") is True


@pytest.mark.asyncio
async def test_add_group_member_raises_when_the_group_or_user_cannot_be_found():
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"
    with pytest.raises(GraphError):
        await connector.add_group_member("finance-guid", "missing-user-guid")


@pytest.mark.asyncio
async def test_set_user_enabled_false_sets_the_accountdisable_bit_and_preserves_other_bits():
    connector, connection = _mock_connector()
    # 0x10000 = DONT_EXPIRE_PASSWORD — an unrelated bit that must survive the disable operation untouched.
    starting_uac = NORMAL_ACCOUNT | 0x10000
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": starting_uac})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"

    result = await connector.set_user_enabled("jane-guid", False)
    assert result is True

    connection.bind()
    connection.search("cn=jane,dc=example,dc=com", "(objectClass=user)", attributes=["userAccountControl"])
    new_uac = int(connection.entries[0].userAccountControl.value)  # MOCK_SYNC returns this as a string, unlike the real server (confirmed in live testing) — the connector itself already casts, same as here
    assert new_uac & ACCOUNTDISABLE  # now disabled
    assert new_uac & 0x10000  # DONT_EXPIRE_PASSWORD preserved, not clobbered


@pytest.mark.asyncio
async def test_set_user_enabled_true_clears_the_accountdisable_bit():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT | ACCOUNTDISABLE})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"

    result = await connector.set_user_enabled("jane-guid", True)
    assert result is True

    connection.bind()
    connection.search("cn=jane,dc=example,dc=com", "(objectClass=user)", attributes=["userAccountControl"])
    assert not (int(connection.entries[0].userAccountControl.value) & ACCOUNTDISABLE)


@pytest.mark.asyncio
async def test_set_user_enabled_is_a_no_op_when_already_in_the_desired_state():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"
    assert await connector.set_user_enabled("jane-guid", True) is True


@pytest.mark.asyncio
async def test_update_user_sets_department_and_job_title():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"

    updated = await connector.update_user("jane-guid", department="Finance", job_title="Analyst")
    assert updated.department == "Finance"
    assert updated.job_title == "Analyst"


@pytest.mark.asyncio
async def test_update_user_clears_department_when_set_to_none():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT, "department": "Old Dept"})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"

    updated = await connector.update_user("jane-guid", department=None, job_title=None)
    assert updated.department is None
    assert updated.job_title is None


@pytest.mark.asyncio
async def test_set_user_manager_sets_the_manager_attribute():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connection.strategy.add_entry("cn=boss,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "boss", "userAccountControl": NORMAL_ACCOUNT})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=boss,dc=example,dc=com" if ext_id == "boss-guid" else "cn=jane,dc=example,dc=com"

    result = await connector.set_user_manager("jane-guid", "boss-guid")
    assert result is True
    connection.bind()
    connection.search("cn=jane,dc=example,dc=com", "(objectClass=user)", attributes=["manager"])
    assert connection.entries[0].manager.value == "cn=boss,dc=example,dc=com"


@pytest.mark.asyncio
async def test_delete_user_removes_the_entry():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"

    result = await connector.delete_user("jane-guid")
    assert result is True
    connection.bind()
    connection.search("dc=example,dc=com", "(sAMAccountName=jane)", attributes=["sAMAccountName"])
    assert len(connection.entries) == 0


@pytest.mark.asyncio
async def test_delete_user_is_idempotent_when_already_gone():
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    assert await connector.delete_user("missing-guid") is True


# ---- activate_assignment / revoke_assignment — the REAL entry points the PIM engine calls, not
# add_group_member/remove_group_member directly (see app.services.assignments._grant_provider_access /
# revoke_provider_access) ----

@pytest.mark.asyncio
async def test_activate_assignment_dispatches_a_group_request_to_add_group_member():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connection.strategy.add_entry("cn=finance,dc=example,dc=com", {"objectClass": ["group"], "objectGUID": uuid.uuid4().bytes_le, "cn": "finance", "member": []})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"

    result = await connector.activate_assignment({"resource_type": "GROUP", "target_external_id": "finance-guid", "user_external_id": "jane-guid"})
    assert result is True
    connection.bind()
    connection.search("cn=finance,dc=example,dc=com", "(objectClass=group)", attributes=["member"])
    assert "cn=jane,dc=example,dc=com" in connection.entries[0].member.values


@pytest.mark.asyncio
async def test_revoke_assignment_dispatches_a_group_request_to_remove_group_member():
    connector, connection = _mock_connector()
    connection.strategy.add_entry("cn=jane,dc=example,dc=com", {"objectClass": ["user", "person"], "objectCategory": "person", "objectGUID": uuid.uuid4().bytes_le, "sAMAccountName": "jane", "userAccountControl": NORMAL_ACCOUNT})
    connection.strategy.add_entry("cn=finance,dc=example,dc=com", {"objectClass": ["group"], "objectGUID": uuid.uuid4().bytes_le, "cn": "finance", "member": ["cn=jane,dc=example,dc=com"]})
    connector._resolve_user_dn = lambda conn, ext_id: "cn=jane,dc=example,dc=com"
    connector._resolve_group_dn = lambda conn, ext_id: "cn=finance,dc=example,dc=com"

    result = await connector.revoke_assignment({"resource_type": "GROUP", "target_external_id": "finance-guid", "user_external_id": "jane-guid"})
    assert result is True
    connection.bind()
    connection.search("cn=finance,dc=example,dc=com", "(objectClass=group)", attributes=["member"])
    assert connection.entries[0].member.values == []


@pytest.mark.asyncio
async def test_activate_assignment_rejects_a_role_or_application_request():
    """AD has no native role/application concept (get_roles/get_applications return empty) — a ROLE/APPLICATION
    assignment should never legitimately target this connector; confirms it fails clearly (ValueError, caught and
    translated by the real caller) rather than silently no-opping."""
    connector, _ = _mock_connector()
    with pytest.raises(ValueError):
        await connector.activate_assignment({"resource_type": "ROLE", "target_external_id": "x", "user_external_id": "y"})
    with pytest.raises(ValueError):
        await connector.activate_assignment({"resource_type": "APPLICATION", "target_external_id": "x", "user_external_id": "y"})


@pytest.mark.asyncio
async def test_update_user_raises_grapherror_not_connectionerror_when_user_not_found():
    """Every real call site for update_user (app.services.identity_attributes, app.services.lifecycle) only
    catches GraphError — confirmed by reading both. A bare ConnectionError would propagate unhandled as a raw
    500 instead of a clean AccessPilotError, which is exactly the bug this test guards against."""
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    with pytest.raises(GraphError):
        await connector.update_user("missing-guid", department="X", job_title="Y")


@pytest.mark.asyncio
async def test_set_user_enabled_raises_grapherror_not_connectionerror_when_user_not_found():
    """Every real call site (app.services.joiner, app.services.privileged_accounts, app.services.accounts) only
    catches GraphError."""
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    with pytest.raises(GraphError):
        await connector.set_user_enabled("missing-guid", True)


@pytest.mark.asyncio
async def test_set_user_manager_raises_grapherror_not_connectionerror_when_not_found():
    """app.services.joiner catches (NotImplementedError, GraphError) specifically."""
    connector, connection = _mock_connector()
    connector._resolve_user_dn = lambda conn, ext_id: None
    with pytest.raises(GraphError):
        await connector.set_user_manager("missing-guid", "also-missing-guid")


@pytest.mark.asyncio
async def test_activate_assignment_rejects_a_malformed_request():
    connector, _ = _mock_connector()
    with pytest.raises(ValueError):
        await connector.activate_assignment({"resource_type": "GROUP"})  # missing target/user ids
