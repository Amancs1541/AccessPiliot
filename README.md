# AccessPilot

AccessPilot is an Identity Governance & Administration (IGA) console for Microsoft Entra ID — a custom, self-hosted alternative to Entra PIM with its own onboarding pipeline, access-package catalog, birthright policy engine, and portal-level authentication (including a break-glass recovery path independent of the tenant it manages).

- **Backend**: FastAPI + async SQLAlchemy + PostgreSQL, talking to Microsoft Graph via application (client-credentials) permissions.
- **Frontend**: React + TypeScript + Vite, MSAL for Entra sign-in.
- **No queue/cache infra** — background work runs as in-process `asyncio` workers (no Celery/Redis), and there are zero new frontend dependencies beyond what shipped on day one (charts, blur effects, etc. are all hand-rolled).

---

## Getting started

### Backend
```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # set DATABASE_URL (Postgres) and Entra app registration values
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```
> Run **without** `--reload` — on this project's Windows setup, uvicorn's auto-reload has re-launched under the wrong Python interpreter and silently served stale code. Restart manually after backend changes.

### Frontend
```powershell
npm install
npm run dev
```
Create `.env.local` at the repo root with your own Entra app registration values:
```
VITE_ENTRA_TENANT_ID=...
VITE_ENTRA_CLIENT_ID=...
VITE_ENTRA_REDIRECT_URI=http://localhost:5173/
VITE_ACCESSPILOT_API_SCOPE=api://<api-app-id>/access_as_user
VITE_API_BASE_URL=http://localhost:8001
```
If these are left unset, the app falls back to a local mock-data mode with a role switcher (no real Entra login) — or, once an Admin has activated a portal IDP configuration via the Security/setup flow, it can pick that up dynamically from the backend instead.
Vite hot-reloads frontend changes; the backend does not.

### Tests
```powershell
cd backend
.venv\Scripts\pytest
```

---

## Complete function reference — every page, every button

This is an exhaustive, page-by-page list of what AccessPilot can do today. "✅ Real" means backed by a live database/Graph call; "🎭 Mock" means the page renders but isn't wired to a real backend concept yet.

### End-user pages

**Dashboard** (`/dashboard`) — 🎭 Mock for end users; stat cards render placeholder values. (The **Admin** Dashboard is fully real — see below.)

**My Access** (`/my-access`) — ✅ Real
- "Eligible access" section: every assignment you hold that isn't active yet (direct, approved, package-sourced, or birthright-granted).
- **Activate** any eligible item yourself, choosing a duration up to the Admin-configured cap, with a required justification.
- "Active access" section: everything currently real, with live remaining time.
- **Deactivate** your own active access early — it returns to Eligible (not revoked), so you can reactivate later with no new approval needed.
- Package items are grouped into one row with one "Activate all" / "Deactivate all" button instead of one row per item.
- A manual refresh button.

**Request Access** (`/request-access`) — 🎭 Mock (not wired to a backend; superseded in practice by Request Packages).

**Request Packages** (`/request-packages`) — ✅ Real
- Lists every Access Package you're eligible for — because you were named individually, or because you're a member of an eligible group.
- **Request** a package for yourself with a required justification; it lands Eligible under My Access (or Active immediately if the package has no approver).
- Shows your own request history, including rejections.

**My Requests** (`/my-requests`) — ✅ Real
- Your own request history across both individual assignments and package requests, with live status.
- "New request" links to Request Packages.

