# AccessPilot — Feature Catalog

**What this document is for**: a complete, current inventory of every feature in AccessPilot, written to be fed into
another AI tool to generate architecture diagrams, flowcharts, entity-relationship diagrams, or lifecycle diagrams.
Organized by feature domain, with explicit state machines and data relationships called out wherever a diagram
would naturally represent them. As of 2026-10-10.

---

## 1. What AccessPilot is

AccessPilot is an **Identity Governance and Administration (IGA)** platform. It sits on top of one or more real
identity directories (Microsoft Entra ID, Okta, on-premises Active Directory, and CSV/HR-feed bookkeeping
identities) and governs **who has access to what, for how long, and under whose approval** — without being the
directory itself. It never replaces Entra/Okta/AD as the system of record for authentication; it reads from them,
writes governed changes back to them, and layers a request/approval/review/expiry lifecycle on top that none of
them provide natively.

**Core architecture**: Python/FastAPI backend, PostgreSQL database, a React/TypeScript single-page frontend, and a
set of always-on background workers (asyncio loops started at process boot) that poll every 60 seconds to enforce
time-based rules (expiry, scheduled activation, scheduled deletion, campaign due-dates, etc.) with no push/websocket
infrastructure anywhere — every "live" feel in the UI is short-interval polling.

---

## 2. Identity providers (connectors)

One abstract `IdentityProvider` interface, implemented per connector. Every connector exposes the same shape:
read users/groups/roles/applications, write (enable/disable, update attributes, group membership), and provision
(create a new account/group). A generic `IdentityProvider` database row stores each connected directory's config;
most fields are reused generically across connector types rather than each getting its own schema.

| Connector | Reads | Writes | Provisions | Notes |
|---|---|---|---|---|
| **Microsoft Entra ID** | Users, Groups, Directory Roles, Enterprise Applications (+ app roles) | Enable/disable, attribute edits, group/role/app-role membership | Create user, create group | The only connector used for interactive sign-in |
| **Okta** | Users, Groups | Enable/disable, attribute edits, group membership | Create user | Directory-source only, no login flow wired |
| **Active Directory** (on-prem, via LDAP/LDAPS) | Users, Groups (direct membership only) | Enable/disable, attribute edits, group membership | Create user (with a one-time password), create security group | Reads/writes go either directly from the backend (if network-reachable) or via a lightweight polling **agent** process installed inside the AD network; no native AD "role" or "application" concept, so those are empty lists |
| **CSV / HR feed** | N/A (push-based) | N/A | Bookkeeping-only identity, "graduates" into a real directory account once one becomes available | Used for onboarding before a real directory account exists |
| **Mock** | Fixed in-memory dataset | Same shape as real connectors | Same | Dev/test only |

**Multi-directory account mapping**: one real person is one `User` row in AccessPilot, with a **primary** directory
account plus zero or more additional `IdentityAccount` rows — one per other connected directory they also have an
account in (e.g. both an Entra and an AD account, created together by one Joiner submission). Every grant/revoke
resolves the account in the **specific directory the resource being granted lives in**, not just the person's
primary account — auto-provisioning a new directory account on the spot if they don't have one there yet.

---

## 3. Identity lifecycle — Joiner / Mover / Leaver

```
  JOINER                    MOVER                       LEAVER
  ------                    -----                       ------
  New hire submitted    →   Department/job title    →   Leaver date reached, OR
  (one or more real          title changes               a directory shows them
  directory accounts                                     disabled, OR a manual
  created, DISABLED)                                     "start leaver process"
       |                         |                            |
       v                         v                            v
  Start date reached:      Old birthright access       Accounts disabled in every
  accounts ENABLED,        removed, new access          connected directory
  birthright access        granted; a review of              |
  evaluated (ELIGIBLE)     any access no POLICY               v
                           granted may start          Manager/lifecycle-owner
                                                       approval (if a manual
                                                       request) → real access
                                                       revoked, leaver policy
                                                       actions applied
                                                            |
                                                            v
                                                 (optional) N days later:
                                                 accounts DELETED from every
                                                 directory — unless the person
                                                 was re-enabled first, in which
                                                 case the scheduled deletion is
                                                 cancelled
```

