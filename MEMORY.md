# AccessPilot Project Memory

## Current Phase
Phase 3: Provider configuration management completed.

## Completed Phases
- Frontend V1 mock UI accepted and frozen.
- Backend Foundation V1 implemented.

## Current Architecture
React/Vite frontend; FastAPI backend; async SQLAlchemy/Alembic; provider abstraction; PostgreSQL remains required. No Graph IAM operations in this phase.

## Frontend Status
Existing UI visual/page structure remains frozen. Phase 2 changed only `src/auth.tsx`, `src/main.tsx`, `src/App.tsx`, `src/vite-env.d.ts`, and package dependencies for MSAL, API role resolution, configured-mode loading/sign-in, and mock fallback.

## Previous Phase
PHASE 2 — ENTRA AUTH + ACCESSPILOT AUTHORIZATION.

## Provider Configuration
Phase 3 provider metadata CRUD and connection-test routes plus Providers UI Add/Edit/Save/Test Connection are implemented for `MOCK` and `ENTRA`. Only admins may manage providers. Secrets are represented only by `configuration_ref`; no secret values are modeled or persisted. Mock tests connect successfully. EntraProvider performs real OIDC discovery against the saved authority and validates tenant issuer/JWKS metadata; failures set ERROR and return safe errors. Added migration `0002_provider_meta` for client/authority/audience/scope/redirect metadata.

## Phase 3 Runtime Bug
Previous issue: Provider configuration Save appeared to do nothing. Root cause: the frontend API helper invoked MSAL `acquireTokenSilent` before MSAL initialization and with no account, and the Save handler had no catch; no POST was emitted and the exception was silent. Fix: initialize MSAL before rendering, skip token acquisition only in explicitly unconfigured mock mode, use real form submit semantics and client validation, and surface safe API/auth errors. Validation: browser emitted `POST /api/v1/providers` with the documented payload; backend returned expected 401 without an account; authenticated provider API tests pass.

## Backend Status
Foundation exists under `backend/app`: health, request IDs, error contract, models, migration, provider boundary, mock provider, workers, tests.

## Authentication Status
Phase 2 foundation implemented. Backend validates Entra JWT signatures through OIDC JWKS, issuer, audience, tenant, expiry, required claims, and exact AccessPilot roles. Frontend has MSAL provider, login/logout, silent API-token acquisition, and `/api/v1/me` role resolution. Tokens are not logged or persisted by AccessPilot.

## Entra Configuration
No real tenant/client/audience/scope identifiers are available in the environment. Use blank `.env` placeholders until supplied.

## Architecture Correction
Provider connector configuration is database-backed. PostgreSQL is the source of truth for provider metadata. Environment variables are not the source of truth for provider connector configuration. `test_provider()` loads the selected `identity_providers` row and passes it to `EntraProvider`; AccessPilot authentication bootstrap settings remain separate.
## Database Status
15 documented tables plus provider metadata columns exist. PostgreSQL connection validated with the local configured server and Alembic reached revision `0002_provider_meta`. Do not commit or record the local password.

## API Status
`/health`, `/api/v1/health`, protected `/api/v1/me`, protected `/api/v1/users`, and admin-only provider CRUD/test routes exist. Provider changes emit audit events and use service-layer persistence.

## Tests
Full backend suite: 14 passed with one non-blocking pytest collection warning. Frontend `npm run build` passed with a non-blocking MSAL/provider bundle warning. Provider API tests cover admin CRUD, mock connection, and unauthenticated denial. Backend compileall passed. Real Entra tenant connectivity remains untested because no tenant configuration is available.

## Known Issues
MSAL remains in placeholder/unconfigured mode until real frontend/backend Entra values are supplied. Frontend build reports a non-blocking large-chunk warning due to MSAL/provider bundle size. Providers UI API calls require an authenticated MSAL account when Entra is configured.

## Environment Blockers
Real Entra values and admin consent are unavailable. Local PostgreSQL is now reachable, but production Entra validation remains blocked.

