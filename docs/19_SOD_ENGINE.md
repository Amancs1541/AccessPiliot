# AccessPilot — Separation of Duties (SoD) Engine

**Status: implemented and live.** This document describes the system as it actually works today, not a plan — everything below has been built, tested (39 backend tests in `test_sod.py`), and verified against the real tenant. This revision adds admin-configurable notifications (§16) — SoD now has its own dedicated sidebar section rather than being folded into GOVERNANCE. The previous revision added the mitigation/risk-acceptance workflow (§9), closing the single biggest gap identified when the engine was assessed against mature IGA tools (SailPoint, Saviynt, Entra ID Governance). An earlier revision incorporated a self-review pass that found and fixed three real gaps (both-sides validation, `/check` access control, fail-open logging).

## 1. Purpose

An SoD policy names two sets of entitlements — Set A and Set B — that must never both be held by the same person at the same time. Example: "Payment Initiator" (a group) must never be held alongside "Payment Approver" (another group), because the same person should never be able to both create and approve a payment.

The engine has two halves:
- **Preventive**: blocks a *new* grant from ever completing if it would create a conflict.
- **Detective**: a live scan that reports conflicts that already exist, for any reason (including ones the preventive check never had a chance to stop).

## 2. Data model

Three tables now (migrations `0024_sod_engine`, `0025_sod_exceptions`):

- **`sod_policies`** — `name`, `description`, `severity` (LOW/MEDIUM/HIGH/CRITICAL), `status` (ACTIVE/DISABLED).
- **`sod_policy_entities`** — one row per member of a policy's Set A or Set B. `conflict_side` is `'A'` or `'B'`. `entity_type` is one of:
  - `GROUP` — a real Entra group
  - `ROLE` — a real Entra directory role (e.g. Global Administrator)
  - `APPLICATION` — a specific app role on a specific Enterprise Application (`entity_id` = the application, `app_role_external_id` = the specific role on it)
  - `PACKAGE` — an entire AccessPilot Access Package. This is resolved **live** against the package's current items every time it's checked — never cached. If the package's items change later, the rule's meaning updates automatically with no migration needed.
- **`sod_exceptions`** — the one genuinely *stored* piece of state in this engine (see §9). A formally accepted, time-boxed risk acceptance for a specific `(sod_policy_id, user_id)` pair: `justification`, `granted_by`, `expires_at`, `revoked_at`.

Violations themselves are still computed fresh on every read, never stored — the same pattern the Dashboard's other live-computed panels already use, so there is nothing that can drift out of sync with real access state. `sod_exceptions` is the one deliberate exception to "nothing is stored" in this whole engine, because a risk-acceptance decision is inherently something that must survive across scans, not something derivable from current access state.

**Severity is a label only.** It's shown in the UI and returned in every API response, but nothing in this engine branches on it — a CRITICAL conflict can be overridden or excepted by an Admin/SoDAdmin exactly as easily as a LOW one, and severity plays no role in which rules get checked or how. It exists purely as a triage aid for whoever is reading the violations list, not as an enforcement lever. If severity-gated behavior (e.g. "CRITICAL can never be overridden") is ever wanted, that's unbuilt — a deliberate product decision, not something to infer from the field's presence.

## 3. Who can do what — a deliberate separation of duties on the engine itself

Three roles matter here:

| Role | Can view violations & rules (`SOD_READ`) | Can edit rules or grant exceptions (`SOD_MANAGE`) | Can grant/revoke SoDAdmin (`SOD_ADMIN_ASSIGN`) |
|---|---|---|---|
| `AccessPilot.Admin` | ✅ | ❌ | ✅ |
| `AccessPilot.SoDAdmin` | ✅ | ✅ | ❌ |

`SOD_ADMIN_ASSIGN` is **exclusively an `AccessPilot.Admin` permission** — it is not in `AccessPilot.SoDAdmin`'s permission set at all. This is deliberate and load-bearing for the whole design: an SoDAdmin can create and edit rules (and, as of §9, grant exceptions to them), but cannot grant the SoDAdmin flag to themselves, to another SoDAdmin, or to anyone — only a plain Admin can expand or shrink that roster. Without this restriction, an SoDAdmin could simply add accomplices. Symmetrically, an Admin can see everything and decide *who* governs the rules, but cannot edit the rules themselves, and — just as deliberately — **cannot grant an exception either**: an Admin granting themselves a risk acceptance would be an equally effective way to defeat the engine as an Admin editing the rule directly, which is already forbidden. Confirmed live: an Admin attempting `POST /sod/exceptions` gets 403.

