"""Active Directory connector.

Phase 1 (connectivity agent) + the dynamic-config step before Phase 2: real, dynamic configuration (LDAP URL,
base DN, bind DN/password — entered in the Providers UI, reusing the exact credential-encryption path Entra/Okta
already use) and a real test_connection() binding over LDAPS.

Phase 2 (this pass): real READ-ONLY sync — get_users/get_groups/get_group_members, wired into the existing
app.services.directory_sync.run_sync() exactly the way Entra/Okta already are, with zero changes to that
orchestration code. get_roles/get_applications deliberately return an empty list rather than raising: plain
on-prem AD has no native "directory role" or "enterprise application" concept the way Entra does (no
admin-configured privileged-group-as-role mapping has been decided yet — a real open product question, not
silently guessed here), and run_sync() calls both unconditionally, so a real list is required for sync to
complete at all.

Phase 3 (this pass): real WRITES — add_group_member/remove_group_member/set_user_enabled, each calibrated
against the real test DC first, not guessed: AD's modify() does NOT raise on an idempotent no-op (adding someone
already a member, or removing someone who isn't) — it returns False with a specific result description
('entryAlreadyExists' / 'unwillingToPerform' on this real DC), which this file treats as success, the same
"already in the desired state counts as success" convention Entra/Okta's own write methods already follow.
set_user_enabled preserves every other userAccountControl bit — it's a bitmask, not a status flag, so a blind
overwrite would silently clobber unrelated account flags.

Phase 4 (this pass): real PROVISIONING — create_user/create_group, both calibrated against the real test DC
first: AD rejects setting unicodePwd in the same add() call that creates the object (must be a separate modify()
right after), and a new account is always created disabled (userAccountControl=514) regardless of
request.enabled, has its password set, and is ONLY THEN enabled if requested — a half-created, passwordless
account on a failed password-set is deleted rather than left behind. create_group makes a real global,
security-enabled group (groupType=-2147483646). Both land new objects under CN=Users,<base DN> — the standard
default container every domain has; no per-provider configurable OU exists yet (a real, deliberate scope limit,
not an oversight).

Still NOT implemented (later work, not yet scoped): nested/transitive group membership, multi-domain/forest
support, AD's attribute-range pagination for a single group with 1500+ direct members (every group seen in this
tenant's live testing returns simply; a real "range=" ranged-retrieval loop is a documented follow-up once a
group that large is actually tested against), a configurable target OU for provisioning.

Runs directly in the backend process — no agent involved — exactly how Entra/Okta already work; the agent (see
app.services.agent) only matters for a deployment where AccessPilot can't reach the DC directly."""
from __future__ import annotations

import re
import secrets
import ssl
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from ldap3 import ALL, BASE, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException, LDAPResponseTimeoutError, LDAPSocketCloseError, LDAPSocketOpenError, LDAPSocketReceiveError, LDAPSocketSendError
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from app.providers.base import CreatedUser, IdentityProvider, NewGroupRequest, NewUserRequest, NormalizedApplication, NormalizedApplicationPermission, NormalizedDomain, NormalizedGroup, NormalizedRole, NormalizedUser, ProviderConflictError
from app.providers.graph_client import GraphError
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential

_NETWORK_ERRORS = (LDAPSocketOpenError, LDAPSocketReceiveError, LDAPSocketSendError, LDAPSocketCloseError)

# A real human/service user account, excluding computer accounts (objectCategory=computer is a sibling, not a
# subtype, of person) — matches the standard AD convention for "find real user accounts."
_USER_FILTER = "(&(objectClass=user)(objectCategory=person))"
_GROUP_FILTER = "(objectClass=group)"
_USER_ATTRIBUTES = ["objectGUID", "sAMAccountName", "userPrincipalName", "mail", "displayName", "givenName", "sn", "department", "title", "userAccountControl"]
_GROUP_ATTRIBUTES = ["objectGUID", "cn", "name", "description", "member"]
ACCOUNTDISABLE = 0x2
NORMAL_ACCOUNT_DISABLED = 0x202  # NORMAL_ACCOUNT (0x200) | ACCOUNTDISABLE (0x2) — the only valid state for a
# brand-new account before it has a password (AD rejects enabling one with no usable password set yet).
NORMAL_ACCOUNT_ENABLED = 0x200
GROUP_TYPE_GLOBAL_SECURITY = -2147483646  # the standard documented value for a global, security-enabled group
_SAM_INVALID_CHARS = re.compile(r'["/\\\[\]:;|=,+*?<>@]')  # AD's documented disallowed sAMAccountName characters