## Files/Directories Created or Changed
Phase 3 changed provider models/schemas/services/routes, `backend/app/security/secrets.py`, migration `backend/alembic/versions/0002_provider_configuration_metadata.py`, dependencies, and provider tests. Frontend provider integration changed `src/ProviderConfiguration.tsx`, `src/App.tsx`, and `src/auth.tsx`. `MEMORY.md` is the continuation state.

## Next Phase
Stop after Phase 3. Do not begin PIM, JIT, Graph IAM, group writes, Okta, approval, or expiration logic.
Final backend suite: 15 passed with one non-blocking pytest collection warning. Frontend `npm run build` passed with a non-blocking MSAL chunk warning. Tests cover DB-backed connector selection, provider CRUD, mock connection, and authorization. Real Entra tenant connectivity remains untested because no tenant configuration is available.
## Important Decisions
Only `AccessPilot.User` and `AccessPilot.Admin` are valid application roles. Backend derives actor identity from validated JWT claims. EntraProvider remains Graph-free.

## Instructions for Next Agent
Read this file first. Do not invent Entra IDs. Keep frontend visual/UI structure unchanged. Run backend and frontend tests/builds and record blockers here.

## Admin Dashboard Runtime Issue
Root cause: Pending live verification. The repository role path is intact. A live `/api/v1/me` request reaches the backend with an Authorization header but returns 401 before post-validation diagnostics. Development-only diagnostics now report the exact validation failure class (JWKS/signature/issuer/audience/tenant/expiry/required claims) and allowlisted unverified claim values for troubleshooting only; they never record a token or header value.

Fix: No authorization behavior changed. Added temporary diagnostics only, enabled exclusively when `ENVIRONMENT=development`.

Validation: Pending a real authenticated `/api/v1/me` request. Frontend production build remains the available local validation; backend test execution is blocked because the checked-in virtual environment references a missing Python 3.9 installation.

## Backend JWT Issuer Fix
Root cause: The backend derived the v2 issuer `https://login.microsoftonline.com/d52cef87-e3e8-4faa-9723-6d961c736349/v2.0`, while the live AccessPilot API token used the configured tenant's v1 issuer `https://sts.windows.net/d52cef87-e3e8-4faa-9723-6d961c736349/`.

Fix: Added explicit `ENTRA_TOKEN_ISSUER` configuration and set it to the tenant-scoped v1 issuer. JWT validation retains exact issuer, audience, tenant, signature, expiry, and required-claim checks. JWKS discovery now uses the configured Microsoft login authority, independently of the token issuer format.

Validation: Backend security suite passed (15 tests) with the configured v1 issuer and wrong-issuer/tenant rejection coverage; frontend production build passed. A live post-restart `/api/v1/me` verification is still required to confirm HTTP 200 and `AccessPilot.Admin` role propagation.

## Phase 3 Live Provider Configuration
Status: Implementation and automated validation passed; live database and Entra discovery validation is pending an authenticated admin form submission.

Implementation: The existing Provider UI uses the existing authenticated provider APIs for load, create, update, and connection-test operations. It now selects only `ENTRA` records, maps edit data to documented writable fields, refreshes database-backed data after mutations, and presents safe save/test feedback without altering the application shell or authentication.

Validation: Backend provider/authentication suite passed (15 tests); frontend production build passed. The existing backend service persists to `identity_providers` and its Entra connector performs tenant OIDC discovery from the saved record.

Database: Pending live UI submission and browser refresh verification.

Connection test: Pending live admin-triggered test; do not claim CONNECTED until Entra OIDC discovery succeeds.

## Phase 4 — Real Entra Directory Integration

Status: PARTIAL. Code, tests, and DB-backed reads/writes are implemented and passing; live Microsoft Graph calls are blocked because no Graph client secret is configured (see Known limitations).