`AccessPilot.SoDAdmin` can be granted two ways, and they're additive (either one is enough):
1. **In-app, instant**: an Admin grants it to any real directory user from the "SoD Administrators" panel on `/admin/sod` (or `POST /api/v1/sod/admins`). Takes effect on that user's very next request — it's checked live, server-side, on every call, not baked into a token.
2. **A real Entra App Role**: if your tenant has an `AccessPilot.SoDAdmin` app role configured on the AccessPilot app registration and a user is assigned it there, it shows up the same way — but only after their access token refreshes (up to ~60 minutes, or immediately via the "Refresh my access" button on the Profile page).

`AccessPilot.SoDAdmin` also has read access to Groups/Roles/Applications/Packages (not manage rights) — without it, the rule-builder's entity pickers would have nothing to show.

## 4. Validation enforced when a rule is saved

Both checked at `POST /policies` and `PATCH /policies/{id}`, before anything is written:

1. **Both sides must be non-empty** — a rule needs at least one entity on Side A and at least one on Side B, or it can never mean anything.
2. **The same entity can never appear on both sides** — the same real `(entity_type, entity_id, app_role_external_id)` tuple cannot be listed as both a Side A and a Side B member of the same policy. This would make the rule fire for literally every holder of that one entitlement — the same "baseline access" failure mode described in §7, just self-inflicted through the rule's own shape rather than a bad entity choice. Rejected with a 422 at save time, not silently accepted.
3. **A submitted duplicate within the same side is deduped**, not rejected — see §7's engineering note on why this needed to be explicit.

An exception (§9) has its own, separate validation: `expires_at` must be in the future — rejected with 422 otherwise, so an exception can never be created already-inert.

Neither policy-save check can see into a *package's* items at save time (a `PACKAGE` entity is only expanded when checked, not when saved) — so a Side A package and a Side B package that happen to share an item today is not currently caught by validation. This is a known gap, not a deliberate design choice; see §14.

## 5. Two data sources — AccessPilot-tracked *and* direct-in-Entra

This is the most important thing to understand about how conflicts are actually detected, and the source of every "why is this flagged / why isn't this flagged" question so far.

A user's real holdings are checked two ways:

