# Plan — A standalone HR Employee app with API-key auth, two-way with AccessPilot

**Scope of this document**: a plan only, nothing built yet, on either side. Covers (1) a brand-new, separate HR
application — its own employee list page, add/edit fields, and an API key the admin can generate and copy — and
(2) the matching, purely additive changes on the AccessPilot side to authenticate with an API key instead of a
stored username/password, and to push lifecycle status back. **Nothing about AccessPilot's existing code changes**
until you say "start code" — this plan doesn't touch the HR connection panel, the onboarding engine, or the
already-shipped session-cookie sync (`HR_INTEGRATION_PLAN.md`), all of which keep working exactly as they do today.

Three design decisions you already confirmed, which shape everything below:
- This is a **brand-new, separate app** — not a page bolted onto the existing HR app on port 3000.
- The AccessPilot → HR direction carries **provisioning/lifecycle status only** (read-only, informational) — never
  employee data edits. HR stays the single source of truth for who someone is; AccessPilot only reports back what
  it did about them.
- The existing username/password (session-cookie) method is **kept, untouched**. API key is a new, additional,
  preferred path — not a replacement.

---

## 1. The new standalone app ("HR Employee Directory v2")

A small, purpose-built app — one coder could build this independently, the same way the first HR app was built
from `HR_SOURCE_OF_TRUTH_APP_PLAN.md`.

### 1.1 Employee list page (the core of the app)

One page: a table of every employee, with **Add employee** and **Edit** actions. Same field contract as the first
HR app, since AccessPilot's importer already expects these exact names — no reason to invent a new shape:

| Field | Required? | Notes |
|---|---|---|
| `employeeId` | Required | Stable, unique, never reused — the join key AccessPilot matches on |
| `firstName` | Required | |
| `lastName` | Required | |
| `email` | Required | |
| `department` | Required | |
| `status` | Required | `ACTIVE` or `TERMINATED` |
| `jobTitle` | Optional | |
| `managerId` | Optional | Another employee's `employeeId` |
| `employeeCategory` | Optional | |
| `employmentType` | Optional | |
| `startDate` | Optional | `YYYY-MM-DD` |
| `effectiveDate` | Optional | `YYYY-MM-DD` — a future-dated department/title change |
| `leaverDate` | Optional | `YYYY-MM-DD` — required once `status` is `TERMINATED`; this is what drives AccessPilot's automatic leaver process |

### 1.2 Provisioning status column (new — read-only, written by AccessPilot)

Each employee row also shows a **read-only** "AccessPilot status" strip — not editable by HR, only ever written
by the API call described in §3.2:

- Last known state per connected directory, e.g. `AD: account created`, `Entra: account disabled`.
- A timestamp for each.
- Nothing here is ever required for the HR admin to act on — it's visibility, not a workflow.

### 1.3 API key management (new)

A settings page, separate from the employee list:

- **Generate API key** button. On click, a cryptographically random key is generated, its **hash** is stored
  server-side (never the raw key), and the raw key is shown **once**, with a copy button and a clear "this won't
  be shown again" warning — the standard pattern (GitHub tokens, Stripe keys, etc. all work this way).
- **Revoke** button — invalidates the current key immediately; a new one must be generated to reconnect.
- Only one active key at a time is needed for v1 (AccessPilot is the only consumer today).

### 1.4 New API surface this app exposes (API-key authenticated)

Every request includes the key, e.g. header `Authorization: Bearer <key>` (not a cookie — this is the whole point
of the new method: a stateless, long-lived credential instead of a login session).