Implemented:
- `EntraProvider` now performs real Microsoft Graph application-permission calls (client-credentials flow via a new `app/providers/graph_client.py`) for `get_users`, `get_user`, `get_groups`, `get_group`, `get_group_members`, `get_roles`, `get_role`, `create_user`, `create_group`, with `@odata.nextLink` pagination and Graph error mapping to the documented stable error codes (401/403/404/409/429/5xx/timeout).
- New `app/services/directory_sync.py`: idempotent sync orchestration keyed on `(provider_id, external_id)` for users/groups/roles, stale group-membership removal on successful re-sync, per-run `sync_runs`/`sync_errors` persistence, and audit events (`SYNC_STARTED/COMPLETED/FAILED`, `USER_SYNCED`, `GROUP_SYNCED`, `GROUP_MEMBERSHIP_SYNCED`, `ROLE_SYNCED`).
- Real endpoints replacing prior stubs: `GET/POST /api/v1/users`, `GET /api/v1/users/{id}`, `GET/POST /api/v1/groups`, `GET /api/v1/groups/{id}`, `GET /api/v1/groups/{id}/members`, `GET /api/v1/roles`, `GET /api/v1/dashboard/admin` (real PostgreSQL counts + provider/last-sync status), `POST /api/v1/providers/{id}/sync`, `GET /api/v1/providers/{id}/sync-runs`.
- Duplicate detection: user create checks Graph for an existing `userPrincipalName` before creating (409 `USER_ALREADY_EXISTS`); group create checks Graph for an existing `displayName` (409 `GROUP_ALREADY_EXISTS`). New users get a server-generated one-time temporary password returned only in the create response, never stored or logged.
- Frontend: Users, Groups, Roles pages and the admin Dashboard now read from the real APIs via `auth.apiRequest` (same pattern as the existing Providers page) with Loading/Empty/Error states and no fake fallback data; Users/Groups pages gained working "Add user"/"Add group" forms; Sync page now shows real `sync_runs` and has a working "Sync now" button. Visual layout/shell/styling unchanged.
- Roles remain strictly read-only; no group-membership write, JIT, PIM, approval, or entitlement code was added, per the Phase 4 boundary.

Graph permissions: User confirmed `User.Read.All`, `Group.Read.All`, `RoleManagement.Read.Directory`, `User.ReadWrite.All`, `Group.ReadWrite.All` are already granted and admin-consented on the Entra API app registration. This was not independently re-verified from this repository/session.

Database: No schema changes were required — all 15 existing tables already supported Phase 4 (confirmed live against the connected PostgreSQL instance). No new Alembic migration was added.

Live validation: NOT performed. `backend/.env`'s `ENTRA_API_CLIENT_SECRET` is empty and `identity_providers.configuration_ref` is `NULL` for the one live ENTRA provider row (verified directly against the database) — the backend cannot obtain a Microsoft Graph application token until a real secret is supplied. All new code paths are covered by tests using mocked Graph HTTP responses (`httpx.MockTransport`) instead. Full backend suite: 49 passed. Frontend `npm run build` passed.

Known limitations:
- Per-user group membership count and last-sign-in are not shown (would require extra Graph permissions/endpoints out of scope for Phase 4); the Users/User-detail pages omit these columns rather than fabricate them.
- `/api/v1/dashboard/user` and JIT-related admin dashboard stat cards (active sessions, pending requests, expiring access, policy coverage) remain explicitly out of scope and show "—" rather than fake numbers.

## Phase 4 — Graph Credential Architecture (supersedes the env-var approach above)

The Graph client secret is no longer read from `ENTRA_API_CLIENT_SECRET`/`.env` for the database-backed flow. `identity_providers` gained `graph_client_id` (plain) and `graph_client_secret_encrypted` (Fernet-encrypted, migration `0003_provider_graph_credentials`) — PostgreSQL is the source of truth, matching the pre-existing architecture decision. A separate, non-secret-specific `PROVIDER_CREDENTIAL_KEY` env var (generated once, stored in `backend/.env`) is the encryption master key protecting that column; it is not itself a Graph credential. `PATCH /api/v1/providers/{id}/credentials` (admin-only) accepts `{graph_client_id?, graph_client_secret}`, encrypts before storing, and never returns the secret in any response — `GET`/`LIST` providers only ever expose `graph_client_id` and a `credential_configured: bool`. `EntraProvider._resolve_secret()` decrypts the DB column first, falling back to the legacy `configuration_ref`/env-var path only for backward compatibility. The Providers UI (`ProviderConfiguration.tsx`) has "Graph Client ID" / "Graph Client Secret" fields wired to this endpoint, showing only Configured/Not configured.