def _sam_account_name(value: str) -> str:
    """AD's legacy sAMAccountName: at most 20 characters, and a specific, documented set of characters are
    rejected outright — confirmed this truncation/sanitizing is needed since a real username convention
    (e.g. "first.last@domain") can easily exceed 20 characters once the domain's own naming convention is applied."""
    cleaned = _SAM_INVALID_CHARS.sub("", value).strip()
    return (cleaned[:20] or "user").rstrip(".")


def _clean_guid(raw) -> str:
    """ldap3's schema-aware formatter normally turns the binary objectGUID into a '{xxxxxxxx-...}' string
    already — this normalizes it to a plain lowercase UUID string, the shape used everywhere else in this app.
    Falls back to decoding it directly (same little-endian byte order as _guid_filter_value below) on the rare
    connection where schema-aware formatting isn't active and objectGUID still arrives as raw bytes — confirmed
    this happens under ldap3's own MOCK_SYNC test strategy, so it's a real code path, not just a test accommodation."""
    if isinstance(raw, bytes):
        return str(uuid.UUID(bytes_le=raw))
    return raw.strip("{}").lower()


def _guid_filter_value(external_id: str) -> str:
    """The reverse direction: AD's objectGUID is stored binary, and an LDAP filter needs it as a backslash-escaped
    octet string in the GUID's own little-endian byte order (uuid.bytes_le) — NOT a plain string match, and NOT
    the big-endian uuid.bytes a filter against most other binary attributes would use. A well-known AD gotcha."""
    return "".join(f"\\{b:02x}" for b in uuid.UUID(external_id).bytes_le)


def _user_from_entry(entry) -> NormalizedUser:
    uac = int(entry.userAccountControl.value) if "userAccountControl" in entry and entry.userAccountControl.value is not None else 0
    email = (entry.mail.value if "mail" in entry and entry.mail.value else None) or (entry.userPrincipalName.value if "userPrincipalName" in entry and entry.userPrincipalName.value else None) or ""
    display_name = (entry.displayName.value if "displayName" in entry and entry.displayName.value else None) or (entry.sAMAccountName.value if "sAMAccountName" in entry else "")
    return NormalizedUser(
        external_id=_clean_guid(entry.objectGUID.value),
        email=email,
        display_name=display_name,
        given_name=entry.givenName.value if "givenName" in entry else None,
        surname=entry.sn.value if "sn" in entry else None,
        department=entry.department.value if "department" in entry else None,
        job_title=entry.title.value if "title" in entry else None,
        status="DISABLED" if (uac & ACCOUNTDISABLE) else "ACTIVE",
    )


def _group_from_entry(entry) -> NormalizedGroup:
    name = (entry.cn.value if "cn" in entry and entry.cn.value else None) or (entry.name.value if "name" in entry and entry.name.value else "")
    return NormalizedGroup(
        external_id=_clean_guid(entry.objectGUID.value),
        name=name,
        description=entry.description.value if "description" in entry and entry.description.value else None,
        is_privileged=name in ("Domain Admins", "Enterprise Admins", "Schema Admins", "Administrators"),
        status="ACTIVE",
    )


