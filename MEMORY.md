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