Earlier iterations of this feature (a `POST /{id}/secret` route, then writing to `.env` via `SecretReferenceStore`) were built and then removed during this same phase — the codebase now only contains the credentials-endpoint/DB-encrypted version described above.

## Phase 4 — Provider Reset & delete_provider Bug Fix

Two real, pre-existing bugs were found and fixed while resetting the stale Entra provider record used during Phase 4 testing:
1. `test_provider()`/`EntraProvider.test_connection()`: `_resolve_secret()` returned `""` instead of `None` when no secret was configured anywhere, so the "no secret yet, but OIDC succeeded" short-circuit never fired and a spurious Graph-auth check ran and failed, forcing status to `ERROR` even when Entra connectivity was actually fine. Fixed by returning `None` for the empty case. Verified directly against the real tenant: `test_provider()` now correctly returns `CONNECTED` for OIDC-only verification when no Graph secret is set yet.
2. `delete_provider()` inserted its `PROVIDER_DELETED` audit row (referencing the provider being deleted) in the same transaction as the delete itself, which self-violates the `audit_logs.provider_id` FK on real PostgreSQL (masked by the test suite, which runs on SQLite without FK enforcement by default). Fixed: it now nulls `provider_id` on any pre-existing `audit_logs` rows referencing the provider (rows are preserved, never deleted — only the FK is cleared), deletes dependent `sync_runs`/`sync_errors` (not audit-protected), and only then deletes the provider row and inserts the `PROVIDER_DELETED` audit entry with `provider_id=NULL`/`target_id=<deleted id>`. If the provider still has synced `users`/`groups`/`roles` (a real, NOT-NULL FK with no safe way to null it), deletion now fails cleanly with `PROVIDER_CONFLICT` (409) instead of a raw 500 — this intentionally blocks silently cascading away real synced directory data. Added a regression test (`test_delete_provider_succeeds_with_foreign_keys_enforced_and_dependent_audit_rows`) that enables SQLite FK enforcement specifically to catch this class of bug going forward.

Status:
- Old stale Entra provider (`08814973-0c6b-4652-a05d-0703a52a9314`, mangled through repeated manual test edits) removed from PostgreSQL. Its dependent `sync_runs`/`sync_errors` (4 each, pure test artifacts) were deleted; its 12 `audit_logs` rows were preserved with `provider_id` nulled. `users`/`groups`/`roles` were empty for this provider, so nothing else was affected.
- `identity_providers` table verified empty (0 rows) after cleanup — ready for a fresh Admin-driven creation via the UI.
- Full CRUD + test-connection cycle (create → list → get → patch → test-connection → delete) verified directly against the real PostgreSQL database at the service layer (same functions the API routes call), using the real tenant's public identifiers. `test_provider()` correctly returned `CONNECTED`.
- Multiple-provider architecture unchanged/preserved — `provider_id` continues to select which DB row's configuration `EntraProvider`/`MockProvider` use; nothing was hardcoded to a single global provider.
- Existing authentication (MSAL, JWT validation, Admin authorization) untouched.
- Existing UI design/layout untouched — only the Providers page's credential fields (added earlier this phase) remain.
- Tests: 57/57 backend passing. Frontend `npm run build` passing.

Remaining live-environment blocker: the actual browser walkthrough (login → Providers page → Add Provider → Save → reload → Test Connection → Sync) has NOT been performed — that requires a real interactive login and is the user's next step. Separately, this session repeatedly found that the user's `--reload` backend process gets silently respawned under the wrong Python interpreter on this machine (a Windows venv/uvicorn interaction, not a code bug), freezing it on stale code after any edit; running without `--reload` was recommended as the workaround but not yet confirmed adopted.
