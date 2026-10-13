# Plan — Generic HR App Integration for Joiner/Mover/Leaver

**Scope of this document**: a plan only, nothing built yet. Written so AccessPilot can automatically pull JML
(Joiner/Mover/Leaver) data from **any** HR system — the one just stood up on port 3000, or any other HR app in the
future — without AccessPilot's own existing code changing at all. Every new piece described here is additive; the
existing manual CSV upload flow (`/admin/onboarding`) keeps working exactly as it does today, untouched.

---

## 1. The key insight: this already exists, just manual

AccessPilot already has a complete, working JML ingestion engine — the CSV Onboarding feature
(`app/services/onboarding.py`). It already does everything a JML integration needs:

- A fixed column contract: `employeeId, firstName, lastName, email, department, status` (required), `jobTitle`,
  `effectiveDate`, `leaverDate` (optional).
- Validates every row, previews the computed action (`CREATE` / `UPDATE` / `DISABLE` / `NO_CHANGE` / `ERROR`)
  before anything is written, then commits — provisioning real Entra/AD/Okta accounts, running birthright policy
  evaluation, and (for a `TERMINATED` row) revoking all of that person's access, exactly like a real leaver.
- A full history table (`OnboardingImport`/`OnboardingImportRecord`) already shown on the Onboarding page.

**The only thing missing for a true "integration" is automation** — today, a human has to manually download a CSV
from the HR system and upload it into AccessPilot. Nothing here needs to change to support "any HR app"; it
already does, as long as that HR app can produce a CSV with these column names. What's missing is *fetching that
CSV automatically*, on a schedule, instead of waiting for someone to click Upload.

This is deliberately why **CSV-with-a-fixed-column-contract**, not a custom JSON/REST API shape, is the right
integration format: almost any HR system — including a simple custom-built one like the one just stood up, and
including spreadsheet-style exports from larger commercial HR platforms — can produce a CSV with specific column
names. A bespoke JSON API contract would instead require every HR vendor to build custom integration code against
AccessPilot's own specific shape, which defeats the "any HR app" goal.

---

## 2. What's new (entirely additive)

### 2.1 HR Connection (new, admin-configured)

A new, small settings entity — "where to automatically fetch employee data from":

| Field | Purpose |
|---|---|
| Name | A label, e.g. "Primary HR System" |
| Export URL | The HR app's own CSV export endpoint (e.g. `http://localhost:3000/api/employees/export.csv`) |
| Auth header (optional) | A bearer token or API key, sent as a header when fetching — most real HR systems protect their export endpoint, even an internal one |
| Sync interval (minutes) | Admin-configurable, same pattern already used for directory provider sync and the Joiner provisioning delay — blank/off = never runs automatically, manual upload still always works regardless |
| Enabled | On/off switch |

### 2.2 A new background worker: fetch → feed into the EXISTING engine, unchanged

On its configured interval, for each enabled HR Connection:
1. `GET` the export URL (with the auth header, if configured).
2. Hand the response body to `parse_and_validate_csv()` — **the exact same function the manual upload path
   already calls**, byte for byte. No new validation logic, no new column parsing, no behavior divergence between
   a manual upload and an automated fetch.
3. If validation passes cleanly (no `ERROR` rows), commit it via `commit_import()` — again, the exact same
   function already used today.
4. If the fetch itself fails (URL unreachable, bad auth, non-200 response) or validation finds row errors, the
   import is recorded as failed (reusing the existing `_fail_import`/`ERROR` status) and **nothing is committed**
   — never a partial, broken apply. The next scheduled run tries again.

Every automated run shows up in the **same** Onboarding import history that already exists today — just tagged
with which HR Connection triggered it (vs. "manual upload") — so the existing history page needs only a small,
additive label change, not a new page.

### 2.3 The one real gap worth closing alongside this: manager/hierarchy data

Flagged already in the standalone HR app's own build plan: AccessPilot's CSV importer does not currently read a
manager/reporting-line column at all — that relationship is set up by hand today, inside AccessPilot's own Org
Chart. Since the whole point of connecting a real HR source of truth is to stop re-entering the same data twice,
this plan proposes one small, additive extension to the *existing* column contract:

- A new **optional** `managerId` column — the value is the manager's own `employeeId` (not an internal database
  id), resolved the same way `employeeId` already resolves everything else in this importer.
- A CSV without this column keeps working exactly as it does today — this is purely additive, never a breaking
  change to the existing contract.
- When present, a `CREATE`/`UPDATE` row sets the person's `manager_id` the same way an admin editing it by hand
  in Org Chart already does — reusing that existing field, not inventing a parallel concept.

This is the one piece of actual (small, additive) backend work this plan recommends doing *with* the integration,
since bringing in automated HR data without also bringing in the manager relationships it should include would be
a half-finished integration — but it is still entirely optional/separable if preferred as a later follow-up.

---

## 3. What does NOT change

- `parse_and_validate_csv()` / `commit_import()` / the leaver-revocation logic / birthright evaluation — reused
  completely as-is. The new worker is a thin wrapper around them, not a parallel reimplementation.
- The manual CSV upload page and flow — identical, available side by side with automated sync at all times. An
  admin can always upload a one-off CSV even with an HR Connection configured and running.
- The real-account-provisioning logic (`provision_real_account`), the username/domain-mapping engine, the
  CSV-identity-graduates-to-a-real-account behavior — all already correct, all reused unchanged.

---

## 4. Open decisions (worth confirming before building, not guessed)

1. **One HR Connection, or several at once?** Most organizations have a single HR source of truth, so this plan
   defaults to supporting exactly one active connection for v1 — simpler, and matches how directory-provider sync
   is already one-per-provider. Multiple simultaneous HR sources (e.g. different systems per business unit) is a
   clean later extension of the same design if ever needed, not a redesign.
2. **Auth method**: a single bearer-token/API-key header covers the common case and is what this plan assumes by
   default. If the real HR app (or a future one) needs something else — basic auth, a signed URL, mutual TLS —
   that's a small adjustment to the fetch step only, nothing else in this plan changes.
3. **Does the new HR app's export endpoint exist yet, and does it match the column contract in §1?** If the HR
   app was built from the earlier `HR_SOURCE_OF_TRUTH_APP_PLAN.md`, its export already uses these exact column
   names by design (§6 of that plan) — worth a quick confirmation against the real running app before wiring
   anything up, rather than assuming.

---

## 5. Build order (once this plan is approved)

1. `managerId` as a new optional CSV column (§2.3) — small, additive, independently testable against the existing
   manual upload flow before anything else changes.
2. The HR Connection settings entity (§2.1) — storage and an admin UI to configure it, no automation behavior yet.
3. The scheduled fetch worker (§2.2) — wired to call the existing `parse_and_validate_csv`/`commit_import`
   functions, with its own failure handling and history tagging.
4. Confirm end to end against the real HR app on port 3000: a real change made there (a new employee, a
   department change, an offboarding) shows up in AccessPilot automatically, on schedule, with zero manual upload.