- **Joiner**: one form submission creates a real account in every chosen directory (DISABLED), shows each
  one-time temporary password once, and enables everything automatically on the start date. Directories can be
  picked explicitly, or **auto-detected** from whichever directories the person's department's birthright policies
  would grant access in. Optional profile fields (office, company, phone, address, description) can be set at
  creation time, applied symmetrically to every target directory.
- **Mover**: a department/job-title change (from a directory sync, a CSV row, or an in-app edit) triggers
  re-evaluation of birthright access (old grants matching the old attributes are removed, new ones matching the
  new attributes are granted) and can start a review of any remaining access that no policy explicitly granted.
- **Leaver**: governed by **Leaver Policies** (scoped by department/employment-type, or a Default policy covering
  everyone else) that decide: revoke AccessPilot access? disable accounts in every directory? disable linked
  privileged/test accounts? remove from all groups (even ones AccessPilot didn't grant)? and, optionally, **delete
  the accounts from every directory N days later** (admin-configurable per policy, blank = never). A background
  worker checks every minute for accounts due for deletion; if the person gets re-enabled before that happens, the
  scheduled deletion is automatically and safely cancelled. Every outcome — disabled, deleted, or cancelled — is
  recorded as its own distinct, separately filterable log entry.
- **Re-enabling** a leaver's account always requires a written reason and manager/lifecycle-owner approval — never
  a plain toggle.

---

## 4. Access Assignments — the core PIM (Privileged Identity Management) engine

One generic engine handles every kind of grantable resource (`GROUP`, `ROLE`, `APPLICATION`+role) the same way.
**Nothing about real access changes until the moment it's actually about to become real** — the organizing
principle behind the whole engine.

**State machine:**

```
                 ┌─────────────────┐
  created ──────►│ PENDING_APPROVAL│──approve──┐
  (no approver)  └─────────────────┘           │
       │                                        ▼
       └───────────────────────────────►  ELIGIBLE ◄──────────────┐
                                              │                     │
                                         self-activate          deactivate
                                              ▼                     │
                                          ACTIVE ─────────────────┘
                                              │
                                   expires / admin revokes
                                              ▼
                                      EXPIRED / REVOKED  (terminal)

  Admin "assign immediately" (bypass_activation) ──► ACTIVE directly
  (or SCHEDULED, if the start time is future-dated)
```

- **Durations**: Permanent or Temporary (with a real, worker-enforced expiration time).
- **Approval**: an assignment can name a single approver, or route through a full multi-stage **Workflow**
  (see §9) instead of a single person.
- **Fallback approver**: a second approver who can act if the primary hasn't within a configurable time window.
- **Admin bypass**: skips the eligible/activate dance entirely, for immediate or scheduled real access.
- **Supersede-on-recreate**: re-granting the same target to someone who already holds it revokes the old grant
  first, deferred to the exact moment the new one becomes real.
- **SoD and risk-aware routing enforcement** happen at every point real access is about to become real: bypass
  creation, activation, and the scheduled-activation worker.
- **Cascading activation**: a Group that has an active Group→Role/Application mapping attached delivers those
  linked grants for real in the same action, not as a second separate activation.

---

## 5. Access Packages

Bundles of several Group/Role/Application-role items, assigned as one unit — fans out to one ordinary Assignment
per item, so every other engine feature (approval, activation, SoD, notifications) applies automatically with no
duplicated logic.

- Self-service request (by named individuals or whole groups) or admin direct-assign (to one user or fanned out to
  every current member of a group).
- Each package can set its own default approver, fallback approver, or required Workflow.
- All items from one assignment action share a batch id — grouped in the UI with "approve/reject/revoke all"
  batch actions.
- Editing a package only affects future assignments, never retroactive.

---

## 6. Business Roles

A named bundle of entitlements representing a real job function (e.g. "Finance Analyst"), each item optionally
tagged with an internal IT reference label. Assigned to a person the same way a Package is — one Assignment per
item, same engine, same notifications/SoD/approval machinery.

---

## 7. Group → Role/Application Mapping

Lets an admin declare "membership in this Group should also carry this Role/Application grant" — when the group
grant activates, the mapped grant activates with it in the same action (cascading activation, §4), and tracks
join/leave so the mapped grant follows real group membership changes detected by directory sync.

---

## 8. Birthright Policies

Attribute-based automatic access rules: `department`/`job_title`/`employment_status`/`email` conditions (AND/OR,
simple or multi-condition) that automatically grant a Group, Role, Application, **Access Package**, or **Business
Role** to anyone matching. Evaluated automatically at Joiner/Mover time, and on-demand via a "recheck everyone"
action after a policy is created or edited. A Package/Business-Role action expands into one tagged grant per item.
**Reconciliation**: when a person's attributes change (mover) or a policy changes, access that no longer matches
is automatically revoked (unless the policy is marked "sticky"/non-reconciling) and newly-matching access is
granted — the same mechanism that also determines which directories a new joiner needs accounts in.

---

## 9. Workflows (multi-stage approval engine)

A configurable, ordered sequence of approval stages (each stage: a specific approver, a role/group of approvers,
or a conditional rule like `department EQUALS "IT"`), used as an alternative to a single named approver on an
Assignment, Access Package, Business Role, or Access Review decision. A stage with no matching condition is
skipped, not blocking. Multi-item requests sharing one workflow instance are grouped and can be bulk-decided.

---

## 10. Separation of Duties (SoD)

Rules naming two sets of entitlements (Group/Role/Application-role/whole Package) that must never both be held by
the same person.

- **Preventive** check (blocks a new grant before it completes) at every point real access becomes real, and
  **detective** scan (live, nothing stored — reports conflicts that already exist from any source, including
  access granted directly in a directory, outside AccessPilot entirely).
- **Exceptions**: a time-boxed, justified risk acceptance for a specific (policy, user) pair — preventive checks
  let it through while active; the detective scan still reports it, marked "accepted." Exceptions can be
  self-requested when someone is blocked, and expiring/revoking one automatically revokes the access it covered.
- **Cooldown anti-gaming check**: blocks deactivating one side of a conflict just to immediately activate the
  other.
- Exclusive to a dedicated `SoDAdmin` role — a plain Admin sees nothing SoD-related at all.

---

## 11. Access Reviews (certification campaigns)

Periodic "does this access still make sense" campaigns: scoped to a Group, Application, or Business Role, assigned
to a reviewer, with every current holder listed as a reviewable item.

- **Suggested decisions**: an open SoD conflict or 90+ days of dormancy auto-suggests "revoke," shown as a hint,
  never auto-applied.
- Campaigns can be one-off or recurring (e.g. offered automatically when an entitlement is marked CRITICAL risk).
- Overdue campaigns and items with no decision by the due date are automatically closed/auto-revoked by a
  background worker.
- Exportable as a PDF report or CSV per campaign.

---

## 12. Entitlement Catalog

A risk/metadata classification layer over every real Group/Role/Application-role the directories expose: risk tier
(Low/Medium/High/Critical), description, and an assigned owner — auto-created (defaulting to Low/unclassified) for
every entitlement the first time it's listed, so nothing needs a manual backfill step.

- **Risk-aware routing**: a High/Critical-risk item with no approval workflow configured blocks the request
  outright, across admin-assign, group fan-out, and self-service request paths alike.
- **Unclassified-entitlement nudge**: surfaces anywhere an admin would otherwise silently leave entitlements
  unclassified forever (the catalog page itself, plus a governance-health dashboard widget).

---

## 13. Privileged (PU) and Test (TU) accounts

Shadow accounts linked to a real person for elevated admin work or QA/UAT — deliberately excluded from all
birthright/mover automation (their access is always a deliberate, individual admin decision). Shown on the linked
real person's own page; enable/disable follows the owner's own leaver process.

---

## 14. Non-Human Identity (NHI) governance

Treats service principals / application identities as first-class governed entities: ownership, credential
expiry tracking, risk flags, and a type classification (service principal, managed identity, etc., with manual
override).

---

## 15. Org Chart, Departments, Group Labels

- **Org Chart**: manager/report hierarchy (Employee/Manager tagging + "reports to"), used to resolve the right
  approver for movers and leavers when no explicit approver is configured. A **Department → Manager mapping**
  lets Joiner auto-suggest the right manager the moment a department is picked.
- **Departments**: an admin-managed canonical list (used for consistent birthright matching and Joiner's
  department picker).
- **Group Labels**: an AccessPilot-only classification (Standard/Privileged/custom) on top of a directory group,
  never written back to the directory itself — used for governance reporting, independent of the group's real
  name/membership.

---

## 16. CSV / HR Onboarding

Upload → validate → preview (computed per-row action: create/update/no-change/disable/error) → commit. A
committed row provisions a real directory account via the same engine as Joiner, falling back to a local-only
bookkeeping identity if no real account could be created yet — which **graduates in place** (same internal
identity, swapped to the real account) the moment a real one becomes possible on a later re-upload, never creating
a duplicate person.

---

## 17. Notifications

Two independent systems: a personal per-user feed (assigned/approved/activated/revoked/etc. — never self-notifies
your own actions) delivered via short-interval polling, and a separate SoD-specific feed for the SoDAdmin team. No
outbound email/Teams/webhook delivery anywhere — everything is in-app only.

---

## 18. Audit Logging

Every consequential action (who, what, on what target, the real directory-call result, a request-correlating id)
is recorded. SoD-relevant entries are also filtered into their own feed so a SoDAdmin — who lacks general audit
access — can still see what's relevant to their own domain.

---

## 19. Dashboards

- **Admin Dashboard**: live stat cards, a privileged-role-activation timeline, an access-mix chart, recent
  activity — every number computed live, every card deep-linking into a pre-filtered list page.
- **Security Operations (SoC) dashboard**: a genuinely self-service, drag-and-drop widget dashboard — stat cards,
  timeseries, grouped bar/list widgets built from a generic field-whitelist query engine, click-to-drilldown on
  any chart into the real rows behind it, per-viewer saved layout. Exclusive to a dedicated `SoCAdmin` role. The
  one widget combining every open SoD violation + dormant access + outlier access + overdue review into a single
  **Identity Risk Posture** number lives here too.
- **System Health dashboard**: real infrastructure telemetry (API latency, DB pool/query stats, background-worker
  liveness, connector status) — exclusive to a `ServerAdmin` role, with a **Troubleshooting** drill-down page that
  diagnoses whichever subsystem is currently worst, citing real numbers.

---

## 20. Reports

- **JML (Joiner/Mover/Leaver) PDF report** — a point-in-time snapshot export.
- **Access Review campaign PDF / CSV export**.

---

## 21. Platform / security features

- **Security Settings**: three independent idle-session tiers (blur / lock / auto-logout), a tenant-wide display
  timezone applied everywhere, an IDP-unreachable fallback contact shown on the sign-in screen.
- **Branding**: admin-uploadable sign-in logo, internal logo, "powered by" text.
- **Portal Authentication / Setup wizard**: bootstrap flow for a brand-new install (one-time generated credential,
  never a hardcoded default), guided IDP + Break-Glass account configuration.
- **Break-Glass emergency access**: a hidden, token-only URL, landing on a narrowly-scoped role that must
  explicitly elevate (one more deliberate click) to reach full Admin — never full access by default.

---

## 22. Background workers (always running, ~60s poll interval each)

1. Directory sync scheduler (per-provider, admin-configurable interval)
2. Access expiration sweep (expires ACTIVE assignments past their end time)
3. Scheduled activation (grants real access for future-dated bypass assignments on their start date)
4. SoD exception expiry (revokes access an expired exception was covering)
5. Access review campaign sweep (auto-closes overdue campaigns/items)
6. Joiner activation sweep (enables accounts on their start date, grants birthright access)
7. Leaver sweep (runs the leaver process on the scheduled leaver date)
8. Mover pending-move sweep (applies scheduled department/title changes)
9. **Account deletion sweep** (deletes accounts from every directory once a leaver policy's retention period
   elapses; cancels the scheduled deletion if the person was re-enabled first)

---

## 23. Self-service pages (every signed-in user)

Dashboard, My Access, My Approvals, My Packages, My Business Roles, My Groups (for Group Owners), My Access
Reviews, Request Packages.

## 24. Administration pages

Users, Groups, Roles, Applications, Assignments, Access Packages, Business Roles, Group→Role Mapping, Policies
(Birthright), Entitlement Catalog, Separation of Duties (+ Configuration), Access Reviews, Audit Logs, Joiners,
Movers, Leavers (+ Leaver Policies), Org Chart, Onboarding (CSV), Providers (+ per-provider sync/agent/LDAP
config), Non-Human Identities, Privileged Account Activity, Security Operations dashboard, System Health (+
Troubleshooting), Security Settings, Branding.