1. **AccessPilot-tracked** — an `ACTIVE` `AccessAssignment` row (something granted *through* AccessPilot's own PIM flow).
2. **Direct-in-Entra** — real membership AccessPilot's assignment engine was never involved in granting at all:
   - **Groups**: the already-synced, source-agnostic `UserGroup` table. No extra cost — this table already reflects real membership regardless of how it was granted.
   - **Roles and Application roles**: there's no synced per-user table for these, so this is a **live, on-demand Microsoft Graph read** at check time (`GET /users/{id}/memberOf/microsoft.graph.directoryRole` for roles, the existing app-role-assignments read for applications).

**Fail-open, and distinctly logged.** If the live Graph read fails (outage, throttling, a missing permission), it's treated as "nothing found" rather than blocking the grant or crashing the check — consistent with every other Direct-in-Entra read elsewhere in this app. This path logs a specific warning (`"SoD direct-in-Entra ROLE/APPLICATION check failed for user %s (treated as no match)"`) distinct from a generic Graph error, precisely because "checked, found nothing" and "failed to check, assumed nothing" look identical to the caller otherwise — the log line is the only signal that SoD enforcement was silently incomplete for that one check. This is a monitoring hook, not a behavior change: nothing currently alerts on it.

**Why direct-in-Entra checking matters**: AccessPilot's own login roles (`AccessPilot Admin` / `AccessPilot User`) are a real example of something that is *only ever* direct-in-Entra — nobody is ever granted those roles by AccessPilot's own assignment engine, since they're assigned directly in the Enterprise Application's "Users and groups" blade in Entra. Without the direct-in-Entra check, a rule referencing them would never be able to detect anything, ever, no matter how correctly it was written. (This is also exactly how a real, live violation — a user holding both `AccessPilot Admin` and `AccessPilot User` — was first found in this tenant.)

## 6. Dangling references — what happens when a referenced entity is deleted

Any entity a policy references — a `GROUP`, `ROLE`, `APPLICATION`, or `PACKAGE` — can later be deleted, either in Entra or inside AccessPilot itself. This is handled the same way for all four types, not just packages:

- **Display (`GET /policies`)**: resolving a deleted entity's name fails gracefully — the entity comes back with `entity_display_name: null` and `entity_resolved: false` instead of erroring the whole response. This is how you notice a rule has gone stale — check for `entity_resolved: false` in the policy list.
- **Enforcement (`check_sod_conflicts` / `get_sod_violations`)**: a dangling reference simply never matches anything real (there's nothing left to hold), so it degrades to "this side can never trigger" rather than raising an error. A policy with one live side and one fully-dangling side is inert, silently — it's still `ACTIVE` and still listed, but it can never actually fire. The only way to notice is the same `entity_resolved: false` flag on the display side; there's no separate alert or automatic disabling of a rule that's gone inert this way.

## 7. The mistake that's easy to make: don't put a "baseline access" role in a rule

Live debugging surfaced a real, concrete trap, worth remembering when writing any rule:

A package ("P01-Project") had three items: a group, a directory role, and — accidentally — the `AccessPilot User` application role. That last one isn't a business entitlement; it's the literal role required to sign into AccessPilot at all. Because the engine correctly checks direct-in-Entra holdings, *every single logged-in user* satisfies "holds AccessPilot User" by definition. A rule with that item on one side therefore fires for every user who has ever logged in, regardless of anything else they hold — which usually isn't what was intended.

**The lesson generalizes**: any entity you add to a rule should represent a genuine, specific business entitlement — not a role or group that describes "is a user of this system at all," "is in the default all-staff group," or similar. If a rule fires universally and that's surprising, check whether one of its items is actually a baseline/default access item rather than a real conflict condition. §4's "no entity on both sides" validation catches the same-side-shape version of this mistake; it cannot catch this version, since the universal item is only on one side and is, in isolation, a perfectly validly-shaped entity.

## 8. The three enforcement points

SoD is checked only at the moments access is actually about to become real — never at request or eligibility time. This mirrors AccessPilot's existing PIM philosophy ("nothing about real access changes until it's actually about to become real").

1. **`create_assignment()`'s bypass branch** — an Admin's direct "grant immediately" assignment. Checked before the row is even constructed; a block leaves nothing half-created.
2. **`activate_assignment()`** — the normal self-service Eligible → Active flow (this is what most real usage goes through, including every Access Package item). A plain end-user gets a hard block with no override. An Admin activating on someone else's behalf can pass `override_sod: true` (still requires the existing mandatory justification — no second field).
3. **The scheduled-activation worker** — the background job that activates future-dated admin grants. No interactive user is present here, so a conflict just leaves the grant `SCHEDULED` and logs it, retrying next poll rather than silently granting or crashing.

Package assignments get this for free (they funnel through the same `create_assignment`/`activate_assignment` calls per item) — but per-item override isn't supported inside a package assignment; an Admin who needs to override must use the direct Assignments UI instead.

At all three points, `check_sod_conflicts()` first excludes any conflict covered by an active exception (§9) before deciding whether to block — an excepted conflict behaves as if it isn't a conflict at all for enforcement purposes, while still being visible everywhere else (the violations list, the policy's history) as exactly what it is: a known, accepted risk, not a resolved one.

## 9. Mitigation / risk acceptance — the exception workflow

**Why this exists**: every other part of this engine treats a conflict as a hard binary — blocked, or manually overridden by an Admin *every single time* a new grant is attempted. Real compliance programs don't work that way: some conflicts are reviewed once and formally accepted as tolerable business risk for a bounded period, with the acceptance itself being the auditable record — not a repeated string of individual overrides with no memory between them. Without this, the engine either nags forever or requires an Admin to re-justify the same known risk on every single grant. This was the single largest gap found when comparing AccessPilot's SoD engine against mature IGA tools (SailPoint, Saviynt, Entra ID Governance all have an equivalent concept — "mitigating control," "risk acceptance," "SoD exception").

**Shape**: an exception is scoped to `(sod_policy_id, user_id)` — not to the specific entitlements held at the moment it was granted. The point is "this user is cleared on this rule," not "this exact pair of resources is cleared" — so it keeps applying even if which entitlement satisfies each side changes later (e.g. the user's group membership is swapped for a different group that still lands on the same side of the same rule).

**Lifecycle**:
- **Grant**: `POST /sod/exceptions` (`SOD_MANAGE`, SoDAdmin-only — see §3) with `sod_policy_id`, `user_id`, `justification` (mandatory), `expires_at` (mandatory, must be in the future). Recorded as `SOD_EXCEPTION_GRANTED` with the policy name, user, expiry, and justification in the audit metadata.
- **While active** (`revoked_at IS NULL AND expires_at > now`):
  - The **preventive check** silently lets a new grant through for that user on that rule — no repeated override needed, no `override_sod` flag required.
  - The **detective scan** still reports the violation (it's real — the user genuinely holds both sides) but marks it `exception_active: true` with `exception_expires_at`, so it reads as "known and accepted," not "needs action." Nothing disappears from view — the whole point of an auditable risk-acceptance program is that accepted risks stay visible, not that they vanish.
- **Revoke early**: `DELETE /sod/exceptions/{id}` (`SOD_MANAGE`). The very next grant attempt for that user on that rule is blocked again immediately — there's no grace period. Recorded as `SOD_EXCEPTION_REVOKED`.
- **Natural expiry**: nothing needs to run — `get_active_sod_exception()` simply stops finding it once `expires_at` has passed, since the check is a live comparison against "now," not a scheduled job flipping a status field. An expired-but-not-revoked exception stays in `sod_exceptions` forever as a historical record (`GET /sod/exceptions` shows every exception ever granted, active or not, via a computed `is_active` field — nothing is ever deleted).

**Frontend**: on `/admin/sod`, an open (non-excepted) violation row gets a "Grant exception" button (SoDAdmin only) that opens a small form (justification + expiry, policy and user pre-filled from the row); an excepted row shows an "Accepted until \<date\>" badge instead. A separate "Active Exceptions" panel lists every exception ever granted (active, expired, or revoked) with a "Revoke" action on active ones.

## 10. Overriding a block (distinct from an exception)

`override_sod: true` on `AssignmentCreate` (Admin-only path, since it's only checked in the bypass branch) or `AssignmentActivate` (checked server-side against `actor_roles` — only honored when the caller is an Admin, never for a plain end-user activating their own access). This is a **one-time, per-grant** decision with no memory — the opposite of §9's exception, which is a standing decision covering every future grant until it expires or is revoked. Use an override for a one-off "this specific grant needs to happen right now despite the conflict"; use an exception for "this user/rule combination is a known, ongoing, accepted risk." Either way, the existing mandatory justification field is reused — there's no separate field for the override's reason. An override is recorded on the resulting audit entry (`sod_override: true`).

## 11. Preventive check vs. detective scan — a deliberate performance split

- For a rule made entirely of **GROUP** entities, the detective scan (`GET /sod/violations`) stays on a pure-database path (`AccessAssignment` + `UserGroup`) — fast, no Graph calls, scales to any tenant size.
- The moment a rule has a **ROLE or APPLICATION** entity on either side, there's no cheap synced table to lean on the way `UserGroup` allows for groups — so the detective scan falls back to checking *every* directory user's live Entra state for that specific rule. This is correct but costs one or two Graph calls per user per such rule. In this tenant (a handful of real users) it takes a few seconds; it has not been optimized for a tenant with thousands of users — a future improvement would batch or cache the Graph reads instead of one call per user.
- The **preventive** check never has this problem — it only ever checks the *one* user attempting the *one* grant, so it's always cheap regardless of entity type. The exception lookup (§9) adds one small indexed query per matched conflict — negligible.
- **The Dashboard's violation-count widget triggers its own independent scan** — it calls the same `GET /sod/violations` the SoD page does, on every Dashboard load, with no caching or sharing of results between the two. There is no caching layer anywhere in this app for any live-computed endpoint, so this cost is real and compounds with the point above.

## 12. Error contract

A blocked grant (any of the three points in §8, when no active exception applies) returns HTTP 409 with:

```json
{
  "error": {
    "code": "SOD_CONFLICT",
    "message": "This grant conflicts with Separation-of-Duties policy: <name>.",
    "requestId": "...",
    "details": {
      "conflicts": [
        {"policy_id": "...", "policy_name": "...", "severity": "HIGH"}
      ]
    }
  }
}
```

`POST /sod/check` (the non-blocking pre-check) returns 200 with the full policy objects, not just names/ids:

```json
{ "conflicts": [ /* SodPolicyResponse objects, same shape as GET /policies entries */ ] }
```

An empty `conflicts` array means no conflict was found — it does not distinguish "genuinely no conflict," "a Graph read failed and fell back to no-match" (§5), or "a conflict exists but is covered by an active exception" (§9); none of those are visible in this response, only in the backend logs or by separately checking `GET /sod/exceptions`.

A validation failure at policy or exception save time (§4) returns a generic 422 (`VALIDATION_ERROR`, `"The request contains invalid data."`) — the same generic shape every Pydantic validation failure in this app returns; the specific reason is not surfaced in the response body today.

## 13. API surface

All under `/api/v1/sod`:

| Endpoint | Permission | Purpose |
|---|---|---|
| `GET /policies` | `SOD_READ` | List rules with resolved entity display names. No pagination — fine at current scale. |
| `POST /policies` | `SOD_MANAGE` | Create a rule (both sides in one call), validated per §4 |
| `PATCH /policies/{id}` | `SOD_MANAGE` | Edit a rule (full replace of both sides), same validation |
| `DELETE /policies/{id}` | `SOD_MANAGE` | Delete a rule |
| `GET /violations` | `SOD_READ` | Live detective scan, exception-annotated (§9). Only filter is `?policy_id=`. |
| `POST /check` | any authenticated user, **for themselves only** | Soft, non-blocking pre-check. Checking a `user_id` other than your own returns 403 unless the caller is an `AccessPilot.Admin`. |
| `GET /exceptions` | `SOD_READ` | Every exception ever granted, active or not |
| `POST /exceptions` | `SOD_MANAGE` (SoDAdmin-only, not Admin — see §3) | Grant a time-boxed risk acceptance |
| `DELETE /exceptions/{id}` | `SOD_MANAGE` | Revoke one early |
| `GET /activity` | `SOD_READ` | SoD-relevant audit history — rule and exception changes, roster changes, blocked/overridden grants — filtered out of the general audit log so a plain SoDAdmin (no `AUDIT_READ`) can see it |
| `GET /admins` / `POST /admins` / `DELETE /admins/{user_id}` | `SOD_ADMIN_ASSIGN` (Admin-only, see §3) | Manage who holds the DB-driven `AccessPilot.SoDAdmin` flag |
| `GET /notification-settings` | `SOD_READ` | Read the singleton notification config |
| `PATCH /notification-settings` | `SOD_MANAGE` (SoDAdmin-only, not Admin — see §3 and §16) | Change what triggers a notification |
| `GET /notifications` | `SOD_READ` | The full notification log — **reconciles against current reality on every call** (see §16), so this is the one SoD read endpoint whose cost includes a fresh violations scan every time |
| `POST /notifications/{id}/read`, `POST /notifications/read-all` | `SOD_READ` | Mark one or all notifications read (a single global flag, not per-viewer — see §16) |

## 14. Sidebar structure

Separation of Duties has its own top-level sidebar section (not folded into GOVERNANCE, where it used to live), containing two items — both visible to `AccessPilot.Admin` and, via the `extra: 'sod'` nav-filter check, to a plain end-user holding `AccessPilot.SoDAdmin`:

- **Separation of Duties** (`/admin/sod`) — rules, violations, exceptions, activity (§9, §13).
- **SoD Configuration** (`/admin/sod/configuration`) — notification settings and the full notification log, both described in §16.

## 15. Frontend surfaces

- **`/admin/sod`** — the SoD management page. Rule builder, a live violations table (with per-row "Grant exception" / accepted-until badge), an "Active Exceptions" panel, an "SoD Activity" audit table, and — Admin-only — the SoDAdmin roster panel.
- **`/admin/sod/configuration`** — notification settings (toggles + the exception-expiry warning-days field, SoDAdmin-editable, Admin read-only) and the notification log (§16), with mark-read/mark-all-read actions.
- **Dashboard** — a widget (visible to Admins and SoDAdmins) showing the current live violation count plus the 3 most recent SoD activity entries, linking to `/admin/sod`. See §11 for its real query cost.
- **Topbar Bell icon** — for Admins/SoDAdmins, now a link to `/admin/sod/configuration` with a live unread-count badge (sourced from `GET /sod/notifications`, fetched once per app session by the persistent `Shell` chrome, not on every navigation).
- **My Access → Eligible access** — every eligible item is soft-checked against `/sod/check` on load (always as the caller checking themselves); anything that would conflict if activated shows a `⚠ SoD conflict` badge *before* the user attempts to activate it.

## 16. Notifications

**Why this exists**: even with the mitigation workflow (§9), discovery was still entirely passive — a new violation or a soon-expiring exception was only visible to someone who happened to open the Dashboard or the SoD page. This was the second-biggest gap named in the industry-standard assessment, right after mitigation.

**Two notification types**, each independently toggleable via `sod_notification_settings` (a singleton, same get-or-create pattern as `SecuritySettings`/`BrandingSettings`; defaults to both ON, 7-day exception-expiry warning):
- `NEW_VIOLATION` — a user now holds both sides of an active rule.
- `EXCEPTION_EXPIRING` — an active exception (§9) is within `exception_expiring_warning_days` of `expires_at`.

**How reconciliation works** (`reconcile_sod_notifications()` in `services/sod.py`): for each enabled type, it computes what's currently true (calls `get_sod_violations()` for the first type; queries `sod_exceptions` directly for the second), diffs that against currently-*open* (`resolved_at IS NULL`) `SodNotification` rows for the same `(policy_id, user_id)` or exception id, creates a new row for anything newly true, and marks `resolved_at` on anything that's no longer true (the violation was fixed; the exception was revoked or is no longer within the warning window). This means:
- **No duplicate notifications** — calling reconciliation twice in a row with nothing changed produces zero new rows.
- **Auto-resolution** — fixing a violation (or revoking the exception that was about to expire) resolves the corresponding notification the next time reconciliation runs, with no separate "close this notification" action needed.
- **Full history retained** — nothing is ever deleted; `is_active`-style state is entirely derived from `read_at`/`resolved_at` being null or not.

**Deliberately NOT wired into `GET /sod/violations` or the Dashboard widget** — those are already the expensive path for ROLE/APPLICATION rules (§11), and reconciliation would double that cost on every Dashboard view. Instead it runs exactly once, at the top of `GET /sod/notifications` — which is what both the SoD Configuration page and the topbar Bell's unread-count fetch call. **Confirmed live**: with two APPLICATION-based rules in this tenant, a full reconciliation pass currently takes ~15 seconds (the same per-user Graph-scan cost from §11, now paid once per `GET /sod/notifications` call instead of scattered across every violations read) — this does not block page rendering (the Bell badge just appears a few seconds after the rest of the page), but it is a real, felt delay worth knowing about, and would need the same batching/caching fix §11 already flags if this ever needs to feel instant.

**Read state is global, not per-viewer** — `read_at` is a single flag on the notification row, not tracked per-Admin/SoDAdmin. Whoever marks it read marks it read for everyone. This matches the small-team scale this app targets; a larger deployment with multiple independent SoDAdmins might want per-viewer read state, which isn't built.

**No delivery beyond in-app** — there is no email, Teams, webhook, or any other outbound channel. `sod_notifications` is purely a log an Admin/SoDAdmin has to actually open the app to see (via the Bell badge or the Configuration page) — nothing pushes to anyone. This was a deliberate scope decision: this app has no email/SMTP infrastructure at all today, so real delivery would be new infrastructure, not an extension of an existing pattern.

## 17. What's deliberately not built yet

- **No multi-way (>2-set) rules** — every rule is exactly two sides.
- **No severity-gated enforcement** — see §2; severity is a label only.
- **No cross-package overlap detection at save time** — see §4's last paragraph.
- **Detective scan doesn't scale-test at large tenant size, and the Dashboard widget doesn't cache/share it** — see §11; §16's reconciliation pass inherits the exact same cost.
- **No outbound notification delivery** (email/webhook/etc.) — see §16's last paragraph.
- **No per-viewer notification read state** — see §16.
- **No versioning or change history on rules themselves** — there is no way to answer "what did this rule say at the time a since-resolved violation occurred." Every edit overwrites the rule's entities in place with no record of the prior shape. (Exceptions and notifications, by contrast, now have full history — see §9, §16 — this gap is specifically about `sod_policies`/`sod_policy_entities`.)
