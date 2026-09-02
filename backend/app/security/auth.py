from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AccessPilotError
from app.db.session import get_db

logger = logging.getLogger("accesspilot.auth")

_jwks_client: Optional[PyJWKClient] = None
_jwks_client_url: Optional[str] = None


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    """A single, reused PyJWKClient — NOT a fresh one per request. PyJWKClient's key fetch is a SYNCHRONOUS,
    blocking HTTP call (PyJWT has no async client); constructing a new one per request meant every authenticated
    request re-fetched Microsoft's JWKS endpoint synchronously, freezing the entire single-threaded event loop —
    including totally unrelated requests — for as long as that call took. Reusing one instance lets its own
    internal cache absorb almost all requests; the remaining (rare) network call is additionally offloaded to a
    thread in decode_access_token() via asyncio.to_thread so a slow/unresponsive JWKS fetch can never block the
    loop, only the one request that triggered it."""
    global _jwks_client, _jwks_client_url
    if _jwks_client is None or _jwks_client_url != jwks_url:
        _jwks_client = PyJWKClient(jwks_url)
        _jwks_client_url = jwks_url
    return _jwks_client

VALID_ROLES = {"AccessPilot.User", "AccessPilot.Admin", "AccessPilot.BreakGlassAdmin", "AccessPilot.SoDAdmin"}
PERMISSIONS = {
    "AccessPilot.User": {"ME_READ", "DASHBOARD_USER_READ", "ACCESS_REQUEST_CREATE", "ACCESS_REQUEST_READ_SELF", "ACCESS_REQUEST_CANCEL_SELF", "ASSIGNMENT_READ_SELF", "ASSIGNMENT_ACTIVATE_SELF", "ASSIGNMENT_REVOKE_SELF"},
    # SOD_READ (oversight) and SOD_ADMIN_ASSIGN (grant/revoke the AccessPilot.SoDAdmin flag on other users) are
    # deliberately here, but SOD_MANAGE (create/edit/disable the actual SoD rules) is NOT — a genuine separation
    # of duties on the SoD engine itself: a plain Admin can see violations and decide who governs the rules, but
    # cannot rig the rules to clear their own conflicts. See AccessPilot.SoDAdmin below.
    "AccessPilot.Admin": {"ME_READ", "DASHBOARD_ADMIN_READ", "USER_READ", "GROUP_READ", "GROUP_MANAGE", "ROLE_READ", "ROLE_MANAGE", "PROVIDER_READ", "PROVIDER_MANAGE", "PROVIDER_SYNC", "ACCESS_REQUEST_READ", "ACCESS_REQUEST_APPROVE", "ACCESS_REQUEST_REJECT", "ACCESS_REQUEST_CANCEL", "ASSIGNMENT_READ", "ASSIGNMENT_CREATE", "ASSIGNMENT_REVOKE", "ASSIGNMENT_EXTEND", "POLICY_READ", "POLICY_CREATE", "POLICY_UPDATE", "POLICY_DELETE", "AUDIT_READ", "SYNC_READ", "PACKAGE_READ", "PACKAGE_MANAGE", "ONBOARDING_READ", "ONBOARDING_MANAGE", "SECURITY_SETTINGS_MANAGE", "BRANDING_MANAGE", "SOD_READ", "SOD_ADMIN_ASSIGN"},
    # Deliberately narrow: the default landing role for the hidden /emergency-access/:token flow. Can see NOTHING
    # else in the app — no users/groups/roles/assignments/etc. — until the holder explicitly elevates to full
    # AccessPilot.Admin via POST /auth/breakglass-elevate (see _authenticate_via_portal_config_or_breakglass below).
    "AccessPilot.BreakGlassAdmin": {"ME_READ", "PORTAL_AUTH_MANAGE", "BREAKGLASS_CREDENTIAL_MANAGE"},
    # NOT sourced from an Entra App Role (unlike every other role here) — this app has no Application.ReadWrite.All
    # grant, so it can't create/assign Entra App Roles itself. Instead this is a DB-driven flag (see
    # app.services.sod.is_sod_admin, folded into the caller's effective roles in require_authenticated_user
    # below) that a plain AccessPilot.Admin grants/revokes on any real directory user from inside the app
    # (SOD_ADMIN_ASSIGN) — fully dynamic, no Entra portal access needed. Narrowly scoped to SoD governance only,
    # same "small and specific" spirit as AccessPilot.BreakGlassAdmin.
    # GROUP_READ/ROLE_READ (the latter also gates GET /applications, see directory.py)/PACKAGE_READ are read-only
    # additions so SoDAdmin can actually reference real groups/roles/applications/packages when building a rule —
    # it still cannot MANAGE any of them (create/edit/delete), only SOD_MANAGE lets it touch SoD rules themselves.
    "AccessPilot.SoDAdmin": {"ME_READ", "DASHBOARD_USER_READ", "SOD_READ", "SOD_MANAGE", "GROUP_READ", "ROLE_READ", "PACKAGE_READ"},
}

