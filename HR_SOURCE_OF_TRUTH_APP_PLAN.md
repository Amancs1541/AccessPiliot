# Build Plan — Standalone HR "Source of Truth" App

**Purpose**: a simple, independent HR application that becomes the authoritative source of employee data — who
exists, their department/title, and who reports to whom. It is built and runs completely on its own, with no
dependency on AccessPilot. Once it exists, AccessPilot will import from it (CSV export today; a direct API is a
natural later upgrade — see §6). This plan is written so a different developer can build the app from it directly;
no code is included.

---

## 1. What this app must do (scope)

1. Let an HR admin **add, edit, and offboard employees** (basic CRUD, not a full HRIS — no payroll, no leave
   tracking, no benefits).
2. Record **who manages whom**, so the organization's reporting structure is captured as real data, not a
   spreadsheet side-note.
3. Show that structure as a **tree diagram** — the actual feature the user asked for — browsable, searchable, and
   correct even as people join, move, and leave.
4. Produce a clean, periodic **export** (CSV, matching a fixed column contract — see §6) that AccessPilot (or any
   other downstream system) can consume without AccessPilot needing to know anything about this app's internals.

Out of scope, deliberately: payroll, benefits, time-off, performance reviews, document storage, multi-company/
multi-tenant support. Keep it to exactly what governs identity and reporting structure.

---

## 2. Data model

### 2.1 Employee — the one core entity

| Field | Type | Required? | Purpose |
|---|---|---|---|
| `employeeId` | string, unique | **Required** | The stable, permanent identifier for this person. Never reused, never changed once assigned — every downstream system (AccessPilot included) keys off this, not the name or email. |
| `firstName` | string | **Required** | |
| `lastName` | string | **Required** | |
| `email` | string (work email) | **Required** | Must be unique. This becomes the person's directory account identifier downstream. |
| `department` | string | **Required** | Free text, not a fixed enum — matches how AccessPilot treats department itself (an admin-managed list of *known* values, but never a hard constraint on what a person's actual value can be). |
| `jobTitle` | string | Optional | |
| `status` | enum: `ACTIVE` / `TERMINATED` | **Required** | Drives whether the person is a current employee. Don't delete a terminated person's row — keep it, flip the status (see §2.3). |
| `managerId` | string, references another employee's `employeeId` | Optional (required for anyone who isn't the top of the org) | **This is the field the tree diagram is built from.** Null/empty means "this person has no manager" (a root of the tree — see §3). |
| `employeeCategory` | enum: `EMPLOYEE` / `MANAGER` | Optional, but recommended | Whether this person manages anyone. Useful for the tree view (visually distinguish manager nodes) and lets a future integration reuse AccessPilot's own existing vocabulary for this exact concept. |
| `employmentType` | enum: `EMPLOYEE` / `CONTRACTOR` / `INTERN` / `OTHER` | Optional | |
| `startDate` | date | Optional, recommended | When they joined. |
| `leaverDate` | date | Optional | Known/planned last day, if any. |
| `effectiveDate` | date | Optional, only needed if you want scheduled/future-dated changes | If a department or manager change should take effect on a future date rather than immediately — a "nice to have," skip it for v1 if it adds complexity. |

### 2.2 Why `managerId` is the one field that matters most

The tree diagram is nothing more than: take every employee, group them by `managerId`, and recursively nest each
group under their manager. **There is no separate "org chart" data to maintain — the tree is a direct, computed
view of the `managerId` field on every employee.** Get this one field right (and validated — see §2.3) and the
tree diagram is almost free.

### 2.3 Validation rules the app must enforce when `managerId` is set or changed

These are real, inevitable edge cases — resolve them in the app itself, not as an afterthought:

- **A manager must be a real employee.** `managerId` must reference an `employeeId` that actually exists. Reject
  the save otherwise with a clear error, don't silently store a dangling reference.
- **No self-management.** `managerId` can never equal the employee's own `employeeId`.
- **No cycles.** A can't manage B who manages A (directly or through a longer chain). Walk up the chain on every
  save and reject if it loops back to the employee being saved.
- **A terminated employee can still be referenced as someone's manager-of-record** (don't force reassignment the
  moment someone is offboarded) — but the tree view and any "who does X report to" lookup should visually flag
  that the manager is no longer active, and the app should surface a simple **"employees whose manager is inactive"**
  list so HR can reassign them deliberately, on their own schedule, rather than the app silently hiding the
  problem or forcing an immediate fix.
- **More than one employee can have no manager** (an empty `managerId`) — a real org can have a CEO plus, say, an
  independent board advisor tracked in the same system. The tree view should handle multiple root nodes
  gracefully (see §3), not assume there's exactly one.

---

## 3. The tree diagram feature

**Given the data model above, this is a read view, not a separate data-entry feature.**

