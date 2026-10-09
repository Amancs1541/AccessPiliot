# AccessPilot → IGA maturity roadmap

**Status:** ✅ **All 6 priorities shipped 2026-10-06**, same day, in sequential order, without modifying any existing feature. Each section below carries its own "what actually got built" note. Priority 5 shipped its first concrete increment (Group Owner self-service editing); the rest of that priority's "Option A" (other resource types, other narrow actions) remains open for a future increment if requested.

**Context:** this ranks the highest-leverage gaps between AccessPilot today and a mature commercial IGA tool (SailPoint/Saviynt/Entra ID Governance-class), based on a review of what's already built this session (JML lifecycle, multi-stage Workflow Engine with escalation/conditions, SoD, Access Reviews, Business Roles, Access Packages, PU/TU privileged accounts, a SoC dashboard with a dormant-access widget). The explicit non-goal, by design: chasing connector breadth (SAP/Workday/Salesforce-profile-level provisioning) — that's a decade-long moat for the big platforms and isn't where this product should compete. Depth on the current Entra/Okta-centric surface beats shallow breadth.

Each section below is a standalone plan. They're ordered by recommended build order, not strict dependency — 1 and 2 reinforce each other but either can ship alone; 4 and 6 are deliberately cheaper "quick wins" that don't need to wait for the others.

---

## 1. Review / certification intelligence

✅ **Shipped 2026-10-06.** Built exactly as planned below: `AccessReviewItemResponse.suggested_decision`/`suggestion_reason` (live-computed, SoD conflict checked before dormancy, no suggestion shown when neither fires), a badge + "Apply all suggestions" bundled-decide button in `ReviewItemsTable`. 3 new backend tests, 695 total passing, zero regressions, no migration. Live-verified against the real tenant. See `AccessReview.md` §35 and the 2026-10-06 project-status memory entry for the full record.

**Goal:** surface a suggested decision (Approve / Revoke) per Access Review item, grounded in signals the app already has — not a new AI model, just finally *using* existing data at the moment a reviewer needs it.

**Why it matters:** this was already flagged on an earlier internal backlog and never built. A flat list with no guidance is the #1 real-world failure mode of access certification (reviewers rubber-stamp everything because evaluating each item from scratch is too slow). This is the single highest-leverage, lowest-new-infrastructure item on this whole list.

**Signals available today, no new data collection needed:**
- Dormant access — `AccessAssignment.activated_at` older than the existing `DORMANT_ACCESS_DAYS` threshold (already powers the SoC "Dormant access" widget in `app/services/soc.py`).
- Open SoD conflict — the SoD engine already evaluates conflicts (`app/services/sod.py`); an item whose assignment currently has one is a strong "suggest revoke" signal.
- (Phase 2, once Priority 2 ships) peer-group outlier status.

**Design:**
- New computed fields on `AccessReviewItemResponse`: `suggested_decision: Optional[str]` (`"APPROVED" | "REVOKED" | null`), `suggestion_reason: Optional[str]`.
- New `_suggest_decision(session, item) -> tuple[str | None, str | None]` in `app/services/access_reviews.py`: checks SoD conflict first (strongest signal), then dormancy; returns `(None, None)` — no badge shown — when there's no negative signal, rather than falsely implying confidence in an "approve" with no real evidence behind it.
- Computed live at list-time (same convention as every other "recomputed on read, never stored" dashboard stat in this app) — no new table.

**Frontend:** a small badge next to the resource name in `ReviewItemsTable` ("⚠ suggest revoke — unused 90+ days" / "⚠ suggest revoke — SoD conflict"). An "Apply all suggestions" bulk action, reusing the shared-justification batch-decide pattern just built for Workflow Requests bundling, so an admin can clear a whole campaign's unambiguous items in one pass and hand-review only the ones without a suggestion.

**Effort:** Medium. Mostly signal composition + two small UI additions; no schema change.

**Open questions for whoever plans this properly:** should thresholds (90-day dormancy, etc.) be admin-configurable per campaign, or stay hardcoded like the SoC widget today? Does "Apply all suggestions" need per-item justification or one shared one (lean shared, matching the precedent just shipped)?

---

## 2. Peer / outlier access analytics

