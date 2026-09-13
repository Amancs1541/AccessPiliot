# AccessPilot — Complete Feature & Function Catalog

**Purpose of this document**: a single, exhaustive inventory of everything actually built in AccessPilot to date — every feature, every API endpoint, every background job, every small behavior and edge case — organized by functional area. This is a *catalog*, not a design doc: for the full reasoning behind any one area, the numbered docs it points to (`08_DATABASE_SCHEMA.md`, `19_SOD_ENGINE.md`, `20_NOTIFICATIONS.md`, etc.) go deeper. Written from a full read of the codebase and the project's own build history; current as of **2026-09-05**. 33 database migrations applied to date (`0001` → `0033`).

---

## 1. Identity & Directory

- **Users**: list/search (`GET /users`), single user detail (`GET /users/{id}`), create a user directly in AccessPilot (`POST /users`, generates a temporary password) — every user record carries `provider_id`, `external_id`, `employee_id` (nullable, globally unique when set — the cross-connector identity key, see §5), `source` (`CSV_ONBOARDING` or `null` for directory-synced/manual), `department`, `job_title`, `status` (`ACTIVE`/`DISABLED`).
- **Groups**: list/search (`GET /groups`), detail + real member list (`GET /groups/{id}`, `/members`), create (`POST /groups`), `is_privileged` flag.
- **Roles**: list (`GET /roles`) — real Entra directory roles (e.g. Global Administrator), flagged `is_privileged`.
- **Applications**: list (`GET /applications`) — real Entra Enterprise Applications, each carrying its own `app_roles` array (id/name/description) fetched from Graph.
- **User access summary** (`GET /users/{id}/access-summary`): every current assignment (any status) for that user, grouped into Groups / Applications / Roles / Access Packages on the User Detail page, each item tagged with its **source**: `ACCESSPILOT` (AccessPilot granted it) or `DIRECT_IN_ENTRA` (real membership AccessPilot never touched — e.g. added directly in the Entra portal). Also returns a **live** Microsoft 365 license list for the user (`GET /subscribedSkus` resolution for human-readable names, falls back to raw SKU GUIDs if `Organization.Read.All` isn't granted).
- **Direct-in-Entra detection**: Groups compared against the already-synced `UserGroup` table (free, no extra Graph call); Applications compared via a live, on-demand Graph read of the user's real app-role assignments. Both fail open (a Graph error is treated as "nothing found," never blocks the page or crashes) and log a distinct warning line so "checked, found nothing" is distinguishable from "the check itself failed."
- **Local group-membership mirroring**: any real Entra/Graph group grant — through self-activation, admin bypass, or the scheduled-activation worker — immediately upserts a `UserGroup(source="ASSIGNMENT")` row, instead of waiting on the next periodic sync (which could lag by a full sync interval or never run if unscheduled). The matching revoke/deactivate path removes it immediately too. This is what makes group-based Access Package eligibility and SoD's GROUP checks correct in real time.
- **Recurring provider sync**: a background worker (§17) plus a manual "Sync now" button, both calling the same `run_sync()` — pulls users, groups, roles, *and* applications from Entra every time. Sync interval is admin-configurable per provider (`sync_interval_minutes`); a provider with this unset never auto-syncs (was a real, since-fixed gap — see §21).
- **One identity row per person, regardless of source** (see §5 for the full history): `employee_id` is the stable cross-connector key. An identity created by CSV onboarding that later gets a real Entra account **graduates in place** — same `users.id`, provider/external-id fields swapped to the real ones — so any assignment history attached earlier stays attached with zero orphaning.

## 2. Access Assignments — the core PIM engine

AccessPilot's own custom eligible/activate model — **no assignment ever grants real access immediately**, except the explicit admin bypass described below.

- **Assignment targets**: `GROUP`, `ROLE`, or `APPLICATION` (+ a specific `app_role_external_id` on the application) — one generic engine (`create_assignment`/`activate_assignment`/`revoke_assignment` etc. in `assignments.py`) handles all three uniformly.
- **Durations**: `PERMANENT` or `TEMPORARY` (with a real `expiration_time` that the expiration worker enforces — see §17).
- **The eligible/activate lifecycle**:
  - No approver configured → lands `ELIGIBLE` immediately (never auto-activates).
  - Approver configured → lands `PENDING_APPROVAL`; the approver's decision (`POST /assignments/{id}/approve`/`reject`) flips it to `ELIGIBLE` (approval alone **never** grants real access either — deliberate, so an assignment "with an approver in between" behaves identically to one without, once past that gate).
  - `ELIGIBLE` → the target user (or an Admin on their behalf) self-activates (`POST /assignments/{id}/activate`) with a chosen `duration_hours`, capped by the provider's admin-configurable `max_self_activation_hours` (default 8) — this is the one moment real Entra/Graph access is actually granted.
  - **Deactivate** (`POST /assignments/{id}/deactivate`, self-service): ends real access early but returns the assignment to `ELIGIBLE` (not a dead end) — clears `activated_at`/`expiration_time` so it can be re-activated later with no new request or re-approval.
  - **Admin Revoke** (`POST /assignments/{id}/revoke`, Admin-only, universal override): ends an assignment in ANY status (`ELIGIBLE`/`PENDING_APPROVAL`/`SCHEDULED`/`ACTIVE`) and always lands on the terminal `REVOKED` — the only way back is a brand-new assignment. Mandatory justification. Distinct from self-service Deactivate (which is `ACTIVE`-only and returns to `ELIGIBLE`).
- **"Assign immediately" bypass** (`bypass_activation: true` on `AssignmentCreate`, Admin-only, mutually exclusive with any approver field): skips the eligible/activate dance entirely — real access the instant the admin submits (or `SCHEDULED` if `start_time` is future-dated, picked up by the activation worker). The end user can never self-deactivate a bypass grant; only an Admin can revoke/deactivate it.
- **Supersede-on-recreate**: assigning a user to the exact same target they already hold automatically revokes the old assignment first — deliberately deferred to the moment access **actually becomes real** (activation/approval/bypass-creation), never at plain request/creation time, so a pending request can never strip existing access before anyone's decided anything.
- **Two eligible rows to the same target can coexist** — whichever activates first supersedes the other.
- **Fallback approver**: an assignment can name a second approver who can act if the primary hasn't within a configurable `fallback_unlock_hours` window (time-gated escalation, not immediate) — either approver's decision is equally valid once unlocked.
- **Mandatory justification** everywhere real access is touched: creating, approving, self-activating. Recorded on the audit entry for approve/activate; the original creation-time justification lives on the assignment row itself.
- **SoD enforcement** at exactly three points — bypass-creation, activation, and the scheduled-activation worker — see §4.
- **Cooldown anti-gaming check** (opt-in): blocks a user from deactivating one side of a conflict and immediately activating the other to dodge SoD, by also checking the audit log for a recent deactivate/revoke of the opposite side.
- **`override_sod: true`**: a one-time, no-memory bypass of a single SoD block, Admin-only, still requires the existing mandatory justification (no second field) — distinct from a standing SoD exception (§4).

## 3. Access Packages

- Bundle several Group/Role/Application-role items into one named package; assigning the package fans out to one ordinary `AccessAssignment` per item via the **same, unmodified** `create_assignment()` — so every other engine feature (approval, activation, SoD, notifications, supersede) applies to package items automatically, with zero package-specific duplication.
- **Unified creation**: name, description, items, AND the approval setup (default approver, fallback approver, fallback-unlock-hours, who-can-request) all happen in one `POST /packages` call.
- **Eligibility**: named individual users and/or whole groups (`PUT /packages/{id}/eligibility`) may request a package themselves (`POST /packages/{id}/request`) — same real activation flow as an admin-direct assignment.
- **Admin direct assign**: to one user or fanned out to every current member of a group (`POST /packages/{id}/assign`, `group_id` or `user_id`), each member getting their own batch id.
- **Batch grouping**: every item from one package-assignment action shares a `package_assignment_id`; the Assignments/Approvals tables group same-batch rows together with "approve/reject/revoke all" batch actions (individual per-item actions still fire the same real, individually-audited underlying calls).
- **My Access batching**: a user's own eligible/active package items are grouped by `package_id` (not per-batch) so re-requesting the same package never shows visual duplicates, with one "Activate all"/"Deactivate all" button per package.
- **Edit** (`PATCH /packages/{id}`): rename/redescribe, full item-list replace — only ever affects future assignments, never retroactive.
- **Delete** (`DELETE /packages/{id}`): hard-deletes if the package was never assigned to anyone; otherwise archives it (history/audit trail stays intact) and returns the archived object.
- **Per-item results are best-effort**: one item's Graph failure never blocks the others in the same assignment; the response reports per-item `CREATED`/`FAILED`.

## 4. Separation of Duties (SoD) Engine

*(Full detail in `19_SOD_ENGINE.md` — this is the summary view.)*

- **Rules** name two sets of entitlements (Group/Role/Application-role/whole Access Package) that must never both be held by the same person — checked **preventively** (blocks a new grant before it completes) and **detectively** (a live scan reporting conflicts that already exist, from any source).
- **Two data sources checked for every rule**: AccessPilot-tracked assignments (`ELIGIBLE` counts as a real holding, not just `ACTIVE`) *and* direct-in-Entra membership (groups via the synced table, roles/applications via a live Graph read) — critical because AccessPilot's own login roles are never tracked as assignments at all.
- **Three enforcement points**: admin bypass-creation, self/admin activation, and the scheduled-activation worker — never at plain request time.
- **`AccessPilot.SoDAdmin`** is a real, separate role from `AccessPilot.Admin` — a genuine separation of duties on the engine itself. A plain Admin sees **nothing** SoD-related at all (no rules, no violations, no exceptions) — moved from read-only oversight to full exclusion per explicit request, the same treatment the Security Operations dashboard already has. Sourced **exclusively from a real Entra App Role assignment** — the in-app grant/revoke roster that used to exist was removed entirely (a real Admin-can-self-escalate gap, found and closed).
- **Exceptions**: a formally accepted, time-boxed risk acceptance for a `(policy, user)` pair, with mandatory justification and expiry. While active, the preventive check silently lets that specific conflict through; the detective scan still reports it, marked "accepted."
- **Exception requests**: a self-service bridge — an admin blocked by a policy can request review instead of being stuck; granting recreates the exact original blocked attempt (including routing through the same approver, if one was configured) rather than a bare eligible row.
- **Revoking or expiring an exception now also ends the specific access it covered** — both `ELIGIBLE` and `ACTIVE`, cascading immediately on manual revoke and within 60 seconds of natural expiry via a dedicated background worker, regardless of who has the app open. The exact assignment is tracked via a direct link recorded at grant time (not re-derived by guesswork), after an earlier heuristic-based version proved unsafe twice in live testing.
- **Cooldown period**, **dangling-reference handling** (a deleted Group/Role/Application/Package a rule references degrades to "never triggers," never errors), **severity as a label only** (no enforcement branches on it), and a **live-only detective scan** (nothing about which conflicts exist is ever stored — only the exceptions/requests that mitigate them are).
- **Notifications**: new violation found, exception expiring soon, exception expired-but-still-open, exception requested, exception request granted/denied — five types, most independently toggleable, delivered via 60s Bell polling for SoDAdmin sessions only (Admin no longer sees this bell at all).
- **Own dedicated sidebar section** ("Separation of Duties"), its own SoD-scoped activity/audit feed, and a Dashboard widget showing live violation count — all exclusive to a real `AccessPilot.SoDAdmin`, invisible to a plain Admin.

## 5. IGA Onboarding — CSV / HR feed

- **Upload → validate → preview → commit** lifecycle (`POST /onboarding/csv` takes a JSON `{filename, content}` body, not multipart — no new dependency needed). Required columns: `employeeId, firstName, lastName, email, department, status`; optional `jobTitle`.
- **Validation** catches missing required fields, invalid email, invalid status, and duplicate `employeeId` within the same file — one bad row never blocks the others.
- **Preview** shows every row's computed action (`CREATE`/`UPDATE`/`NO_CHANGE`/`DISABLE`/`ERROR`) before anything is written.
- **Commit** provisions identities via the exact same normalization path (`upsert_user()`) real Entra sync already uses — a CSV row becomes an ordinary AccessPilot identity, immediately visible everywhere else in the app.
- **One identity row per person, regardless of connector** (a real, since-fixed bug — see §21): a new joiner tries **real Entra account provisioning first**, falling back to a local CSV-only bookkeeping row only if no real connector is configured or Graph rejects it. An identity stuck on the CSV fallback **graduates in place** on every subsequent commit as real provisioning becomes possible (e.g. once a domain is verified), never creating a second, duplicate row. `NO_CHANGE` rows still retry provisioning every re-upload for this reason.
- **Leaver handling**: a `TERMINATED` row disables the identity **and** automatically revokes every one of that user's assignments (`ELIGIBLE`/`PENDING_APPROVAL`/`SCHEDULED`/`ACTIVE`) via the same universal Admin-Revoke function, real Entra access removed for anything that was genuinely `ACTIVE`. A mere attribute change (a mover, not a leaver) never touches assignments.
- **Birthright policy evaluation** (§6) runs automatically on every `CREATE`/`UPDATE` commit.
- **Real Entra account provisioning** on commit: builds the new account via the connector, using an **admin-configurable domain + username-naming-convention mapping** (`{first}`/`{last}`/`{f}`/`{l}` tokens, validated at save time) instead of blindly trusting the CSV's own (often placeholder/unverified) email domain. Falls back gracefully to local-only if no domain mapping is configured, the provider rejects the domain, or provisioning otherwise fails — never blocks the rest of the commit.
- **A day-one real (not merely eligible) birthright grant** happens automatically the moment real provisioning succeeds — via the existing bypass-activation mechanism, targeting the real account.
- Past-import history browsable (`GET /onboarding/imports`, `/{id}`, `/{id}/preview`).
- Admin UI (`/admin/onboarding`): file upload, live preview table, commit button, import history, plus a plain-text API-reference panel (linking the FastAPI-generated Swagger docs) for a future direct HR-system integration.

## 6. Birthright Policies

- A rule of the shape `department|job_title == value → auto-grant Group/Role/Application`. Evaluated against every joiner/mover during onboarding commit, and available standalone (`POST /policies/birthright/evaluate/{user_id}`) for already-synced identities.
- **Idempotent by construction** — checks for an existing non-final assignment to the exact same target before creating, so re-evaluation never duplicates.
- CRUD on `/admin/policies` (Create/edit/disable/delete, Group/Role/Application picker matching the Assignments form's own pattern).

## 7. Provisioning Mapping (real accounts + naming conventions)

- **Verified-domain picker**: `GET /providers/{id}/domains` fetches the tenant's real verified/unverified domains from Graph (needs `Domain.Read.All`); the Admin UI falls back to a free-text input if the fetch fails or hasn't run, so the whole feature stays usable regardless.
- **Username-convention engine**: a small token-substitution template (`{first}`, `{last}`, `{f}`, `{l}`), slugified and validated at save time, with a safe fallback to the CSV row's own email local-part on any error — never blocks provisioning.
- **Zero behavior change until an Admin opts in** — both fields are `NULL` by default on every existing provider.

## 8. Approvals

- Any real user (not just Admins) can be a designated approver on an assignment — a genuinely object-level, not role-based-only, capability.
- `GET /assignments/pending-approval` (self-scoped to the caller as approver) powers the Approvals page for both Admins and plain end-users who happen to be someone's approver.
- Batch approve/reject for whole package assignments; individual approve/reject for single items.
- Approval decision requires a mandatory justification, recorded on the audit entry.

## 9. Notifications

*(Full detail in `20_NOTIFICATIONS.md`.)*

- **Two separate systems, deliberately not unified**: a general, per-user notification table (personal, one row per recipient-event, individually marked read) for ordinary lifecycle events (assigned, approved, rejected, activated, deactivated, revoked, package-request outcomes, SoD exception-request outcomes) — and the SoD-specific log (§4), whose read state is a single shared flag appropriate for a small SoDAdmin team, not per-person.
- **Core rule for the general system**: only notify when the actor isn't the target themselves — an admin acting on someone else's assignment always notifies them; a self-service action never self-notifies.
- **Delivery is polling-based, not push** (no WebSocket/SSE infrastructure exists anywhere in this app): personal feed every 10 seconds (cheap plain read), SoD feed every 60 seconds (expensive reconciliation pass), Exception Requests panel every 20 seconds.
- **Topbar Bell**: visible to every signed-in user, merges both sources chronologically into one dropdown (capped at 10), live unread-count badge, "Mark read"/"Mark all read" routed to the correct backend per source, click-outside-to-close popover.

## 10. Dashboard & Reporting

- **Admin dashboard**: total users/groups/privileged roles, provider health, active JIT sessions, pending requests, expiring-soon access, policy coverage — all real, computed live, each stat card deep-linking into a pre-filtered admin list page via URL query params.
- **Privileged-role-activation timeline** — a hand-rolled SVG line chart (zero chart dependencies), bucketed by day, crediting the assignment's real target user (not whoever clicked activate on their behalf).
- **User-access-mix pie chart** — Permanent+Active vs. Eligible-only, no double-counting; each slice/legend row is clickable, opening a real member-list overlay.
- **SoD violation-count widget** (visible only to a real SoDAdmin, not a plain Admin) plus the 3 most recent SoD activity entries.
- **Recent access requests / recent activity** feed, sourced from the real audit log.
- **End-user dashboard**: real stat cards (active/eligible/pending/expiring-soon, all computed from the caller's own `GET /assignments/mine`), real recent-activity list, real current-active-access table — the "Request access" button actually navigates now.
- **Real-time feel** via a 30-second polling interval, the same technique the whole app uses everywhere "live" is claimed (no push infrastructure exists).
- **URL-driven filtering** (search + status/category dropdowns) across every real admin list page (Users, Groups, Roles, Assignments, Access Packages, Audit Logs, Sync history) — state lives in the URL via `useSearchParams`, so a filtered view is bookmarkable and a Dashboard card can deep-link straight into one with zero special plumbing.

## 10a. Security Operations Dashboard (SoC)

- **New role, `AccessPilot.SoCAdmin`** — sourced exclusively from a real Entra App Role (no in-app grant path, matching the lesson learned from SoDAdmin's own history — see §4). Read-only observer permission set (`SOC_READ`, `AUDIT_READ`, `SOD_READ`, `SYNC_READ`) — can watch everything relevant, act on nothing. **Unlike the SoD dashboard, `AccessPilot.Admin` does NOT have `SOC_READ`** — a deliberate exception to this app's usual "Admin can see everything read-only" pattern, at the user's explicit request: this dashboard is exclusive to whoever actually holds the SoCAdmin role.
- **A genuinely self-service, drag-and-drop dashboard**, not a fixed layout: every widget can be freely reordered by dragging it with the mouse (native HTML5 drag-and-drop, no new dependency), removed, or added — including **building an entirely new graph from scratch**. `GET /soc/fields` exposes the exact whitelist of data sources (`audit_logs`, `assignments`) and their groupable/filterable fields; the "Add widget" builder lets a SoCAdmin choose a source, a chart type (number / line-over-time / grouped bar / grouped list), a field to group by, and an optional equals-filter — computed live via one generic `POST /soc/widget-data` call, never raw/arbitrary column access.
- **6 built-in panels** remain available to add (not hardcoded into a fixed layout anymore): 4 stat cards (active sessions, pending approvals, open SoD violations, break-glass events in the last 24h), a 30-day activity timeline, and a curated **high-signal events feed** — a fixed, in-code list of specifically security-relevant audit actions (break-glass login/elevate/rotate, assignment revoked/blocked, SoD exception or policy changes, provider deletion, sync failure, portal-auth-config changes), not "the most recent N events overall."
- **Personal layout persistence** (`GET`/`PUT /soc/layout`) — the first genuinely per-viewer preference table in this app (every other settings table is a shared singleton); every add/remove/reorder auto-saves immediately, no separate "Save" step.
- **Its own blue accent theme**, scoped only to this one page — visually distinct from the rest of the app's teal.
- **Click-to-drilldown on any timeseries chart** — clicking a point opens the real rows behind that day (`POST /soc/widget-drilldown`, capped at 200 rows, newest first). For the built-in activity timeline this returns real hydrated audit-log entries (actor/target names resolved, not raw ids); for a custom timeseries widget it re-validates the widget's filters against the same field whitelist and returns rows shaped for the underlying source. Only timeseries widgets — plus one named exception below — are drillable (422 on cards/bars/lists otherwise). Fixed an incidental bug while adding this: the reused `ActivationTimelineChart` component had its Y-axis hardcoded "Users activated" from its original single-purpose Dashboard usage — it now takes optional `yAxisLabel`/`unitLabel`/`tooltipSuffix` props, defaulting to the original strings so the admin Dashboard's own chart is unaffected.
- **"SoD violations" card counts every current conflict, exempted or not** — matches the real SoD admin page's own convention (its violations table never hides an exempted one from the count either); an earlier version filtered out exempted violations, which meant a real, known conflict could silently show as `0` with no indication anything existed. Clicking the card opens an overlay showing every current conflict — open and exempted alike — with the user, the policy, the actual holdings on each side, and (when exempted) the exception's expiry date, sourced from the same live `get_sod_violations()` scan the count itself uses.
- **Every card and graph on the dashboard is clickable**, not just the SoD violations card and the timeseries chart: any other card (built-in or custom) drills into the real rows behind its number, and any bar/list widget drills into the rows behind whichever entry was clicked. The only two panels with nothing further to show are "SoD violations" (its own richer drilldown above) and "High-signal events" (already a raw list of individual events).
- **Explicitly not built**: true freeform pixel positioning or resize (this is grid-reorder via drag, not arbitrary x/y placement); multi-condition filters (one equals-filter per custom widget); real statistical/ML anomaly detection (the signal feed is a fixed curated list, a deliberate stated scope boundary); a failed-login/auth-attempt audit trail (so sign-in-based anomalies aren't detectable at all yet).

## 10b. System Health Dashboard (AccessPilot.ServerAdmin)

- **New role, `AccessPilot.ServerAdmin`** — sourced exclusively from a real Entra App Role (no in-app grant path, same as SoDAdmin/SoCAdmin), signs in through the exact same default login flow as everyone else. Read-only permission set (`SERVER_HEALTH_READ` plus the baseline `ME_READ`/`DASHBOARD_USER_READ`) — a pure observer, nothing to manage. **Exclusive to this role** — `AccessPilot.Admin` does not have `SERVER_HEALTH_READ`, the same exclusive-to-its-own-role treatment SOC and Separation of Duties both already have.
- **A distinct concern from SOC and SoD** — infra/ops health (API, database, background workers, connected identity providers, live event log), not security signal or governance rules. Its own sidebar section ("SYSTEM HEALTH"), own route (`/admin/server-health`), own graphite/emerald accent theme.
- **V2: every number is real**, computed on each request from real sources — nothing on this page is mock data anymore (`GET /api/v1/server-health` returns `is_mock: false`).
  - **Real new instrumentation**: `RequestTimingMiddleware` (a raw ASGI middleware, not `BaseHTTPMiddleware` — same disconnect-deadlock reasoning as the app's existing `RequestIdMiddleware`) records every request's route/method/status/duration into a bounded in-memory window, powering the request-volume/latency chart and the per-endpoint table. SQLAlchemy engine-level event listeners record real per-query timing for the DB panel's avg/slowest query.
  - **Real worker liveness**: each of the app's 4 real background loops (Entra sync, access expiry sweep, scheduled activation, SoD exception expiry) records a real tick after every iteration; the page checks the actual `asyncio.Task` objects for genuine liveness, not just process-not-crashed.
  - **Real everywhere else**: DB connection pool via SQLAlchemy's engine, Graph/sync health from `sync_runs`/`sync_errors`, the real configured-provider list, a real `audit_logs` row count, and a live event log of real hydrated `AuditLog` rows.
  - **Two cards were honestly reworked, not just re-sourced**: no request-history table exists to compute a real 30-day uptime percentage, so the API card shows real **process uptime** instead; this is a stateless JWT/MSAL app with no server-side session concept and no failed-login trail, so "active sessions" became **"users with active access"** (a real, differently-named, honest metric) plus a real connected-provider count. `replication_lag` ("n/a — single node") and `last_backup` ("Not configured") stay hardcoded strings deliberately — both are real, honest facts about this deployment, never fabricated numbers.
  - Process-local state only (resets on restart) — this describes what the running process is doing right now, not something that needs to survive a restart, so nothing here touches the database as its own storage.
  - **Auto-refreshes every 20 seconds** while the page is open.
  - **Live event log shows real server-side activity only** — filtered to audit entries with no human actor (`AuditLog.actor_user_id IS NULL`, the same real signal every worker's own audit record already carries), so it shows real `Sync Completed`/`Role Synced`/etc. entries, never an ordinary business action like a plain assignment grant (which already has its own home on the regular Audit Logs page).
  - **Connection-pool reading reflects genuine, real usage** — a real DB query runs earlier in the same request before the pool is read, so the number reflects this request's own connection actually being checked out rather than a guaranteed `0` (`AsyncSession` only acquires a physical connection lazily, on its first query).
- **A dedicated "Workflows" section, light theme, 6 real pipelines** — a responsive card grid, not just the 4 background asyncio loops: **Entra sync**, **access expiry sweep**, **scheduled activation**, and **SoD exception expiry** (each with its own real per-worker status — not-running/stopped/last-tick-failed/running, a real tick history, and a real "N in the last 24h" activity count from its own unique audit signature), plus two **on-demand** workflows that aren't background loops but are still real and monitorable: **Onboarding CSV import** (real most-recent import status and a real "N committed in the last 7 days" count) and **Access Package assignment** (real created/failed counts from the last 24h's `PACKAGE_ASSIGNED` audit entries). Every card shows a plain, accurate description of what the pipeline actually does.

## 10c. Troubleshooting (drill-down from System Health)

- A diagnostic page (`/admin/server-health/troubleshooting`, linked from System Health's own action bar) — same light "White look" as System Health (an earlier dark theme was reverted, since this is a drill-down from that page, not a separate area of the app), same `SERVER_HEALTH_READ` gate, a drill-down not a separate nav destination. Auto-refreshes every 20 seconds.
- **Diagnoses one real thing at a time**: picks whichever service card is currently worst (severity-ranked) and only shows an incident/root-causes/checklist when something is genuinely degraded — when everything's healthy, the page shows a calm "no active incidents" state rather than always pretending there's a problem to investigate. Per-scenario diagnosis for Graph connector, Database, Background workers, and API, each citing real numbers (real `SyncError`/throttling counts in the last hour, real pool/query stats, real dead-worker names, real erroring endpoints) — curated, explainable, rule-based diagnosis, the same honest scope boundary SOC's "high-signal events" already drew; not ML.
- **Checklist items are only ever marked done when genuinely verifiable from real data** — an informational item (e.g. "confirm which calls are failing") is checked off with the real count when evidence exists; an action item a human still needs to do (re-run a sync, check the Entra portal's quota page) is never auto-checked.
- **Real error-rate chart** (last 30 minutes, bucketed from real `SyncError` timestamps) and a **real dependency chain** (API → Graph connector → Sync worker → SoD detective scan, the last one honestly labeled as an *inferred* dependency — SoD's detective scan reads synced group/role data, so a degraded connector means that data is stale, a real architectural fact, not an independently measured one).
- **Identity providers panel maps real provider status honestly**: `CONNECTED` (a real test/sync succeeded) → healthy; `CONFIGURED` (credentials set, not yet verified — not broken) → a distinct "unverified" state, not lumped in with "mock"; `ERROR` → critical. The one provider type actually used for interactive login (`ENTRA`) is tagged "primary"; anything else is tagged "secondary — data-source only."
- **Correlated logs**, same real server-side-only filter as System Health's own event log, with filter pills generated from whatever services are actually present in the real data (never a fabricated empty category).
- **Quick actions**: "Copy diagnostics" is genuinely functional (client-side only, copies the real fetched payload as JSON). "Retry Graph connection" and "Run manual sync" are deliberately informational only, not one-click buttons, even though the real endpoints they'd call already exist (`POST /providers/{id}/test-connection`, `POST /providers/{id}/sync`) — `AccessPilot.ServerAdmin` is a deliberately read-only role and doesn't hold `PROVIDER_MANAGE`/`PROVIDER_SYNC`; wiring these up would have meant quietly expanding a role's permission set, a real security-boundary change made without being asked.

## 11. Audit Logging

- Every consequential action across the whole app records an entry: who (or "System" for a background worker), what, on what target, the real provider-call result, and a `request_id` correlating it to the exact HTTP request or worker tick that caused it.
- `GET /audit-logs` (Admin-only, full history) resolves and shows the **target** user of an assignment-related entry, not just the acting admin.
- SoD-relevant entries are also filtered into their own feed (§4) so a plain SoDAdmin — who lacks general `AUDIT_READ` — can still see the history relevant to their own domain.

## 12. Security Settings

- **Three independent, admin-configurable idle-session tiers**, each with its own on/off toggle and minute threshold, applied to every signed-in user (Admin and end-user alike) via the normal login only (never the restricted Break-Glass dashboard):
  1. **Blur** — a dismiss-on-any-activity blurred overlay.
  2. **Lock** — an opaque, click-"Continue"-to-resume overlay that does **not** end the session; mere activity does not clear it.
  3. **Auto-logout** — actually signs the user out after the threshold, the only tier that ends the session.
- **Tenant-wide, admin-configurable display timezone** (this session's own feature): a single IANA timezone (default `Europe/Berlin`) applied to **every** date/time shown anywhere in the app, for every viewer, regardless of their own browser locale — validated server-side, picked from a curated list or typed freely in the Admin UI. Deliberately **display-only**: date/time *input* fields still use each user's own browser-local time; only already-recorded timestamps are shown in the configured zone.
- All three idle tiers plus the timezone setting live in one `security_settings` singleton table/endpoint (`GET`/`PATCH /security-settings`) — readable by any signed-in user (everyone needs to apply it client-side), writable by Admin only.
- **Admin-configurable IDP-unreachable fallback contact**: a `support_contact_email` field on this same settings table (blank by default). When the identity provider itself can't be reached at all, the sign-in screen shows a real error panel — heading, plain-language explanation, and a clickable `mailto:` link to this address when one is configured (a generic "contact your administrator" with no address otherwise) — instead of a dead end. Exposed via its own public endpoint, `GET /security-settings/support-contact`, with **no authentication at all** (mirrors `GET /branding`'s existing public-read precedent) — the one scenario this exists for is a user who can't sign in, so it can never sit behind the normal auth-required settings read.
  - **A real, hard limit worth knowing**: `loginRedirect()` is a full top-level browser navigation to the IDP's own domain — if that navigation itself fails mid-flight (a genuine network outage), the *browser* intercepts it and shows its own native offline error page before this fallback UI (or any AccessPilot JS) ever gets a chance to run, since the page has already navigated away. No web app can override a browser's native error page for a navigation already in progress. The mitigation: `signIn()` runs two real connectivity probes in parallel *before* ever calling `loginRedirect()` — a short-timeout `fetch()` against AccessPilot's own `/api/v1/health`, **and** a `mode:'no-cors'` `fetch()` against the IDP's own real domain (`login.microsoftonline.com`) directly. Either one failing (a thrown/rejected fetch) skips the redirect and shows this fallback panel immediately instead. Both checks are needed for different reasons: the own-backend probe alone is *not* a valid internet-connectivity signal — loopback/LAN traffic never leaves the machine, so it stays reachable even with the real internet fully down (confirmed live: with internet disconnected, `localhost:8001/health` still succeeded instantly while `login.microsoftonline.com` failed with a DNS error in the same test) — so the actual IDP-domain probe is the one that matters for detecting a real outage; the own-backend probe is kept alongside it because a reachable internet but a dead AccessPilot backend would otherwise strand the user right after a successful Microsoft sign-in. (`navigator.onLine` was tried first and found unreliable too, for a related reason — it only reflects whether a network *interface* is up, e.g. Wi-Fi still associated with a router, and stays `true` even with a dead router and zero real internet.) A connection that's up but can't reach this one domain specifically for some other reason (a captive portal, a proxy blocking just Microsoft's login domain) is still correctly caught by the IDP-domain probe itself.

## 13. Branding / White-labeling

- Admin-configurable sign-in logo, internal (sidebar) logo, and "Powered by" attribution text — all stored as base64 data URIs directly in the database (no filesystem/blob storage), `NULL` meaning "use the bundled default" so every deployment renders unchanged until an Admin uploads something.
- `GET /branding` is fully public (no auth) — the sign-in screen needs it before anyone has logged in.
- Upload validation: only real raster image types accepted (SVG deliberately excluded — can carry embedded scripts), size-capped.

## 14. Providers & Directory Sync

- Multi-provider-ready schema (every core table already carries `provider_id`), though only one connector runs at a time today (`MOCK` for dev, real `ENTRA` for production) — an `IdentityProvider` abstract base class is the seam a future Okta connector would implement.
- Provider CRUD, credential rotation, connection test, manual sync trigger, sync-run history, and the domain/naming-convention mapping (§7) all live under `/admin/providers` and `/admin/sync`.
- **Scheduled sync** requires an Admin to explicitly set `sync_interval_minutes` — a real, once-live gap where sync silently never ran at all until this was actually configured (see §21).

## 15. Portal Authentication & Setup

- **Bootstrap flow**: a fresh install with no portal IDP configured anywhere prints a one-time, randomly generated bootstrap credential to the startup log exactly once — never a hardcoded `admin/admin`. That session can reach *only* the setup wizard.
- **Setup wizard** (backend chain fully built; wizard UI not yet built — see §21): captures the real IDP config (Entra today; Okta scaffolded but not wired) *and* a Break-Glass account together, requires a real successful test login through the newly-entered IDP config before activating anything, then permanently deletes the bootstrap credential and activates Break-Glass.
- **Dynamic, DB-driven IDP config** as a fallback when build-time `VITE_ENTRA_*` env vars are absent — the portal's own MSAL client can be built from a config fetched live from the backend instead of baked in at build time.
- Every existing deployment (this one included) that already has env-var Entra configured is **completely unaffected** by any of this — it's dormant until deliberately triggered.

## 16. Break-Glass Emergency Access

- Reachable **only** via a hidden `/emergency-access/:token` URL — the secret token is generated solely by a CLI command (`python -m app.cli emergency-url`), never shown or discoverable anywhere in the normal UI. A wrong/missing token renders a generic 404, byte-identical to a genuinely nonexistent route.
- A fresh Break-Glass login lands on a **narrowly-scoped `AccessPilot.BreakGlassAdmin` role** — can see nothing but its own restricted dashboard (fix the broken IDP config, rotate its own password) — never full Admin by default.
- **Explicit elevation** (`POST /auth/breakglass-elevate`, one confirm click, no re-entered password) is required to reach full `AccessPilot.Admin` capability — proven live that a non-elevated session genuinely 403s on ordinary admin endpoints and only succeeds after elevating.
- The emergency-access page renders **outside** the normal authenticated app tree entirely, so the regular sign-in screen can never intercept or expose it.
- An IDP outage shows only a generic "identity provider unavailable" notice to a normal user — no Break-Glass UI is ever surfaced on the public sign-in screen.

## 17. Background Workers

Five independent async loops, all started together in `main.py`'s lifespan (dormant/no-op in the test environment):

1. **Sync scheduler** (`workers/scheduler.py`) — runs a provider's directory sync on its configured interval.
2. **Expiration worker** (`workers/expiration.py`, 60s) — flips `ACTIVE` assignments past their `expiration_time` to `EXPIRED` (removing real access first), and separately expires `ELIGIBLE` rows whose activation deadline passed without ever being activated.
3. **Activation worker** (`workers/activation.py`, 60s) — grants real access for future-dated `SCHEDULED` bypass-assignments once their start time arrives; leaves a conflicting one `SCHEDULED` (with an audited reason) if it would violate an SoD rule.
4. **SoD exception expiry worker** (`workers/sod_expiry.py`, 60s) — ends the specific access an SoD exception was covering once that exception's own expiry passes, regardless of who has the app open (§4).
5. **(Directory) sync worker** (`workers/sync.py`) — the actual per-provider sync execution the scheduler above triggers.

## 18. Frontend — pages & navigation map

**Self-service** (every signed-in user): Dashboard, My Access, Request Access, Request Packages, My Requests, Approvals, Profile.

**Administration** (`AccessPilot.Admin`): Users (+ per-user detail), Groups, Roles.

**Access Management** (Admin): Access Requests (legacy mock page, see §21), Assignments, Access Packages.

**Governance** (Admin): Policies (birthright policies live here; the rest of this page is still mock — see §21), Audit Logs.

**Separation of Duties** (exclusive to a real `AccessPilot.SoDAdmin` — a plain Admin does not see this section at all; its own dedicated sidebar section): Separation of Duties (rules/violations/exceptions/activity), SoD Configuration (notification settings + full notification log).

**System** (Admin): Providers, Sync, Onboarding, Security, Branding.

**Outside the normal app shell entirely**: the public sign-in screen, the hidden Break-Glass emergency-access page and its restricted dashboard, the setup-wizard flow (backend-only today).

## 19. Full API surface (by router)

| Router | Base path | Endpoint count |
|---|---|---|
| `assignments.py` | `/api/v1/assignments` | 11 (list mine/pending/all, activation-policy, CRUD, approve/reject/activate/deactivate/revoke) |
| `audit.py` | `/api/v1/audit-logs` | 1 |
| `auth.py` | `/api/v1/auth` | 4 (breakglass-login, emergency-access verify, breakglass-elevate, portal-config) |
| `branding.py` | `/api/v1/branding` | 2 (public read, admin update) |
| `breakglass_console.py` | `/api/v1/auth` | 3 (portal-auth-config read/update, credential rotate) |
| `directory.py` | `/api/v1` | 14 (users, groups, roles, applications, dashboard variants) |
| `notifications.py` | `/api/v1/notifications` | 3 |
| `onboarding.py` | `/api/v1/onboarding` | 5 |
| `packages.py` | `/api/v1/packages` | 12 |
| `policies.py` | `/api/v1/policies` | 5 (birthright CRUD + manual evaluate) |
| `providers.py` | `/api/v1/providers` | 10 |
| `security_settings.py` | `/api/v1/security-settings` | 2 |
| `setup.py` | `/api/v1/setup` | 4 |
| `sod.py` | `/api/v1/sod` | 18 |
| `soc.py` | `/api/v1/soc` | 4 (available fields, layout read/update, widget data) |

Roughly **98 real HTTP endpoints** in total (excluding the auto-generated `/docs`/`/openapi.json`).

## 20. Cross-cutting engineering conventions (the small things worth knowing)

- **"Nothing about real access changes until it's actually about to become real"** — the single organizing principle behind the entire eligible/activate model, supersede-on-recreate timing, and every SoD enforcement point.
- **Live-computed, never materialized** for anything that's just a *view* of current state (dashboard segments/timelines, SoD violations) — only genuine *decisions with their own lifecycle* get a real table (SoD exceptions/requests, notifications, onboarding imports, birthright policies).
- **Fail-open on any live Graph read used for detection/display** (Direct-in-Entra checks, SoD's role/application scan) — a Graph error is logged distinctly and treated as "nothing found," never blocks the caller or crashes.
- **Reuse the shared lifecycle functions, never fork them** — packages, birthright grants, onboarding leaver revocation, and SoD's own exception-grant path all funnel through the same `create_assignment`/`activate_assignment`/`revoke_assignment` core, so every cross-cutting feature (notifications, audit, SoD checks) applies to all of them for free.
- **No new dependencies for what stdlib/existing libraries can already do** — CSV upload as a JSON body (no `python-multipart`), PBKDF2-HMAC password hashing (no bcrypt/argon2/passlib), hand-rolled SVG charts (no chart library), plain `argparse` CLI (no Typer/Click).
- **"Live" always means short-interval polling, never real push** — there is no WebSocket/SSE infrastructure anywhere in this app; every "real-time" feel (Dashboard, notifications, SoD Bell) is an interval timer of a deliberately chosen cadence, traded off against the real cost of what it's polling.
- **Mandatory justification** is the one recurring, non-negotiable field at every point real access is created, approved, or activated.
- **A dormant `AccessRequest`/`ApprovalStep` table pair and a `Policy`/`PolicyTarget` pair exist in the schema from early planning and were deliberately never resurrected** — superseded by the real `AccessAssignment` model and the SoD/birthright tables respectively; noted here so a future session doesn't mistake them for live code.

## 21. Known gaps / explicitly not built (as of this writing)

- No real account **deletion**/deprovisioning in Entra — a leaver's real account must be removed manually in the Entra portal; the connector has no `delete_user` method.
- The admin "Access Requests" page and most of the "Policies" page's own table are still mock data, superseded by the real Assignments/Access-Packages and Birthright-Policies systems respectively but never removed.
- No Okta login flow actually wired (the connector interface and setup-wizard schema support it; no client-side Okta SDK integration exists yet).
- The setup-wizard **frontend UI** doesn't exist yet — the entire backend chain is built and tested, but completing setup today means calling `/setup/activate` by hand with a token obtained via MSAL, not a guided form.
- No multi-way (>2-set) SoD conflict rules; no severity-gated enforcement (severity is a label only); no rule version history.
- SoD's cooldown check and exception auto-revoke only ever catch AccessPilot's own recorded actions — a change made directly in Entra outside the app leaves no audit trail to key off of.
- No outbound notification delivery (email/Teams/webhook) anywhere — everything is in-app only.
- The Security Operations dashboard's "high-signal events" is a fixed curated action list, not real statistical/ML anomaly detection, and there's no failed-login/auth-attempt audit trail yet — both deliberate, stated v1 scope limits (§10a).
- No drag-and-drop grid for dashboard widget customization anywhere (SOC dashboard included) — reordering is click-based (move earlier/later, hide/show), a conscious no-new-dependency tradeoff.
- No per-viewer read state on the shared SoD notification log (a single global read flag, appropriate only at small-team scale).
- No real deletion path for a package's own items independent of deleting the whole package (fine today — see §20's "delete if unused, archive if not" convention, applied identically to packages and SoD policies).