- **Build**: group all employees by `managerId`. Anyone with no `managerId` (or whose `managerId` doesn't resolve
  to a real employee — shouldn't happen if §2.3's validation is enforced, but defend against it anyway) is a root
  node. Recursively attach each employee under their manager's node.
- **Multiple roots**: render each as its own top-level tree, or group them under a single synthetic "Organization"
  root purely for display — either is fine; pick whichever is simpler to build.
- **Each node shows**: name, job title, department. A manager node (anyone with at least one direct report) should
  look visually distinct from an individual-contributor node.
- **Terminated employees**: show them in the tree, visually de-emphasized (greyed out / a "left" badge), with a
  toggle to hide them entirely — don't just delete them from the view, since their historical position in the
  structure is still real HR information worth keeping visible.
- **Collapsible**: a large org should let you collapse/expand branches, not render everyone flat on one screen.
- **Search**: find a person by name and jump to (expand the path down to) their position in the tree.
- **A circular or orphaned reference should never crash the tree render** — if validation (§2.3) somehow missed
  something (e.g. data imported directly into the database, bypassing the app's own save path), the tree builder
  should detect the cycle, stop recursing, and show that one branch as "data error — needs fixing" rather than
  hanging or crashing the whole page.

---

## 4. Core app features (beyond the tree)

- **Employee list**: searchable/filterable (by department, status, manager), the obvious "front page" of the app.
- **Add / edit employee** form — the fields from §2.1, with the validation from §2.3 enforced at save time.
- **Offboard** action: sets `status = TERMINATED` (+ `leaverDate`) rather than deleting the row.
- **Basic admin authentication** — this app holds real employee data, so even a "simple" version needs a login,
  not an open page. A single admin role is enough for v1; it doesn't need AccessPilot's own multi-role model.
- **Audit trail** (lightweight): who changed what, when — doesn't need to be elaborate, but "department X was
  changed by user Y on date Z" is worth having from day one, since this becomes a system of record.

---

## 5. Suggested shape (kept deliberately simple)

This is a small, standalone app — it doesn't need a heavyweight stack:

- A single `Employee` table/collection (the model in §2.1) plus a minimal `User`/admin-login table for the app's
  own authentication.
- A normal CRUD web app: a backend with a handful of REST endpoints (list/get/create/update/offboard employees,
  plus one endpoint that returns the computed tree structure — §3's grouping logic lives server-side, not
  recomputed in the browser from a flat list every time), and a frontend that's mostly: an employee list/form, and
  a tree-diagram page (any standard tree/org-chart UI library in whatever frontend framework the other developer
  is comfortable with — this is a well-solved UI problem, no need to build tree-rendering from scratch).
- No need for background workers, queues, or anything beyond a standard request/response web app — this system
  doesn't have time-based automation the way AccessPilot does.

---

## 6. Integration with AccessPilot (for later — plan only, not built now)

AccessPilot already has a CSV-based HR onboarding import with a **fixed column contract**:

```
Required columns: employeeId, firstName, lastName, email, department, status
Optional columns: jobTitle, effectiveDate, leaverDate
```

To connect this new app cleanly:

- **v1 integration (simplest)**: this app exports a CSV matching that exact column contract (plus any extra
  columns AccessPilot doesn't read yet — it ignores columns it doesn't recognize, so this is safe to do early).
  An HR admin uploads that CSV into AccessPilot's existing Onboarding page, same as today.
- **The one real gap to flag now**: AccessPilot's current CSV import does **not** yet read a manager/hierarchy
  column — reporting-line data is set up manually inside AccessPilot's own Org Chart today. Once this HR app
  exists as the real source of truth for `managerId`, AccessPilot's CSV importer would need a small follow-up
  change to also read a `managerId` (and ideally `employeeCategory`) column and apply it automatically, instead
  of an admin re-entering the same reporting structure a second time by hand inside AccessPilot. Worth doing as
  the very next step once this app exists — not required for this app's own v1 build.
  - Keep the exported column named `managerId` and have it contain the **other employee's `employeeId`** (not an
    internal database id) — matching the `employeeId`-keyed contract the rest of the CSV already uses, so
    AccessPilot can resolve it the same way it resolves everything else.
- **v2 integration (later, optional)**: a direct API connection (AccessPilot calls this app's REST endpoints on a
  schedule) instead of a manual CSV upload — avoids the manual export/upload step entirely. Not needed for a first
  version; the CSV path is simpler to build and is exactly how AccessPilot already expects to receive HR data
  today.

---

## 7. Build order (suggested)

1. Employee data model + validation rules (§2).
2. Add/edit/offboard employee + list/search page (§4).
3. Tree-diagram view, built from `managerId` (§3).
4. Basic admin login (§4).
5. CSV export matching AccessPilot's column contract (§6).

Steps 1–4 are a complete, usable standalone app on their own — step 5 is what makes it ready to connect to
AccessPilot.