@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    display_name: str
    email: Optional[str]
    tenant_id: str
    roles: tuple[str, ...]
    claims: dict

    @property
    def directory_object_id(self) -> str:
        """The actual Entra/Graph object ID (the `oid` claim) — matches `users.external_id` from directory sync.

        `subject` (the `sub` claim) is a per-application pairwise identifier and does NOT match the Graph object ID;
        never use it to correlate the signed-in actor against synced directory rows.
        """
        return str(self.claims.get("oid") or self.subject)

bearer = HTTPBearer(auto_error=False)


def _auth_error(code: str, message: str) -> AccessPilotError:
    return AccessPilotError(code, message, status_code=401)


def _issuer() -> str:
    settings = get_settings()
    if settings.entra_token_issuer:
        return settings.entra_token_issuer
    if settings.entra_authority:
        return settings.entra_authority.rstrip("/") + "/v2.0"
    raise _auth_error("INVALID_ISSUER", "Token issuer is not configured.")


def _jwks_url() -> str:
    authority = get_settings().entra_authority
    if authority:
        return authority.rstrip("/") + "/discovery/v2.0/keys"
    raise _auth_error("INVALID_ISSUER", "Token authority is not configured.")


def _audience() -> str:
    audience = get_settings().entra_api_audience or get_settings().entra_api_client_id
    if not audience:
        raise _auth_error("INVALID_AUDIENCE", "API audience is not configured.")
    return audience


def _log_jwt_validation_failure(token: str, *, request_id: str, failure_code: str, failure_reason: str, jwks_loaded: bool) -> None:
    settings = get_settings()
    if settings.environment != "development":
        return
    try:
        unverified_claims = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    except Exception:
        unverified_claims = {}
    logger.info(
        "JWT DEBUG\n"
        "token_received: true\n"
        "jwks_loaded: %s\n"
        "validation: FAIL\n"
        "failure_code: %s\n"
        "failure_reason: %s\n"
        "configured_issuer: %s\n"
        "configured_audience: %s\n"
        "token_aud: %s\n"
        "token_iss: %s\n"
        "token_tid: %s\n"
        "token_oid: %s\n"
        "token_scp: %s\n"
        "token_roles: %s",
        jwks_loaded,
        failure_code,
        failure_reason,
        _issuer(),
        _audience(),
        unverified_claims.get("aud"),
        unverified_claims.get("iss"),
        unverified_claims.get("tid"),
        unverified_claims.get("oid"),
        unverified_claims.get("scp"),
        unverified_claims.get("roles", []),
        extra={"request_id": request_id},
    )


async def decode_access_token(token: str, *, request_id: str = "-") -> AuthenticatedUser:
    settings = get_settings()
    jwks_loaded = False
    try:
        issuer = _issuer()
        jwks_client = _get_jwks_client(_jwks_url())
        # Hard backstop: PyJWKClient's own `timeout` guards a single socket read, not the whole call (redirects,
        # DNS, retries aren't covered) — wait_for guarantees this can NEVER hang the request indefinitely
        # regardless of the underlying cause, so one bad request degrades to a clean 401, not a stuck connection.
        signing_key = (await asyncio.wait_for(asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token), timeout=10)).key
        jwks_loaded = True
        claims = jwt.decode(token, signing_key, algorithms=["RS256"], audience=_audience(), issuer=issuer, options={"require": ["exp", "iss", "aud", "tid", "sub"]})
    except jwt.ExpiredSignatureError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="TOKEN_EXPIRED", failure_reason="expiry", jwks_loaded=jwks_loaded)
        raise _auth_error("TOKEN_EXPIRED", "The access token has expired.") from exc
    except jwt.InvalidAudienceError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_AUDIENCE", failure_reason="audience", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_AUDIENCE", "The access token is not intended for this API.") from exc
    except jwt.InvalidIssuerError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_ISSUER", failure_reason="issuer", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_ISSUER", "The access token issuer is invalid.") from exc
    except jwt.MissingRequiredClaimError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_TOKEN", failure_reason="required_claims", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_TOKEN", "The access token is missing required claims.") from exc
    except jwt.InvalidSignatureError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_TOKEN", failure_reason="signature", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_TOKEN", "The access token signature is invalid.") from exc
    except jwt.InvalidTokenError as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_TOKEN", failure_reason="token", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_TOKEN", "The access token is invalid.") from exc
    except AccessPilotError:
        raise
    except Exception as exc:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_TOKEN", failure_reason="jwks_or_validation", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_TOKEN", "The access token could not be validated.") from exc
    if claims["tid"] != settings.entra_tenant_id:
        _log_jwt_validation_failure(token, request_id=request_id, failure_code="INVALID_TENANT", failure_reason="tenant", jwks_loaded=jwks_loaded)
        raise _auth_error("INVALID_TENANT", "The access token tenant is invalid.")
    if settings.environment == "development":
        logger.info(
            "JWT DEBUG\n"
            "token_received: true\n"
            "jwks_loaded: true\n"
            "signature: PASS\n"
            "issuer: PASS\n"
            "audience: PASS\n"
            "tenant: PASS\n"
            "expiry: PASS\n"
            "required_claims: PASS\n"
            "validation: PASS",
            extra={"request_id": request_id},
        )
    roles = tuple(role for role in claims.get("roles", []) if role in VALID_ROLES)
    if not roles:
        raise _auth_error("INVALID_TOKEN", "The access token has no valid AccessPilot role.")
    return AuthenticatedUser(str(claims["sub"]), claims.get("name", claims["sub"]), claims.get("preferred_username") or claims.get("email"), str(claims["tid"]), roles, claims)