✅ **Shipped 2026-10-06.** Built exactly as planned below, reusing the existing SoC builtin-widget plumbing: `compute_access_outliers(session)` in `app/services/soc.py` (10% department-prevalence threshold, 5-person minimum department size, both fixed constants matching the `DORMANT_ACCESS_DAYS` convention), wired into `_compute_builtin`/`compute_widget_drilldown` and auto-surfaced everywhere `_BUILTIN_TITLES` is iterated (the widget builder needed zero frontend changes to list it; only the drilldown row-rendering in `src/App.tsx` needed a new branch for this widget's distinct row shape). 1 new backend test (plus 3 existing widget-count assertions updated 7→8), 696 total passing, zero regressions, no migration. Live-verified against the real tenant with throwaway real users/grants (1-of-5 holder at 20% correctly NOT flagged; 1-of-10 at the 10% threshold correctly flagged with the real resource name resolved; a 100%-prevalent grant never flagged), fully cleaned up. See `AccessReview.md` §36.

**Goal:** flag "this person holds X, but almost nobody else in their department does" as a lightweight anomaly signal — not full UEBA, just prevalence-within-peer-group.

**Why it matters:** this is the headline feature every modern IGA vendor leads with now (SailPoint AI recommendations, Saviynt Risk Exchange, Entra Governance's access-review recommendations). It also directly strengthens Priority 1's suggestions and gives SoC something sharper than dormancy alone.

**Design:**
- For a given resource (Group/Role/Application[+AppRole]), compute what fraction of each department's members hold it. A holder in a department where prevalence is below some threshold (e.g. <10%) is an outlier.
- New service, e.g. `app/services/access_analytics.py`: `compute_department_prevalence(session, resource_type, resource_id) -> dict[str, float]` and `is_access_outlier(session, user_id, resource_type, resource_id) -> bool`.
- New SoC builtin widget "Access outliers," mirroring the existing dormant-access widget's exact plumbing (`_BUILTIN_TITLES` / `_compute_builtin` / `_builtin_drilldown_source`) — proven pattern, low integration risk.

**Performance note:** a live per-resource department aggregate is fine at current tenant scale; flag for a future pre-computed/cached table (a periodic background worker writing a lightweight `AccessOutlierFlag` table) if it ever shows up slow on a larger tenant — don't build that complexity preemptively.

**Effort:** Medium-high — the plumbing is cheap (same shape as an existing widget); the real work is a product decision, not code: what counts as a "peer group" (department alone? department + job title? + employment type?) and what threshold counts as an outlier without flooding reviewers with false positives.

**Open questions:** peer-group definition and outlier threshold need an explicit product decision before building — recommend prototyping against real tenant data first to see what threshold actually produces a sane signal-to-noise ratio, rather than guessing a number.

---

## 3. Entitlement catalog

✅ **Shipped 2026-10-06.** Built largely as planned, with one deliberate design improvement over the original sketch: instead of a one-time backfill job that could go stale the moment a new group/role/app arrives via sync, `list_catalog()` in `app/services/entitlement_catalog.py` live-joins every real entitlement against its catalog row on every read, auto-creating a blank (LOW risk, no description/owner) row for anything missing one — so the catalog is always complete with no separate backfill step to remember to re-run. New `EntitlementCatalogEntry` table (migration `0070_entitlement_catalog`), new `GET/PATCH /api/v1/entitlement-catalog` routes gated by new `ENTITLEMENT_CATALOG_READ`/`MANAGE` permissions (Admin-only for now), a new "Entitlement Catalog" admin page (search + type filter + inline edit of description/risk tier/owner), and surfaced on Access Review item rows (Admin view) as a small description + risk-tier badge next to the resource name. 6 new backend tests, 702 total passing, zero regressions (plus one pre-existing "documented tables" allowlist test updated for the new table). Live-verified against the real tenant: listing auto-created 1,009 real catalog rows (one per real Group/Role/AppRole currently in this tenant) with zero duplicates on a second listing; an update + revert round-trip on a real row confirmed clean.

**Goal:** a catalog of every real entitlement (Group / Role / Application+AppRole) with a plain-English description, a risk tier, and an owner — independent of whether it's bundled into a Package or Business Role yet.

**Why it matters:** Business Roles and Access Packages group coarse resources, but a reviewer or requester only ever sees the raw group/role name. Without a catalog, certification quality caps out at "does this name sound right" — the actual failure mode of most real-world IGA rollouts. This is also the foundation a "risk tier" signal could later feed into Priority 1's suggestions.

**Design:**
- New table `EntitlementCatalogEntry`: `id, resource_type, resource_id, app_role_external_id (nullable), description, risk_tier (LOW/MEDIUM/HIGH/CRITICAL), owner_id (FK users), created_at, updated_at`. A dedicated table (not bolting fields onto `Group`/`Role`) because Application app-roles today only exist as JSON on `Application.app_roles` with no row of their own to extend — a catalog needs to address them individually too.
- Note this deliberately sits *alongside*, not replacing, the existing per-resource-type owner tables (`GroupOwner`, `ApplicationOwner`, `BusinessRoleOwner`, `AccessPackageOwner`) — those are "who's accountable for this container," this is "what does this specific entitlement mean and how risky is it." Worth revisiting whether they should eventually merge, but not a blocker to shipping this.
- One-time backfill job to seed a catalog row (blank description/risk tier, no owner) for every Group/Role/Application+AppRole that exists today, so the catalog starts complete rather than empty.

**Frontend:** new "Entitlement Catalog" admin page (list + inline edit of description/risk tier/owner per entry, filterable by resource type). Surfaced wherever an entitlement is already shown to a decision-maker — Access Review items, Package/Business Role item pickers — so "Finance Approvers" becomes "Finance Approvers — grants PO approval up to $50k (Risk: HIGH, Owner: Jane)."

**Effort:** High — the largest item here. New table + CRUD page + backfill job + several integration touch-points to actually surface the description where it matters (the catalog is worthless if it's a page nobody ever sees it reflected in).

---

## 4. Seeded SoD rule library

✅ **Shipped 2026-10-06.** Built exactly as planned: a curated, static `SOD_RULE_TEMPLATES` library (12 starter conflicts across Finance/IT-Identity/HR/Operations) in `app/services/sod.py` — plain content, not DB rows, since a template describes only the *shape* of a conflict (no real resource ids exist yet). New read-only `GET /api/v1/sod/rule-templates` route (reuses the existing `SOD_READ` permission — no new permission needed). Frontend: a "Start from a template" gallery on the SoD page; picking one pre-fills the existing policy-creation form's name/description/severity and swaps the generic "Side A"/"Side B" labels for the template's own concrete labels (e.g. "Payroll processing" / "Payroll approval"), then the admin maps each side to real Groups/Roles/Applications through the exact same entity picker and `POST /sod/policies` endpoint every other policy already uses — no new creation path. 2 new backend tests, 704 total passing, zero regressions, no migration.

**Goal:** ship a library of common, recognizable SoD conflict templates so an admin doesn't start from a blank rule builder.

**Why it matters:** also an existing, never-built backlog item. Low effort relative to its credibility payoff — a blank SoD rule builder on day one usually just stays empty.

**Design:**
- SoD policies already exist (`SodPolicy`/`SodPolicyEntity`) — this is primarily a content/packaging task, not new architecture. Since real rules need real resource IDs that don't exist until an admin has their own Groups/Roles configured, templates can't just be pre-made `SodPolicy` rows — they need to be a separate `SodRuleTemplate` concept (name, description, category, the *shape* of the conflict — e.g. "AP processing × Vendor management") shown in a gallery, with an "Apply this template" flow that walks the admin through mapping each side to their real resources before creating the actual `SodPolicy`.
- The real work here is curating 10-15 genuinely recognizable starter conflicts (payroll processing vs. payroll approval, user provisioning vs. access approval, AP processing vs. vendor master maintenance, etc.) — as much a content exercise as a coding one.

**Effort:** Medium, and lower-risk than 2 or 3 — a good "quick win" candidate alongside Priority 6.

---

## 5. Scoped / delegated administration

✅ **Shipped 2026-10-06 (first increment).** Went with Option A as recommended. Since "grow the Owner pattern to cover more actions per resource type" was itself under-specified, asked the user which concrete capability to build first; they chose **Group Owner self-service editing**. A Group Owner can now edit their own group's description and Group Label without any Admin/GROUP_MANAGE permission — new `PATCH /api/v1/groups/{id}/owner-update` + `GET /api/v1/groups/owned`, self-authorizing exactly like `owner_rename_package`/`owner_rename_business_role` (ownership checked inside the service, not via a permission dependency). A group's real name stays synced-from-directory and is never owner-editable — description/Group Label are the only two fields a Group has ever had an edit path for at all, so this is the full available surface, not an arbitrary subset. New "My Groups" self-service frontend page, mirroring My Packages/My Business Roles. 5 new backend tests, 709 total passing, zero regressions, no migration (reuses existing `groups.description`/`group_label` columns). Live-verified against the real tenant: made a real user a real owner of `grp-all-staff`, confirmed `/groups/owned` lists it, edited description+label, confirmed the real group name stayed untouched, reverted both fields, deleted the throwaway ownership row — re-confirmed independently afterward that the group and owner table are back to their exact original state. Application Owner and other Owner-pattern extensions (the rest of "Option A") remain open for a future increment if requested.

**Goal:** let an admin's authority be scoped (e.g. "the HR team can only manage HR department identities and policies") instead of today's effectively-binary Admin/User split.

**Why it matters:** a genuine enterprise blocker once an org is past a single central security team holding every Admin credential — not a nice-to-have at that point.

**Design — two options, deliberately presented separately since they're very different sizes:**
- **Option A (lighter, incremental):** extend the **Owner pattern already proven** for Packages/Business Roles/Groups — object-level, non-Admin-gated capability already exists for narrow actions (an owner can rename/remove items from their own package without being a global Admin). Growing this pattern to cover more actions per resource type is a series of small, independent, low-risk additions reusing existing plumbing.
- **Option B (full scoped RBAC):** a real `AdminScope` concept — an Admin-tagged user also gets one or more `AdminScopeAssignment` rows (scope_type: DEPARTMENT/APPLICATION/GROUP + value), and `require_permission` gains a scope-aware variant that every object-listing/mutating service function would need to respect. This is a correct, "real IGA tool" answer, but it's a multi-quarter architectural project — every endpoint that lists or touches a resource needs a scope filter audited in, not just the permission decorator.

**Effort:** Medium (Option A, incremental) vs. Very High (Option B, full rewrite of the permission-checking layer).

**Recommendation:** start with Option A unless/until a specific customer need demands true scoped RBAC — it reuses a pattern that's already shipped and working three times over, versus a ground-up authorization rewrite.

---

## 6. Attestation / compliance exports

✅ **Shipped 2026-10-06 — all 6 priorities now complete.** Went one step beyond the original "browser Print-to-PDF" sketch: `reportlab` turned out to already be a dependency (it powers the existing JML report, `app/services/jml_report.py`), so a true generated PDF was the better, more consistent choice over a second "print this HTML page" convention — still genuinely zero *new* backend dependencies, just reusing the one already there. New `app/services/access_review_report.py` (`build_campaign_report`/`build_campaign_csv`, pure functions over already-hydrated response data, reusing the JML report's own styling helpers) and two new routes, `GET /api/v1/access-reviews/{id}/report.pdf` and `GET /api/v1/access-reviews/{id}/export.csv` (both `ACCESS_REVIEW_READ`-gated, no new permission). Frontend: "Report (PDF)" / "Export (CSV)" buttons on the campaign detail page, downloading via the same blob pattern the JML report button already uses. Compliance-framework control mapping (SOX/SOC2/ISO27001 labels) deferred to a future v2 as planned. 3 new backend tests, 712 total passing, zero regressions, no migration. Live-verified by generating a real PDF+CSV against a real completed campaign ("HR / App Admin / P01 Access Review (test)", 7 real items) — valid PDF header, correct item data in both outputs; purely read-only, so no cleanup was needed.

**Goal:** a polished, exportable record of a completed Access Review campaign (or any approval trail) in the shape an auditor actually asks for.

**Why it matters:** the audit trail data already exists (every decision, justification, decider, timestamp is recorded) — what's missing is the *artifact*. Compliance teams usually need a document to hand over, not API access to a database.

**Design (cheapest-first):**
- A clean, read-only "printable" view (e.g. `/admin/access-reviews/{id}/report`) — campaign metadata, every item with decision/justification/decided-by/decided-at, summary counts — styled for print (`@media print` CSS hides nav/buttons). Browser "Print to PDF" covers the PDF use case with **zero new backend dependencies**.
- A CSV export endpoint for the same data, for anyone who wants to process it rather than file it.
- Compliance-framework control mapping (SOX/SOC2/ISO27001 references shown on the report) is a static labeling exercise worth deferring to a v2 — lower priority than just having the exportable record at all.

**Effort:** Low-medium — the data layer is already done; this is purely a presentation/export layer. Good quick win alongside Priority 4.

---

## 7. Risk-aware routing (follow-on, post-roadmap)

✅ **Shipped 2026-10-08.** Connects two already-shipped pieces that previously didn't talk to each other: the Entitlement Catalog's risk tier (Priority 3) now actually *does* something, instead of only being displayed. Two concrete decisions confirmed via AskUserQuestion before building:
- **Block, don't just warn:** assigning a Package or Business Role containing a HIGH/CRITICAL-risk item (per the Entitlement Catalog) with no workflow attached is now rejected outright (`409 WORKFLOW_REQUIRED_FOR_HIGH_RISK_ITEM`) — covers admin direct-assign, group fan-out, and self-service request alike. An item with no catalog entry yet defaults to LOW (unclassified), so this never blocks on an entitlement nobody has ever risk-rated. New `require_workflow_for_high_risk_items()` in `app/services/entitlement_catalog.py`, wired into `assign_package`/`request_package`/`assign_business_role`.
- **Auto-review offer, opt-in:** marking an Entitlement Catalog entry CRITICAL (with an owner already set) now offers — via a confirm dialog, never silently — to create a recurring 90-day Access Review scoped to that resource, reviewed by the entry's owner. Declining or having no owner set just leaves risk-tier editing working exactly as before.

5 new backend tests, 718 total passing (cumulative with #8 below), zero regressions, no migration. Live-verified against the real tenant: set a real group's risk tier to CRITICAL, confirmed a direct assign attempt was correctly blocked and correctly allowed once a workflow was attached, reverted the risk tier back to LOW afterward — re-confirmed independently clean. Also demonstrated live: a throwaway Package wrapping a real group temporarily marked CRITICAL, with no workflow attached — confirmed blocked in the browser, then fully cleaned up.

---

## 8. Identity Risk Posture (follow-on, post-roadmap)

✅ **Shipped 2026-10-08.** A single rolled-up SoC widget answering "how exposed are we right now" — deliberately a transparent **sum of real counts** (open SoD violations + dormant access + access outliers + overdue Access Reviews), never a fabricated 0-100 "score": this app has no sign-in-risk model to back a weighted score honestly, matching the same reasoning that already kept a fake "high risk users" figure off the Access Review dashboard. New `compute_identity_risk_posture()` in `app/services/soc.py`, wired into the existing builtin-widget plumbing exactly like `access_outliers` — appears automatically in the widget builder, click-through drilldown shows the per-category breakdown. 1 new backend test, no migration. Live-verified against the real tenant (correctly showed `1` — the one real SoD conflict already on this tenant from earlier testing — with the breakdown attributing it to the right category).

---

## 9. Catalog completeness nudge (follow-on, post-roadmap)

✅ **Shipped 2026-10-08.** Closes a real gap found while discussing what to build next: the Entitlement Catalog (#3) and risk-aware routing (#7) both depend entirely on entitlements actually being classified, but nothing ever prompted an admin to do that — every entry silently defaults to LOW forever unless someone happens to visit the page. New `compute_unclassified_entitlements()` in `app/services/entitlement_catalog.py` flags any entry still at its auto-created default (LOW risk, no description, no owner) — the best honest proxy available, since there's no explicit "reviewed" flag distinguishing a deliberate "LOW, nothing to add" classification from one nobody has looked at (same convention as `DORMANT_ACCESS_DAYS` being a proxy for "unused" with no real usage telemetry).

Surfaced in **two places**, deliberately — a SoC-only widget would've been useless here, since `AccessPilot.Admin` (who actually manages the catalog) doesn't have `SOC_READ` at all:
- A **banner directly on the Entitlement Catalog page** ("N entitlements still unclassified"), with a one-click "Show only these" filter toggle — computed client-side from the list already fetched, no extra request.
- A new **"Unclassified entitlements" SoC widget** (for SoCAdmin's own governance-health visibility), wired into the existing builtin plumbing exactly like `access_outliers`.

1 new backend test, 719 total passing, zero regressions, no migration. Live-verified against the real tenant (read-only): correctly showed 1008 of 1009 real catalog entries as unclassified — the 1 excluded being the entry manually classified earlier in this session while testing the catalog feature itself.