| Endpoint | Purpose |
|---|---|
| `GET /api/v2/export.csv` | Same CSV shape as the existing `/api/export.csv` (§1.1's columns) — API-key authenticated instead of session-cookie authenticated. Reusing the CSV shape means AccessPilot's existing, unchanged CSV importer can read it with zero new parsing code. |
| `POST /api/v2/employees/{employeeId}/accesspilot-status` | Body: `{ "directory": "AD" \| "ENTRA", "event": "ACCOUNT_CREATED" \| "ACCOUNT_DISABLED" \| "ACCOUNT_DELETED" \| "LEAVER_PROCESSED", "occurred_at": "<ISO timestamp>" }`. Writes one row into the status strip from §1.2. AccessPilot calls this best-effort, after the fact — this endpoint failing or being unreachable never affects whether AccessPilot's own provisioning succeeded. |

A key is scoped to full read + status-write access for v1 — no need for finer-grained scopes with a single
consumer.

---

## 2. What does NOT change in this new app vs. the first one

- The underlying Employee data model and validation rules (manager cycles, self-reference, dangling references)
  from `HR_SOURCE_OF_TRUTH_APP_PLAN.md` carry over unchanged — this is the same entity, just a fresh app around it
  with API-key auth added.
- CSV export stays the integration format for the pull direction, for the same reason the original plan chose it:
  any HR system can produce a CSV with named columns; a bespoke JSON contract would not travel as well to a future
  HR system.

---

## 3. AccessPilot-side changes (additive only — nothing existing is touched)

### 3.1 New, optional auth method on the HR connection

`HrIntegrationSettings` gains two new, nullable columns — the existing `username`/`password_encrypted` columns
and every existing row stay exactly as they are:

| New column | Purpose |
|---|---|
| `auth_method` | `'SESSION'` (today's default, existing behavior) or `'API_KEY'` (new) |
| `api_key_encrypted` | Fernet-encrypted, same mechanism as every other stored credential |

The **"HR connection" panel** gains an additive auth-method choice (radio: *Username/password* vs. *API key*).
Picking *API key* swaps the username/password fields for a single **API key** paste field. Anyone who never
touches this stays on `SESSION` — their existing setup is byte-for-byte unaffected.

### 3.2 Fetch path: a new branch, not a rewrite

In `_fetch_employee_csv()` (`app/services/hr_integration.py`): when `auth_method == 'API_KEY'`, skip the
`POST /api/login` step entirely and call `GET /api/v2/export.csv` directly with the `Authorization` header built
from the decrypted key. The result still goes through the **exact same, unchanged**
`parse_and_validate_csv()`/`commit_import()` onboarding engine as today — this is the one deliberate design choice
that keeps the whole rest of the pipeline (Joiner, Mover, Leaver, birthright) completely untouched regardless of
which auth method is used to fetch the CSV.

### 3.3 New, best-effort push-back step

After `commit_import()` finishes (still unchanged), a new, additive step — only runs when `auth_method ==
'API_KEY'`, since the original session-cookie HR app has no status endpoint to call — posts one
`POST /api/v2/employees/{employeeId}/accesspilot-status` call per row that had a real outcome this sync (a real
account created, disabled, or deleted; a leaver processed). This never affects the sync's own success/failure —
a push failure is logged and skipped, exactly like every other best-effort side-channel already in this app (e.g.
mirroring a department change to a secondary directory already works this way).

### 3.4 What AccessPilot never does

- Never writes `department`, `status`, `jobTitle`, or any other HR-owned field back into the HR app — the
  provisioning-status endpoint is the only thing AccessPilot ever calls, and it's additive metadata, not identity
  data.
- Never requires the API-key method — it's a second option next to the one already shipped and working.

---

## 4. Build order (once you say go)

1. New standalone app (§1) — independently buildable by another coder, exactly like the first HR app was.
2. Confirm its real `/api/v2/export.csv` and `/api/v2/employees/{id}/accesspilot-status` endpoints against a
   running instance, the same way the first integration was verified against the real running app before any
   AccessPilot code was written — never guessed.
3. AccessPilot: `HrIntegrationSettings` gains the two new columns (migration, additive) + the auth-method branch
   in `_fetch_employee_csv()` + the new best-effort push-back step + the panel's new auth-method toggle.
4. Tests (new auth branch, push-back best-effort behavior) + full suite re-run for zero regressions, exactly like
   every prior round.
5. Live-verify against the real new app: a real API key generated there, pasted into AccessPilot, a real sync, and
   a real status row appearing back on an employee's card.

---

## 5. Open items worth confirming before the other coder starts

- **Push-back granularity**: is per-employee, per-directory status (as scoped above) enough, or do you also want
  a *history* of status events per employee (not just the latest), shown as a small log rather than one line per
  directory? Easy to decide later; doesn't change anything else in this plan.
- **Key rotation**: v1 assumes one active key, replaced by generating a new one (old one dies immediately). Say if
  you'd rather support two active keys briefly during rotation (zero-downtime key swap) — a small addition to §1.3
  if so.