async def _authenticate_via_portal_config_or_breakglass(db: AsyncSession, token: str) -> Optional[AuthenticatedUser]:
    """Called only after the primary env-var Entra path has already failed. Returns None (never raises) if
    neither fallback accepts the token, so the caller can re-raise the ORIGINAL primary-path error — keeping
    error messages/codes unchanged for a deployment where this new setup flow was never used."""
    from app.services.portal_auth import decode_breakglass_token, get_active_portal_auth_config, validate_token_against_config

    active_config = await get_active_portal_auth_config(db)
    if active_config is not None:
        try:
            claims = await validate_token_against_config(token, active_config)
        except AccessPilotError:
            claims = None
        if claims is not None:
            # NOTE: this "roles" claim shape is what Entra provides today; an actual Okta login flow (not yet
            # built — see project memory's Phase 12 "next steps") may need its own role-extraction logic here
            # once that connector type is real, rather than assuming Entra's exact claim shape generalizes.
            roles = tuple(role for role in claims.get("roles", []) if role in VALID_ROLES)
            if roles:
                tenant = str(claims.get("tid") or active_config.tenant_id or "")
                return AuthenticatedUser(str(claims["sub"]), claims.get("name", claims["sub"]), claims.get("preferred_username") or claims.get("email"), tenant, roles, claims)

    result = await decode_breakglass_token(db, token)
    if result is not None:
        account, elevated = result
        role = "AccessPilot.Admin" if elevated else "AccessPilot.BreakGlassAdmin"
        return AuthenticatedUser(f"breakglass:{account.id}", f"Break-Glass ({account.username})", None, "breakglass", (role,), {"elevated": elevated})
    return None


async def require_authenticated_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: AsyncSession = Depends(get_db)) -> AuthenticatedUser:
    if get_settings().environment == "development" and request.url.path == "/api/v1/me":
        authorization = request.headers.get("authorization")
        logger.info(
            "AUTH HEADER DEBUG\npresent: %s\nscheme: %s",
            authorization is not None,
            authorization.split(" ", 1)[0] if authorization else None,
            extra={"request_id": getattr(request.state, "request_id", "-")},
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _auth_error("AUTHENTICATION_REQUIRED", "Authentication is required.")
    try:
        user = await decode_access_token(credentials.credentials, request_id=getattr(request.state, "request_id", "-"))
    except AccessPilotError as primary_error:
        # Additive fallbacks ONLY — the env-var Entra path above is completely unchanged and always tried first,
        # and its error is what gets raised if every fallback below also fails (preserving today's exact error
        # behavior for a deployment where none of this new setup flow is in play). Tried in order: (1) an active
        # PortalAuthConfig — a deployment that has completed the new setup wizard with its own IDP, which may or
        # may not be the same tenant as the env-var config; (2) a break-glass session — dormant until setup
        # activates it, meant purely for recovering portal access if the configured IDP itself is what's broken.
        user = await _authenticate_via_portal_config_or_breakglass(db, credentials.credentials)
        if user is None:
            raise primary_error
    # AccessPilot.SoDAdmin is DB-driven, not an Entra App Role (see PERMISSIONS above) — fold it into this
    # caller's effective roles if a plain Admin has flagged their real directory record for it. Local import
    # avoids a module-load-time circular import between app.security.auth and app.services.sod.
    if "AccessPilot.SoDAdmin" not in user.roles:
        from app.services.sod import is_sod_admin
        if await is_sod_admin(db, user.directory_object_id):
            user = dataclasses.replace(user, roles=tuple(user.roles) + ("AccessPilot.SoDAdmin",))
    request.state.user = user
    if get_settings().environment == "development" and request.url.path == "/api/v1/me":
        claims = user.claims
        logger.info(
            "TOKEN DEBUG\n"
            "authenticated: true\n"
            "aud: %s\n"
            "iss: %s\n"
            "tid: %s\n"
            "oid: %s\n"
            "preferred_username: %s\n"
            "scp: %s\n"
            "roles: %s",
            claims.get("aud"),
            claims.get("iss"),
            claims.get("tid"),
            claims.get("oid"),
            claims.get("preferred_username"),
            claims.get("scp"),
            claims.get("roles", []),
            extra={"request_id": getattr(request.state, "request_id", "-")},
        )
    return user


def require_permission(permission: str) -> Callable:
    async def dependency(user: AuthenticatedUser = Depends(require_authenticated_user)) -> AuthenticatedUser:
        if not any(permission in PERMISSIONS[role] for role in user.roles):
            raise AccessPilotError("ACCESS_DENIED", "You do not have permission to perform this action.", status_code=403)
        return user
    return dependency
