from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.config import get_settings
from app.core.errors import AccessPilotError

logger = logging.getLogger("accesspilot.auth")

VALID_ROLES = {"AccessPilot.User", "AccessPilot.Admin"}
PERMISSIONS = {
    "AccessPilot.User": {"ME_READ", "DASHBOARD_USER_READ", "ACCESS_REQUEST_CREATE", "ACCESS_REQUEST_READ_SELF", "ACCESS_REQUEST_CANCEL_SELF", "ASSIGNMENT_READ_SELF", "ASSIGNMENT_ACTIVATE_SELF", "ASSIGNMENT_REVOKE_SELF"},
    "AccessPilot.Admin": {"ME_READ", "DASHBOARD_ADMIN_READ", "USER_READ", "GROUP_READ", "GROUP_MANAGE", "ROLE_READ", "ROLE_MANAGE", "PROVIDER_READ", "PROVIDER_MANAGE", "PROVIDER_SYNC", "ACCESS_REQUEST_READ", "ACCESS_REQUEST_APPROVE", "ACCESS_REQUEST_REJECT", "ACCESS_REQUEST_CANCEL", "ASSIGNMENT_READ", "ASSIGNMENT_CREATE", "ASSIGNMENT_REVOKE", "ASSIGNMENT_EXTEND", "POLICY_READ", "POLICY_CREATE", "POLICY_UPDATE", "POLICY_DELETE", "AUDIT_READ", "SYNC_READ"},
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


def decode_access_token(token: str, *, request_id: str = "-") -> AuthenticatedUser:
    settings = get_settings()
    jwks_loaded = False
    try:
        issuer = _issuer()
        signing_key = PyJWKClient(_jwks_url()).get_signing_key_from_jwt(token).key
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


async def require_authenticated_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> AuthenticatedUser:
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
    user = decode_access_token(credentials.credentials, request_id=getattr(request.state, "request_id", "-"))
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
