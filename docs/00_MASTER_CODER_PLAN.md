# AccessPilot V1 — Master Coder Plan

## Purpose

Build AccessPilot as a Microsoft Entra-first Identity Governance and Just-In-Time access platform.

The first release must provide:

- Microsoft Entra authentication
- Admin and Normal User experiences
- Entra users, groups and directory roles visibility
- Group membership management
- JIT access request workflow
- Approval workflow
- Time-bound access
- Automatic expiration
- Policy evaluation
- Audit logging
- Provider synchronization
- PostgreSQL persistence
- Provider abstraction ready for Okta

## Non-negotiable architecture

```text
React SPA
   |
   | MSAL
   v
Microsoft Entra ID
   |
   | access token for AccessPilot API
   v
FastAPI
   |
   +--> Authorization
   +--> Policy Engine
   +--> JIT Engine
   +--> Audit
   +--> Provider Abstraction
   |
   +--> PostgreSQL
   +--> Background Workers
   |
   v
Entra Connector
   |
   v
Microsoft Graph
```

The frontend never directly performs privileged Graph operations.

## Roles

V1 has exactly:

```text
AccessPilot.User
AccessPilot.Admin
```

See `04_FINAL_APP_ROLES.md`.

## Development phases

1. Requirements freeze
2. Repository foundation
3. Entra application setup
4. Authentication and authorization
5. PostgreSQL foundation
6. Provider abstraction
7. Entra read-only connector
8. IAM admin UI
9. Group management
10. JIT request and approval
11. PIM/role assignment integration
12. Expiration workers
13. Policy, audit and reconciliation
14. Security testing and release

## Coding order

Do not start with privileged role writes.

First prove:

```text
Login
 -> API token validation
 -> /me
 -> Graph read
 -> PostgreSQL
 -> React UI
```

Only then enable write permissions.

## Source-of-truth documents

The following documents are contracts, not suggestions:

- Product requirements
- UI/UX specification
- Architecture
- App roles
- Authorization matrix
- Entra setup
- Graph mapping
- Database schema
- API contract
- State machines
- Provider connector contract
- Environment configuration
- Error contract
- Background workers
- Audit logging
- Security threat model
- Test plan
- Deployment

If implementation conflicts with a contract, stop and resolve the conflict before coding.

## Definition of done

V1 is complete only when:

- Authentication works
- Admin/User authorization works
- Users/groups/roles can be read
- Group membership management works
- Access requests work
- Approval works
- JIT activation works where supported by Entra/PIM
- Expiration works
- Audit works
- Policies work
- Sync works
- Security tests pass
- Frontend uses only AccessPilot APIs
- No secrets are exposed
- All privileged operations are server-side