class ActiveDirectoryProvider(IdentityProvider):
    """Reuses the same generic IdentityProvider row every connector does, with AD-specific meaning for 4 already-
    generic fields: organization_url is the LDAP(S) URL (e.g. ldaps://192.168.71.4:636), tenant_id is the base DN
    to search under (e.g. DC=TeamDEV,DC=local), graph_client_id is the bind DN/UPN, and
    graph_client_secret_encrypted is the bind password — set through the exact same
    PATCH /providers/{id}/credentials endpoint Entra's Graph client secret already uses, Fernet-encrypted the
    same way. No new DB columns, no new encryption mechanism."""

    def __init__(self, provider: Any = None):
        self.provider = provider

    def _server_args(self) -> tuple[str, int, bool]:
        url = getattr(self.provider, "organization_url", None)
        if not url:
            raise ValueError("An LDAP(S) URL is required (e.g. ldaps://10.0.0.5:636).")
        parsed = urlparse(url)
        if parsed.scheme not in ("ldap", "ldaps"):
            raise ValueError("The LDAP URL must start with ldap:// or ldaps://.")
        if not parsed.hostname:
            raise ValueError("The LDAP URL is missing a hostname.")
        use_ssl = parsed.scheme == "ldaps"
        port = parsed.port or (636 if use_ssl else 389)
        return parsed.hostname, port, use_ssl

    def _base_dn(self) -> str:
        base_dn = getattr(self.provider, "tenant_id", None)
        if not base_dn:
            raise ValueError("A base DN is required (e.g. DC=example,DC=local).")
        return base_dn

    def _bind_dn(self) -> str:
        bind_dn = getattr(self.provider, "graph_client_id", None)
        if not bind_dn:
            raise ValueError("A bind username/DN is required.")
        return bind_dn

    def _bind_password(self) -> str:
        encrypted = getattr(self.provider, "graph_client_secret_encrypted", None)
        if not encrypted:
            raise ValueError("A bind password is required.")
        try:
            return decrypt_credential(encrypted)
        except CredentialEncryptionError as exc:
            raise ValueError(str(exc)) from exc

    def _connection(self) -> Connection:
        host, port, use_ssl = self._server_args()
        # validate=ssl.CERT_NONE: a test/internal AD CS-issued certificate is very often not in this process's
        # trust store — this phase proves the directory itself is reachable and the credentials are right, the
        # same scope boundary test_connection() has everywhere else; real certificate pinning/trust is a later,
        # deliberate hardening pass, not silently skipped by accident.
        tls = Tls(validate=ssl.CERT_NONE) if use_ssl else None
        server = Server(host, port=port, use_ssl=use_ssl, tls=tls, get_info=ALL, connect_timeout=10)
        return Connection(server, user=self._bind_dn(), password=self._bind_password(), authentication=SIMPLE, receive_timeout=10)

    def _bound_connection(self) -> Connection:
        """Every real read in this file needs a bound connection — same bind-and-translate-errors logic
        test_connection() already has, reused instead of duplicated."""
        connection = self._connection()
        try:
            bound = connection.bind()
        except LDAPResponseTimeoutError as exc:
            raise TimeoutError("The directory did not respond in time.") from exc
        except _NETWORK_ERRORS as exc:
            raise ConnectionError(f"Could not reach the directory: {exc}") from exc
        except LDAPBindError as exc:
            raise ConnectionError(f"The directory rejected the connection: {exc}") from exc
        except LDAPException as exc:
            raise ConnectionError(str(exc)) from exc
        if not bound:
            raise ConnectionError("The directory rejected the configured bind credentials.")
        return connection

    def _bound_connection_for_write(self) -> Connection:
        """Same bind as _bound_connection, but translates a connectivity failure into GraphError instead of
        ConnectionError/TimeoutError. Confirmed by reading every real write call site in this app
        (services.identity_attributes, services.joiner, services.privileged_accounts, services.accounts,
        services.leaver_followup): every single one catches GraphError specifically — ConnectionError/TimeoutError
        are test_connection()'s own contract (matching what provider_configuration.test_provider() catches
        instead), not what a real write path's caller is prepared to handle. Every write method below uses this,
        never _bound_connection directly."""
        try:
            return self._bound_connection()
        except (ValueError, ConnectionError, TimeoutError) as exc:
            raise GraphError("PROVIDER_UNAVAILABLE", str(exc), 502) from exc

    async def test_connection(self) -> bool:
        """The one real IdentityProvider method from the pre-Phase-2 step — still here, still used by the
        Providers UI's "Test connection" button. Runs synchronously (ldap3 has no native asyncio support); fine
        for an on-demand admin action or a periodic sync, not something called per-request."""
        try:
            connection = self._bound_connection()
        except ConnectionError:
            return False
        connection.unbind()
        return True

    async def get_users(self, query: Optional[str] = None) -> list[NormalizedUser]:
        connection = self._bound_connection()
        filter_str = _USER_FILTER if not query else f"(&{_USER_FILTER}(|(displayName=*{query}*)(sAMAccountName=*{query}*)(mail=*{query}*)))"
        # generator=False (not the bare return value) is what actually matters here: it drives paged_search to
        # exhaust every page before returning, the same way connection.entries gets populated after a plain
        # .search() call — AD caps a single unpaged search at 1000 results, so this is required, not optional,
        # once a real directory has more users than that.
        connection.extend.standard.paged_search(self._base_dn(), filter_str, search_scope=SUBTREE, attributes=_USER_ATTRIBUTES, paged_size=500, generator=False)
        users = [_user_from_entry(entry) for entry in connection.entries]
        connection.unbind()
        return users

    async def get_user(self, external_id: str) -> Optional[NormalizedUser]:
        connection = self._bound_connection()
        try:
            filter_str = f"(&{_USER_FILTER}(objectGUID={_guid_filter_value(external_id)}))"
            connection.search(self._base_dn(), filter_str, SUBTREE, attributes=_USER_ATTRIBUTES)
            if not connection.entries:
                return None
            return _user_from_entry(connection.entries[0])
        finally:
            connection.unbind()

    async def get_groups(self, query: Optional[str] = None) -> list[NormalizedGroup]:
        connection = self._bound_connection()
        filter_str = _GROUP_FILTER if not query else f"(&{_GROUP_FILTER}(cn=*{query}*))"
        connection.extend.standard.paged_search(self._base_dn(), filter_str, search_scope=SUBTREE, attributes=_GROUP_ATTRIBUTES, paged_size=500, generator=False)
        groups = [_group_from_entry(entry) for entry in connection.entries]
        connection.unbind()
        return groups

    async def get_group(self, external_id: str) -> Optional[NormalizedGroup]:
        connection = self._bound_connection()
        try:
            filter_str = f"(&{_GROUP_FILTER}(objectGUID={_guid_filter_value(external_id)}))"
            connection.search(self._base_dn(), filter_str, SUBTREE, attributes=_GROUP_ATTRIBUTES)
            if not connection.entries:
                return None
            return _group_from_entry(connection.entries[0])
        finally:
            connection.unbind()

    async def get_group_members(self, external_id: str) -> list[NormalizedUser]:
        """Direct membership only (the group's own `member` attribute) — AD's `member`/`memberOf` reflects direct
        membership by default; resolving nested/transitive membership needs a different, more expensive query
        and is a deliberate later phase, not silently approximated here."""
        connection = self._bound_connection()
        try:
            filter_str = f"(&{_GROUP_FILTER}(objectGUID={_guid_filter_value(external_id)}))"
            connection.search(self._base_dn(), filter_str, SUBTREE, attributes=["member"])
            if not connection.entries:
                return []
            member_dns = list(connection.entries[0].member.values) if "member" in connection.entries[0] else []
            members: list[NormalizedUser] = []
            for dn in member_dns:
                connection.search(dn, _USER_FILTER, BASE, attributes=_USER_ATTRIBUTES)
                if connection.entries:
                    members.append(_user_from_entry(connection.entries[0]))
            return members
        finally:
            connection.unbind()

    async def get_roles(self, query: Optional[str] = None) -> list[NormalizedRole]:
        """Plain on-prem AD has no native "directory role" catalog the way Entra does — mapping a configured set
        of privileged groups as roles is a real product decision not yet made (see this file's own docstring), so
        this deliberately returns empty rather than guessing. run_sync() calls this unconditionally, so it must
        return a real (empty) list, not raise."""
        return []

    async def get_applications(self, query: Optional[str] = None) -> list[NormalizedApplication]:
        """No equivalent in plain on-prem AD (that's an Entra Enterprise App / cloud concept) — same "must return
        a real empty list, not raise" reasoning as get_roles."""
        return []

    async def _not_implemented(self):
        raise NotImplementedError("This Active Directory operation is not built yet (see this file's docstring for what's planned next).")

    def _resolve_dn(self, connection: Connection, filter_str: str) -> Optional[str]:
        connection.search(self._base_dn(), filter_str, SUBTREE, attributes=["distinguishedName"])
        if not connection.entries:
            return None
        return connection.entries[0].entry_dn

    def _resolve_user_dn(self, connection: Connection, external_id: str) -> Optional[str]:
        return self._resolve_dn(connection, f"(&{_USER_FILTER}(objectGUID={_guid_filter_value(external_id)}))")

    def _resolve_group_dn(self, connection: Connection, external_id: str) -> Optional[str]:
        return self._resolve_dn(connection, f"(&{_GROUP_FILTER}(objectGUID={_guid_filter_value(external_id)}))")

    async def update_user(self, external_id: str, *, department: Optional[str], job_title: Optional[str]) -> NormalizedUser:
        connection = self._bound_connection_for_write()
        try:
            user_dn = self._resolve_user_dn(connection, external_id)
            if user_dn is None:
                raise GraphError("USER_NOT_FOUND", "That user was not found in the directory.", 404)
            # [] (not None) clears an AD attribute entirely via MODIFY_REPLACE — None would be rejected as an
            # invalid value. Matches how an admin clearing a field in the UI should actually clear it in AD too,
            # not leave the old value behind.
            changes = {"department": [(MODIFY_REPLACE, [department] if department else [])], "title": [(MODIFY_REPLACE, [job_title] if job_title else [])]}
            if not connection.modify(user_dn, changes):
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this update: {connection.result.get('description')}", 502)
            connection.search(user_dn, _USER_FILTER, BASE, attributes=_USER_ATTRIBUTES)
            return _user_from_entry(connection.entries[0])
        finally:
            connection.unbind()

    async def set_user_enabled(self, external_id: str, enabled: bool) -> bool:
        """userAccountControl is a bitmask, not a status flag — every other bit on it (password-never-expires,
        smartcard-required, etc.) must survive this call untouched, so the current value is read first and only
        the ACCOUNTDISABLE bit is flipped, never a blind overwrite."""
        connection = self._bound_connection_for_write()
        try:
            user_dn = self._resolve_user_dn(connection, external_id)
            if user_dn is None:
                raise GraphError("USER_NOT_FOUND", "That user was not found in the directory.", 404)
            connection.search(user_dn, _USER_FILTER, BASE, attributes=["userAccountControl"])
            if not connection.entries:
                raise GraphError("USER_NOT_FOUND", "That user was not found in the directory.", 404)
            current_uac = int(connection.entries[0].userAccountControl.value)
            new_uac = (current_uac & ~ACCOUNTDISABLE) if enabled else (current_uac | ACCOUNTDISABLE)
            if new_uac == current_uac:
                return True  # already in the desired state
            if not connection.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [new_uac])]}):
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this update: {connection.result.get('description')}", 502)
            return True
        finally:
            connection.unbind()

    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        connection = self._bound_connection_for_write()
        try:
            group_dn = self._resolve_group_dn(connection, group_external_id)
            user_dn = self._resolve_user_dn(connection, user_external_id)
            if group_dn is None or user_dn is None:
                raise GraphError("PROVIDER_RESOURCE_NOT_FOUND", "The group or user was not found in the directory.", 404)
            if connection.modify(group_dn, {"member": [(MODIFY_ADD, [user_dn])]}):
                return True
            # Confirmed live against a real DC: AD returns False with description "entryAlreadyExists" (LDAP
            # result 68) for a redundant add — never an exception. Already a member counts as success, the same
            # idempotent-write convention Entra/Okta's own add_group_member already follow.
            if connection.result.get("description") == "entryAlreadyExists":
                return True
            raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this group membership change: {connection.result.get('description')}", 502)
        finally:
            connection.unbind()

    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        connection = self._bound_connection_for_write()
        try:
            group_dn = self._resolve_group_dn(connection, group_external_id)
            user_dn = self._resolve_user_dn(connection, user_external_id)
            if group_dn is None or user_dn is None:
                return True  # already gone (or never existed) either way — nothing left to remove
            if connection.modify(group_dn, {"member": [(MODIFY_DELETE, [user_dn])]}):
                return True
            # Confirmed live: AD returns False with description "unwillingToPerform" (LDAP result 53) when
            # removing someone who isn't currently a member — not a distinct "not found" error. Treated as
            # success for the same idempotent-write reason as the entryAlreadyExists case above.
            if connection.result.get("description") in ("unwillingToPerform", "noSuchAttribute"):
                return True
            raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this group membership change: {connection.result.get('description')}", 502)
        finally:
            connection.unbind()

    async def set_user_manager(self, external_id: str, manager_external_id: str) -> bool:
        connection = self._bound_connection_for_write()
        try:
            user_dn = self._resolve_user_dn(connection, external_id)
            manager_dn = self._resolve_user_dn(connection, manager_external_id)
            if user_dn is None or manager_dn is None:
                raise GraphError("USER_NOT_FOUND", "The user or manager was not found in the directory.", 404)
            if not connection.modify(user_dn, {"manager": [(MODIFY_REPLACE, [manager_dn])]}):
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this update: {connection.result.get('description')}", 502)
            return True
        finally:
            connection.unbind()

    async def delete_user(self, external_id: str) -> bool:
        connection = self._bound_connection_for_write()
        try:
            user_dn = self._resolve_user_dn(connection, external_id)
            if user_dn is None:
                return True  # already gone
            if not connection.delete(user_dn):
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this delete: {connection.result.get('description')}", 502)
            return True
        finally:
            connection.unbind()

    async def get_role(self, external_id: str) -> Optional[NormalizedRole]:
        return None

    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]:
        return await self._not_implemented()

    async def set_application_enabled(self, external_id: str, enabled: bool) -> bool:
        return await self._not_implemented()

    async def get_application_permissions(self, external_id: str) -> list[NormalizedApplicationPermission]:
        return await self._not_implemented()

    async def activate_assignment(self, request: dict[str, Any]) -> bool:
        """The REAL entry point the PIM engine actually calls (app.services.assignments._grant_provider_access)
        when an AD group is self-activated or admin-bypass-assigned — not add_group_member directly. Only GROUP
        is supported: get_roles()/get_applications() return empty for this connector (see their own docstrings),
        so a ROLE/APPLICATION request should never legitimately target an AD provider; _grant_provider_access
        catches (GraphError, NotImplementedError, ValueError) specifically, so a clear ValueError here becomes a
        clean error rather than an unhandled exception. add_group_member itself already raises GraphError on
        failure (via _bound_connection_for_write), so nothing further needs translating here."""
        resource_type = request.get("resource_type")
        target_external_id = request.get("target_external_id")
        user_external_id = request.get("user_external_id")
        if not resource_type or not target_external_id or not user_external_id:
            raise ValueError("Assignment activation requires resource_type, target_external_id, and user_external_id.")
        if resource_type != "GROUP":
            raise ValueError(f"Active Directory does not support assignment resource type '{resource_type}' yet — only GROUP is supported today.")
        return await self.add_group_member(target_external_id, user_external_id)

    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool:
        """Mirrors activate_assignment above — the real entry point app.services.assignments.revoke_provider_access
        actually calls."""
        resource_type = assignment.get("resource_type")
        target_external_id = assignment.get("target_external_id")
        user_external_id = assignment.get("user_external_id")
        if not resource_type or not target_external_id or not user_external_id:
            raise ValueError("Assignment revocation requires resource_type, target_external_id, and user_external_id.")
        if resource_type != "GROUP":
            raise ValueError(f"Active Directory does not support assignment resource type '{resource_type}' yet — only GROUP is supported today.")
        return await self.remove_group_member(target_external_id, user_external_id)

    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool:
        return await self._not_implemented()

    async def sync(self) -> dict[str, int]:
        # Orchestration (DB upserts) lives in app.services.directory_sync; the connector only fetches normalized
        # data — same division of responsibility as EntraProvider.sync() (itself also not implemented — the real
        # sync path is app.services.directory_sync.run_sync(), confirmed this is genuinely unused dead code).
        return await self._not_implemented()

    async def create_user(self, request: NewUserRequest) -> CreatedUser:
        """Phase 4: real provisioning. Calibrated against the real test DC first, not guessed: AD rejects setting
        unicodePwd in the SAME add() call that creates the object — it must be a separate modify() right after —
        and an account cannot be meaningfully enabled before a valid password is set, so every new account is
        created disabled (userAccountControl=514) regardless of `request.enabled`, has its password set, and is
        ONLY THEN enabled if `request.enabled` was True. If setting the password fails (e.g. it doesn't meet this
        domain's complexity policy), the half-created, passwordless object is deleted rather than left behind —
        confirmed live that add() + modify(unicodePwd) + modify(userAccountControl) + a real bind with the
        generated password all succeed in that order against the real DC."""
        connection = self._bound_connection_for_write()
        try:
            sam = _sam_account_name(request.mail_nickname)
            conflict_filter = f"(&{_USER_FILTER}(|(sAMAccountName={escape_filter_chars(sam)})(userPrincipalName={escape_filter_chars(request.user_principal_name)})))"
            if self._resolve_dn(connection, conflict_filter) is not None:
                raise ProviderConflictError(f"An account with the username {sam} or UPN {request.user_principal_name} already exists in Active Directory.")
            user_dn = f"CN={escape_rdn(request.display_name.strip() or sam)},CN=Users,{self._base_dn()}"
            attrs: dict[str, Any] = {"objectClass": "user", "sAMAccountName": sam, "userPrincipalName": request.user_principal_name, "displayName": request.display_name, "userAccountControl": NORMAL_ACCOUNT_DISABLED}
            if request.department:
                attrs["department"] = request.department
            if request.job_title:
                attrs["title"] = request.job_title
            if request.given_name:
                attrs["givenName"] = request.given_name
            if request.surname:
                attrs["sn"] = request.surname
            if request.employee_id:
                attrs["employeeID"] = request.employee_id
            if request.office:
                attrs["physicalDeliveryOfficeName"] = request.office
            if request.company:
                attrs["company"] = request.company
            if request.mobile_phone:
                attrs["mobile"] = request.mobile_phone
            if request.street_address:
                attrs["streetAddress"] = request.street_address
            if request.city:
                attrs["l"] = request.city
            if request.state:
                attrs["st"] = request.state
            if request.postal_code:
                attrs["postalCode"] = request.postal_code
            if request.country:
                attrs["co"] = request.country
            if request.description:
                attrs["description"] = request.description
            if not connection.add(user_dn, attributes=attrs):
                if connection.result.get("description") == "entryAlreadyExists":
                    raise ProviderConflictError(f"An account named {request.display_name} already exists in Active Directory.")
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this account creation: {connection.result.get('description')}", 502)
            password = secrets.token_urlsafe(18)
            try:
                if not connection.modify(user_dn, {"unicodePwd": [(MODIFY_REPLACE, [f'"{password}"'.encode("utf-16-le")])]}):
                    raise GraphError("PROVIDER_UPDATE_FAILED", f"The account was created but the directory rejected the generated password: {connection.result.get('description')}", 502)
                if request.enabled and not connection.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [NORMAL_ACCOUNT_ENABLED])]}):
                    raise GraphError("PROVIDER_UPDATE_FAILED", f"The account was created but could not be enabled: {connection.result.get('description')}", 502)
            except GraphError:
                connection.delete(user_dn)  # never leave a half-created, passwordless account behind
                raise
            connection.search(user_dn, _USER_FILTER, BASE, attributes=_USER_ATTRIBUTES)
            return CreatedUser(user=_user_from_entry(connection.entries[0]), temporary_password=password)
        finally:
            connection.unbind()

    async def create_group(self, request: NewGroupRequest) -> NormalizedGroup:
        """Creates a real, global, security-enabled group (groupType=-2147483646, the standard documented value —
        confirmed live) — the one AD group kind every other write/read in this file already assumes (plain
        distribution groups have no real membership-governance meaning here)."""
        connection = self._bound_connection_for_write()
        try:
            sam = _sam_account_name(request.mail_nickname or request.display_name)
            conflict_filter = f"(&{_GROUP_FILTER}(|(sAMAccountName={escape_filter_chars(sam)})(cn={escape_filter_chars(request.display_name.strip())})))"
            if self._resolve_dn(connection, conflict_filter) is not None:
                raise ProviderConflictError(f"A group named {request.display_name} already exists in Active Directory.")
            group_dn = f"CN={escape_rdn(request.display_name.strip() or sam)},CN=Users,{self._base_dn()}"
            attrs: dict[str, Any] = {"objectClass": "group", "sAMAccountName": sam, "groupType": GROUP_TYPE_GLOBAL_SECURITY}
            if request.description:
                attrs["description"] = request.description
            if not connection.add(group_dn, attributes=attrs):
                if connection.result.get("description") == "entryAlreadyExists":
                    raise ProviderConflictError(f"A group named {request.display_name} already exists in Active Directory.")
                raise GraphError("PROVIDER_UPDATE_FAILED", f"The directory rejected this group creation: {connection.result.get('description')}", 502)
            connection.search(group_dn, _GROUP_FILTER, BASE, attributes=_GROUP_ATTRIBUTES)
            return _group_from_entry(connection.entries[0])
        finally:
            connection.unbind()

    async def get_domains(self) -> list[NormalizedDomain]:
        return await self._not_implemented()
