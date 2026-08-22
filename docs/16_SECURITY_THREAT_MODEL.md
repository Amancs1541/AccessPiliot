# AccessPilot V1 — Security Threat Model

## Security objective

Prevent unauthorized identity privilege changes and ensure every privileged operation is controlled and auditable.

## 1. Token theft

Threat:

An attacker obtains a user access token.

Mitigation:

- Short token lifetime controlled by Entra
- HTTPS
- Backend audience validation
- Issuer validation
- Tenant validation
- No token persistence in local database
- Secure frontend storage pattern

## 2. Privilege escalation

Threat:

Normal user attempts an admin API.

Mitigation:

- App-role authorization
- Server-side checks
- Centralized permission dependencies
- Integration tests for every admin endpoint

## 3. IDOR

Threat:

User changes a URL/request ID to access another user's request.

Mitigation:

- Derive actor from token
- Object-level authorization
- Never trust requesterId from client

## 4. Approval bypass

Threat:

User attempts to activate access without approval.

Mitigation:

```text
activate
 -> verify assignment
 -> verify eligibility
 -> evaluate policy
 -> verify approval
 -> provider operation
```

## 5. Duration manipulation

Threat:

Client requests 30 days when policy allows 2 hours.

Mitigation:

- Server calculates maximum
- Server ignores client-side countdown
- Reject excessive duration

## 6. Replay/double click

Threat:

Duplicate privileged operations.

Mitigation:

- Idempotency keys
- State checks
- Database locking

## 7. Provider credential theft

Threat:

Graph application secret is exposed.

Mitigation:

- Backend only
- Secure secret store
- Never frontend
- Never Git
- Never logs

## 8. Graph over-privilege

Threat:

Application has excessive directory permissions.

Mitigation:

- Least privilege
- Staged permission grants
- Permission review before release

## 9. SQL injection

Mitigation:

- SQLAlchemy parameterization
- No string-built SQL
- Input validation

## 10. XSS

Mitigation:

- React escaping
- Avoid dangerous HTML
- Content Security Policy where appropriate

## 11. CSRF

Mitigation:

Use token-based API architecture correctly and avoid unsafe cookie authentication patterns unless CSRF protection is explicitly implemented.

## 12. Tenant isolation

If multi-tenant support is introduced later, every provider operation must be scoped to the correct tenant.

V1 may be single-tenant but the provider model should retain tenant identity.

## 13. Race conditions

Protect:

```text
approve
activate
revoke
extend
expire
```

with transactional state checks.

## 14. Logging leakage

Never log secrets or tokens.

## 15. Security acceptance

No V1 release until:

```text
authentication tests pass
authorization tests pass
IDOR tests pass
JIT bypass tests pass
expiration tests pass
secret scanning passes
dependency scanning passes
```