**Approvals** (`/approvals`) — ✅ Real, and available to **any** authenticated user designated as an approver, not just Admins
- Lists every pending request where you are the (or the fallback) approver.
- **Approve** (prompts for a required justification) or **Reject**.
- Package-sourced requests are grouped into one batch with one Approve/Reject action.
- Fallback approvers can only act after the package's configured wait window has elapsed if the primary hasn't responded yet (unless you're an Admin, who can always act immediately).

**Profile** (`/profile`) — shows your signed-in account details.

### Admin pages

**Dashboard** (`/dashboard`, Admin view) — ✅ Real, auto-refreshes every 30 seconds
- Live counts: total users, groups, directory roles, privileged roles, active JIT sessions, pending requests, access expiring within 24 hours, connected-provider health and last sync time.
- Every stat card is a link that deep-links into the matching admin list page **pre-filtered** (e.g. clicking "Expiring access" opens Assignments already filtered to `status=ACTIVE&expiring=24h`) — the same filters work identically if you type the URL directly.
- A line chart of privileged-role activations over the last 30 days (hover for exact counts per day).
- A donut chart of users split by Permanent+Active vs. Eligible-only access — click a slice (or its legend) to open an overlay listing the actual users in that segment.
- "Recent access requests" / activity feed pulled from the real audit log.

**Users** (`/admin/users`) — ✅ Real
- List with live search and status filter.
- **Source** column/badge: shows whether an identity came from Entra sync or CSV onboarding, and its employee ID/connector detail.
- **Add user** directly (with duplicate-email detection).
- Click through to **User Detail**: identity source panel (onboarded-via, employee ID, connector, a warning badge if the identity has no real Entra account yet); access summary split into four boxes — Groups, Applications, Roles, Access Packages — each showing both AccessPilot-managed items and items **added directly in Entra** (previously invisible, now surfaced); a live Microsoft 365 license lookup; copy-to-clipboard email.

**Groups** (`/admin/groups`) — ✅ Real: list with search + "privileged" filter, synced live from Entra.

**Roles** (`/admin/roles`) — ✅ Real: list with search + "privileged" filter, synced live from Entra.

**Access Requests** (`/admin/access-requests`) — 🎭 Mock (never wired to a real backend concept — use Assignments for real pending requests).

**Assignments** (`/admin/assignments`) — ✅ Real
- **Add assignment**: target a Group, Directory Role, Enterprise Application role, or Access Package; choose Permanent or time-bound; optionally require an approver + fallback approver; or check **"Assign immediately"** to bypass eligibility entirely and grant real access the instant you submit (mutually exclusive with routing through an approver).
- Search + status filter + an "expiring within 24h" toggle.
- **Approve** / **Reject** pending requests (single item or a whole package batch at once), each requiring a justification.
- **Revoke** — Admin-only universal override that terminates an assignment in *any* status (eligible, pending, scheduled, active) with mandatory justification; works on whole package batches too.

**Access Packages** (`/admin/access-packages`) — ✅ Real
- **Add package**: name, items (Groups/Roles/Application roles), approver, fallback approver + escalation wait time, and exactly who may self-request it — all in one creation form.
- **Edit**: rename, redescribe, replace the item list (existing assignment history is never retroactively touched).
- **Eligibility**: separately editable after creation — add/remove individual users and/or whole groups who may self-request; the picker prevents choosing a duplicate. The "Requestable by" count in the table is clickable, opening an overlay with every eligible person/group listed by name.
- **Assign**: push a package to one user, or fan it out to every current member of a group, in one action.
- **Delete**: hard-deletes if the package was never assigned to anyone; otherwise archives it so history stays intact.

**Policies** (`/admin/policies`) — Partially real
- ✅ **Self-activation cap**: the global maximum duration (in hours) any user can self-activate access for.
- ✅ **Birthright policies**: create/enable/disable/delete rules of the form `department` or `job_title` equals a value → grant a Group/Role/Application. Automatically evaluated on every CSV onboarding commit (new joiners and movers) and available as a one-off manual "evaluate" action for any already-synced identity.
- 🎭 The rest of this page's policy table is still mock data.

**Audit Logs** (`/admin/audit`) — ✅ Real: every consequential action in the system, search + result filter, resolves and shows the actual target user (not just who performed the action).

**Providers** (`/admin/providers`) — ✅ Real
- Configure the Entra (or mock) connector: credentials, sync interval.
- **Provisioning mapping**: fetch the tenant's real verified domains and pick one to use for real account creation; configure a username-naming-convention template (`{first}.{last}`, `{f}{last}`, etc.) with a live preview — both optional, and provisioning behaves exactly as before if left unset.

**Sync** (`/admin/sync`) — ✅ Real: manual "Sync now" button, sync run history/status, schedule configuration (also drives the automatic recurring background sync).

**Onboarding** (`/admin/onboarding`) — ✅ Real
- Upload a CSV (client-side file read, no server-side file storage needed).
- **Validate**: required-column check, per-row validation (bad email, invalid status, duplicate employee ID) — one bad row never blocks the rest.
- **Preview**: every row's resulting action — Create / Update / No-change / Disable / Error — before anything is written.
- **Commit**: creates/updates local identities, tries real Entra account provisioning for new joiners, evaluates birthright policy, disables and fully revokes access for terminated rows (a real "leaver" flow), and reports counts (accounts provisioned, birthright grants made, access revoked).
- Import history list, plus a quick-reference panel pointing at the live interactive API docs.

**Security** (`/admin/security`) — ✅ Real, new
- Toggle and configure **screen blur** after N minutes of inactivity (any activity dismisses it).
- Toggle and configure a **click-to-continue lock screen** after a separately-configurable N minutes — never signs the user out, only an explicit click clears it.
- Applies to every signed-in user, Admin and end-user alike.

**Branding** (`/admin/branding`) — ✅ Real, new
- Upload a custom logo for the public **sign-in screen**.
- Upload a separate custom logo for the **internal sidebar**.
- Customize the "Powered by" attribution text shown on both.
- Live before/after previews against both a light and a dark background; resets to the bundled default per-field if cleared.

### System-level capabilities (not a nav page)

- **First-time portal setup wizard**: on a fresh install with no sign-in IDP configured, a one-time randomly-generated bootstrap credential (shown once in the server's startup log) logs into a wizard that can reach *nothing else* — it captures the real Entra/Okta configuration and a Break-Glass recovery account together, proves the configuration works with a real interactive test login, then activates everything and permanently deletes the bootstrap credential.
- **Break-Glass emergency access**: invisible during normal operation (no mention of it anywhere in the normal UI); reachable only via a hidden URL whose secret token is generated exclusively by a console command (`python -m app.cli emergency-url`). Logging in lands in a role that can do exactly two things — fix a broken IDP configuration or rotate its own password — with full Admin access requiring one further explicit "Enter Admin Console" click.
- **Dynamic sign-in configuration**: once a portal IDP is activated through the wizard, real end-user login can be driven entirely from the database with no frontend rebuild required.
- **Idle blur/lock enforcement**: runs continuously in the background of every authenticated session, per the Security page's configuration.

---

## Feature tour (narrative summary)

### 1. Real Microsoft Entra directory integration
- Real user/group/role/application sync via Microsoft Graph (application permissions, not delegated).
- Recurring background sync worker (configurable interval) plus a manual "Sync now" button — both call the same sync routine.
- Create users/groups directly from AccessPilot with duplicate detection.
- Detects and reconciles access **removed outside AccessPilot** (e.g. directly in the Entra portal) — a stale `ACTIVE` assignment is automatically revoked if the real membership disappears.
- Surfaces access **granted outside AccessPilot** too — the User Detail page shows both AccessPilot-managed and "Added directly in Entra" items side by side.

### 2. Custom PIM — Eligible → Activate model
Nothing (a direct assignment, an approved request, a package item, or a birthright grant) becomes *real* Entra access the instant it's created. Everything lands **ELIGIBLE** first; the target user (or an Admin on their behalf) has to explicitly **activate** it for a duration they choose, capped by an Admin-configured maximum. This mirrors Entra PIM's eligible/active split, implemented independently:
- `POST /assignments/{id}/activate` — self-service activation with a chosen duration and a required justification.
- `POST /assignments/{id}/deactivate` — end early; returns to `ELIGIBLE` (not revoked) so it can be reactivated later with no new approval.
- Real access always **supersedes** any existing assignment to the same exact target, but only at the moment it actually becomes real (activation/approval), never at request time — a pending request never disturbs access you already hold.
- Approval is a separate, independent gate: an assignment can require an approver, but approval alone never grants real access either — it only flips the assignment to `ELIGIBLE`, still requiring self-activation afterward.
- Admin **"Assign immediately"** bypass checkbox: grants real access the instant an Admin submits, skipping eligibility entirely (mutually exclusive with routing through an approver). Only an Admin can later deactivate a bypassed grant.
- Admin **Revoke** — a universal override that terminates an assignment in *any* status (eligible, pending, scheduled, or active) with mandatory justification; there's no path back except a new assignment.
- Supports three target types with identical workflow: Entra **Groups**, **Directory Roles**, and **Enterprise Application** roles.
- Non-Admin users can be designated **approvers** on individual assignments — object-level authorization, not role-gated.
- Optional **fallback approver** per Access Package, with a configurable time-gated escalation window (the fallback can only act after the primary hasn't responded for N hours).
- Mandatory, validated justification at every consequential step: creating, approving, and self-activating.

### 3. Access Packages
Bundle several Group/Role/Application-role items into one named package and manage them as a unit:
- Assign a whole package to a user (or fan out to every member of a group) in one action — same eligible/activate workflow as an individual item, per item.
- **Eligibility**: name exactly which users and/or groups may self-request a package; end users see what they're eligible for under **Request Packages** and can request it themselves.
- One combined setup flow at creation time: name, items, approver, fallback approver, escalation window, and who-can-request all in a single form.
- Smart delete: hard-deletes a package with no assignment history, otherwise archives it so audit history stays intact.
- My Access groups same-package batches into one row with one Activate/Deactivate-all button, instead of one row per item.
- The eligibility editor's target picker filters out already-chosen users/groups to prevent an accidental duplicate (which used to silently corrupt the whole save due to a database uniqueness constraint) — the table's "Requestable by" count is clickable, opening an overlay listing every eligible person/group by name.

### 4. Admin Dashboard
Real-time (30s auto-refresh) metrics, all backed by live queries, not mocks:
- User/group/role counts, privileged role count, provider health.
- Active JIT sessions, pending requests, access expiring within 24 hours.
- A hand-rolled SVG line chart of privileged-role activations over the last 30 days.
- A donut chart of users by Permanent+Active vs. Eligible-only access, with clickable slices opening the real member list in an overlay.
- Every stat card deep-links into the relevant admin list page, **pre-filtered** via URL query params (e.g. `?status=ACTIVE`) — filters work identically whether you clicked a card or typed the URL directly.
- Real, URL-driven search/status filtering across every admin list page (Users, Groups, Roles, Assignments, Access Packages, Audit Logs, Sync history).

### 5. Audit logging
- Real audit trail for every consequential action (create/approve/activate/deactivate/revoke/reject, package operations, onboarding commits, portal-auth changes, security/branding changes).
- Every entry resolves and displays the actual target user, not just the actor.

### 6. Governance policies
- **Birthright policy engine**: rules of the shape `department | job_title == value → Group/Role/Application`. New joiners (or anyone whose attributes change) are automatically evaluated against every active rule and granted a matching (eligible, self-activatable) assignment — idempotent, never duplicates.
- Admin-configurable global self-activation duration cap, living on the Policies page.

### 7. IGA onboarding pipeline (CSV-driven joiner/mover/leaver)
- Upload a CSV (validated: required columns, per-row error isolation — one bad row never blocks the rest) and preview exactly what will happen (Create/Update/No-change/Disable/Error) before committing.
- **Joiner**: a new CSV row creates a local identity, tries to provision a **real** Entra account (reusing the same connector path as the "Add user" admin feature), and — if provisioning succeeds — grants matching birthright access for real (not just eligible) on day one.
- **One identity per person, provider-independent**: keyed by employee ID rather than the connector's own object ID, so the same person is found on every re-upload regardless of whether they're still on the CSV-only fallback or have since graduated to a real provisioned account — no duplicate rows, ever, and assignment history survives the transition.
- **Mover**: attribute changes (e.g. department) are re-evaluated against birthright policy on every commit.
- **Leaver**: a `TERMINATED` row disables the identity and automatically revokes every one of their assignments, in any status, with real Entra access removal where applicable.
- **Attribute-mapping engine**: an Admin can pick a verified tenant domain and a configurable username-naming-convention template (`{first}.{last}`, `{f}{last}`, etc.) that overrides the CSV's own email domain/local-part when provisioning real accounts — with a live preview and a safe fallback to the CSV's own values if unconfigured.
- Full admin UI (`/admin/onboarding`): upload, preview, commit, history — plus a reference panel pointing at the live Swagger docs.

### 8. Portal authentication (separate from the Entra connector above)
This is a genuinely distinct system from the HR-sync/provisioning connector above — it governs **who can log into AccessPilot itself**, not what access AccessPilot's users have in the company's tenant.

- **First-time setup wizard**: a fresh install with no IDP configured logs in via a randomly-generated, one-time bootstrap credential (printed once to the server's startup log), which can *only* reach the setup flow. Setup captures the real IDP (Entra/Okta) config and a Break-Glass recovery account together; saving triggers a real interactive test login through the chosen IDP, which is independently re-validated server-side before anything activates. On success, the bootstrap credential permanently self-destructs and Break-Glass activates.
- **Dynamic IDP configuration**: once activated, real end-user sign-in can be driven entirely from the database instead of requiring a frontend rebuild — the app only falls back to this when no build-time Entra environment variables are present, so an existing deployment is provably unaffected.
- **Break-Glass emergency access** — designed to be effectively invisible during normal operation:
  - No mention of it anywhere on the public sign-in screen; an IDP outage there shows only a generic "identity provider unavailable" notice.
  - Reachable *only* via a hidden `/emergency-access/:token` URL, whose secret token is generated exclusively by a console command (`python -m app.cli emergency-url`) — never by any UI or API. A wrong or guessed token renders a page that is byte-for-byte identical to a genuinely nonexistent route.
  - The emergency URL's token is a real second authentication factor on top of username/password, not just a UI gate.
  - Logging in lands in a deliberately narrow **`BreakGlassAdmin`** role that can do exactly two things — fix the broken IDP configuration, or rotate its own password — enforced server-side, not just hidden in the UI.
  - Reaching full Admin access requires an explicit, separate "Enter Admin Console" click (a single confirm, no session logout/relogin) — there is no such thing as an *accidental* full-admin Break-Glass session.

### 9. Security settings — idle blur & lock (admin-configurable)
Applies to every signed-in user, Admin and end-user alike:
- **Blur**: after N minutes of inactivity, a blurred overlay appears; any activity dismisses it instantly.
- **Lock**: after a separate, independently configurable N minutes, an opaque click-to-continue screen appears. It never signs the user out — only an explicit "Continue" click clears it, not mere mouse movement.
- Both behaviors are off by default and independently toggleable, configured from a dedicated **Security** admin page.

### 10. Branding (admin-configurable, stored in the database)
- Upload a **separate logo** for the public sign-in screen and for the app's internal sidebar, plus customize the "Powered by" attribution text — all from a dedicated **Branding** admin page with live previews.
- Fully public read endpoint (no login required) since the sign-in screen needs its logo before anyone has authenticated; only Admins can change it.
- Until customized, everything renders with the bundled default logo/text — zero risk to an existing deployment.
- Uploads are validated server-side (PNG/JPEG/GIF/WEBP only, ~2MB cap; SVG deliberately rejected since it can carry embedded scripts).

---

## Architecture notes

- **Provider abstraction**: a single `IdentityProvider` connector interface (`create_user`, `add/remove member`, `activate/revoke assignment`, `get_domains`, …) is implemented by a real `EntraProvider` (Microsoft Graph) and a `MockProvider` (tests/dev) — every core table already carries a `provider_id`, so the schema has been multi-provider-ready from day one even though only Entra is wired up today.
- **Two separate identity concerns, deliberately not conflated**: `identity_providers` (the HR-sync/provisioning connector — what access a company employee has) vs. the portal-authentication tables (`portal_auth_configs`, `breakglass_accounts`, `bootstrap_credentials` — who can log into AccessPilot's own console).
- **Deferred-supersede model**: creating a request never touches existing access; only the moment access *actually becomes real* (activation, approval-then-activation, or an explicit bypass) does AccessPilot supersede a prior grant to the same exact target.
- **Zero-new-dependency discipline**: charts are hand-rolled inline SVG, CSV uploads are plain JSON bodies (no `python-multipart`), password hashing is PBKDF2 via the Python standard library only, logo uploads are base64 data URIs stored directly in Postgres — nothing in this feature list added a new runtime dependency unless explicitly necessary.

## Known limitations

- No real Entra **account deletion** — a leaver flow disables the local record and revokes AccessPilot-tracked assignments, but a real Entra account must still be removed directly in the tenant.
- Okta is modeled throughout (schemas, `idp_type` branching) but has no real client-side login flow yet — both the setup wizard and the emergency-access page fall back to manually pasting a token for Okta.
- The formal compliance-policy engine (`Policy`/`PolicyTarget` — max duration/MFA/approval rules) is scaffolded but not enforced anywhere yet; birthright policy is a separate, newer concept that *is* fully wired up.
- `DELETE /packages/{id}` fails on a package that still has items attached (a cleanup-ordering bug, not yet fixed).
- The admin "Access Requests" page and the Policies page's main table are still mock data — never wired to a real backend concept.
