# Access Review — Setup & Usage Guide

Access Review is AccessPilot's periodic recertification feature: it lets an Admin ask a
reviewer "does this person still need this access?" for a defined set of grants, on a
deadline, with a real consequence (auto-revoke) if nobody answers in time.

This is a **usage guide**. For the full technical design (data model, service internals,
API surface) see the code directly under `backend/app/services/access_reviews.py`,
`backend/app/api/v1/access_reviews.py`, and `backend/app/schemas/access_reviews.py`.

## 1. What it does, in one paragraph

You create a **campaign**: a name, a scope (which grants to review), a due date, and a
reviewer. The moment you create it, AccessPilot takes a **snapshot** — one line item per
matching grant that's currently `ELIGIBLE` or `ACTIVE` — and freezes that list. The
reviewer (or an Admin) then works through the list deciding **Approve** ("still needed,
leave it alone") or **Revoke** ("not needed, take it away — for real, right now") for each
item. Anything still undecided when the due date passes is **auto-revoked** automatically.

Approving an item does **not** grant or refresh anything — the access was already there;
approving just records that someone certified it. Revoking an item calls the exact same
revoke path an Admin would use anywhere else in the app, so it removes real Entra/Graph
access, not just an in-app flag.

## 2. Who can use it — permissions

Two ways to get access, and either is enough:

- **Plain `AccessPilot.Admin`** — already has full Access Review access. No setup needed.
- **`AccessPilot.AccessReviewAdmin`** — a dedicated Entra App Role. Anyone holding it gets
  full Access Review permissions *and* everything a plain Admin can do (it's a superset of
  Admin, not a narrower specialist role like SoDAdmin/NHIAdmin/ServerAdmin). This exists for
  organizations that want to hand out "run access reviews" as its own assignable role
  without also having to hand out the generic Admin role.

**There is no in-app way to grant `AccessPilot.AccessReviewAdmin`.** It's assigned like any
other AccessPilot app role, directly in the Entra/Azure portal:

1. Azure Portal → Microsoft Entra ID → Enterprise Applications → your AccessPilot app
   registration → Users and groups → Add assignment.
2. Pick the user, pick the `AccessPilot.AccessReviewAdmin` role, save.
3. Takes effect on the user's next token refresh (up to ~60 minutes), or immediately if
   they click "Refresh my access" on their Profile page in AccessPilot.

Separately, **any authenticated user** can be picked as a campaign's reviewer or fallback
reviewer, regardless of role — deciding an item you were assigned only requires that the
campaign names you as its reviewer or fallback, not any of the permissions above.

## 3. Creating a campaign

Admin Panel → **Access Reviews** → **New campaign**.

Fields:

| Field | Notes |
|---|---|
| Name / Description | Free text. Description is optional. |
| Scope | See the seven scope types below. |
| Reviewer | Any user. Two auto-suggestions exist (you can always override either): scoping to a specific Application pre-fills from its real Application Owner if one is on file; scoping to one User pre-fills from that user's Manager (Org Chart → `manager_id`), if they have one tagged. |
| Fallback reviewer (optional) | A second person who can also decide items, but only after "Fallback unlocks after" hours have passed since the campaign was created — the same escalation pattern used for approval fallback elsewhere in AccessPilot. |
| Due in (days) | How many days from now the campaign is due. Undecided items are auto-revoked the moment this passes. |
| Recurring campaign (checkbox) | Unchecked by default (one-time). Check it to pick a cadence — Monthly (30 days), Quarterly (90), or Yearly (365). See §8. |

### Scope types

- **Every current grant (`ALL`)** — reviews literally every `ELIGIBLE`/`ACTIVE` assignment
  in the tenant. Use sparingly — this can produce a very large item list.
- **Every Group / Role / Application / Package grant (`RESOURCE_TYPE`)** — every current
  grant of one resource *type*, regardless of which specific Group/Role/etc.
- **One specific Group / Role / Application / Package (`SPECIFIC_RESOURCE`)** — e.g.
  "review everyone who currently holds the Finance group." The most common shape for a
  focused, single-object review.
- **Several specific resources, mixed (`MULTIPLE_RESOURCES`)** — pick a resource type,
  pick a target, click Add, repeat. You can freely mix a Group, a Role, an Application, and
  a Package in the same campaign — useful for "everything related to Project X" style
  reviews that don't fit one resource type.
- **One user's entire access (`USER`)** — every current grant held by one specific person.
- **Every Privileged (PU) or Test (TU) account (`ACCOUNT_TYPE`)** — a standard "quarterly
  privileged access review," scoped to every grant held by accounts tagged PU or TU.
- **Users inactive for N+ days (`INACTIVE_USERS`)** — every current grant held by a user
  whose last real Entra sign-in is older than the threshold you set (or who has never
  signed in). **Needs Microsoft Graph `AuditLog.Read.All`** in addition to the permissions
  AccessPilot already uses — if that scope isn't granted on your tenant, creating this kind
  of campaign fails immediately with a clear `INACTIVE_USER_CHECK_UNAVAILABLE` error rather
  than silently creating a campaign that reviews nobody. Grant the permission in Entra
  (Azure Portal → your app registration → API permissions) to use this scope.

**Scope cannot be changed after creation.** A campaign's item list is a one-time snapshot
of what matched at creation time — see §6 (Editing) for exactly what *can* still be
changed.

If the scope matches zero current grants, the campaign is still created, just with an
empty item list — this isn't an error; it usually just means nobody currently holds that
access. Check the real data (Users/Groups/Packages pages) if that's unexpected.

## 4. Deciding items

Click the campaign's row (a chevron/name toggle) in the list to expand its item roster
in place — no separate page load needed. Each row shows the user (avatar, name, and email —
the same user-cell layout used everywhere else in AccessPilot, e.g. Group Members), the
resource, what status it was in at snapshot time, and its decision. (The dedicated
`/admin/access-reviews/:id` page still exists too, reachable via the small external-link
icon on each row, if you want a shareable direct link to one campaign.)

For each still-`PENDING` item you can:

- **Approve** — records the certification. The underlying access is left completely
  untouched.
- **Revoke** — you'll be asked for a short justification (minimum 3 characters), then the
  real access is removed immediately, exactly as if an Admin had revoked it directly. If a
  Group revoke cascades into linked Group→Role mappings elsewhere in the app, that cascade
  still applies here too — nothing is forked or special-cased for Access Review.

A progress indicator ("N of Total decided", plus "(N removed)" once anything's been
revoked) shows how much of the campaign is left. Whoever created the campaign gets a
notification every time one of its items is approved or revoked, so they can follow along
without keeping the page open.

### Self-service: "My Access Reviews"

Anyone who's a reviewer or fallback reviewer on one or more campaigns can go to
**My Access Reviews** in the main nav (not admin-only) to see and decide every pending
item assigned to them across all campaigns, in one place, without needing Access Review
permissions themselves.

## 5. Deadlines and auto-revoke

A background worker (`Access review worker`, polling every 60 seconds — visible on the
System Health page alongside the other background workers) checks for `ACTIVE` campaigns
whose due date has passed. For each one, every still-`PENDING` item is auto-revoked (same
real revoke as a manual decision, reason recorded as `ACCESS_REVIEW_AUTO_REVOKED`) and the
campaign is marked `COMPLETED`.

You can also close a campaign early yourself: the **Close now** button on the campaign list
does the exact same thing immediately, for campaigns you don't want to wait out.

**There is no "auto re-certify" option.** Nothing happening by the due date always means
revoke, never a silent renewal — if you want to keep access, someone has to explicitly
click Approve before the deadline.

## 6. Editing a campaign

Active campaigns (`status = ACTIVE`) have an **Edit** button on the campaign list. You can
change:

- Name, description
- Reviewer
- Fallback reviewer, and the fallback unlock hours
- Due date
- Frequency (see §8 — turning recurrence on/off, or changing 30/60/90 days, doesn't
  retroactively touch the current campaign, only whatever gets spawned after it)

Reassigning the reviewer or fallback reviewer sends them a fresh notification, and they can
start deciding items immediately — the previous reviewer loses decision rights on that
campaign right away.

**What you cannot edit**: scope (which resources/users/account types are being reviewed),
or the item list itself. This is deliberate, not a missing feature — a campaign's roster is
a fixed snapshot taken the moment it was created; changing scope after the fact would leave
already-snapshotted items silently out of sync with a scope they were never actually taken
from. If you need to review a different or additional set of resources, create a new
campaign.

A `COMPLETED` (or auto-completed) campaign can no longer be edited at all — it's a closed
historical record at that point.

## 7. Where this shows up elsewhere

- **System Health** — the `Access review worker` appears in the background workers list,
  with its own recent-activity count (auto-revoke completions).
- **Audit log** — every campaign create/edit, and every item decision, is recorded via the
  normal audit trail (`ACCESS_REVIEW_CAMPAIGN_CREATED`, `ACCESS_REVIEW_CAMPAIGN_UPDATED`,
  `ACCESS_REVIEW_ITEM_DECIDED`, plus the underlying `ASSIGNMENT_REVOKED` entry for any real
  revoke), same as every other governance action in the app.
- **Notifications** — the reviewer and fallback reviewer get notified when a campaign is
  created and whenever they're (re)assigned via an edit; the campaign's creator gets
  notified every time one of its items is decided (§4).

## 8. Recurring reviews

Checking **"Make this a recurring campaign"** at creation (or later, via Edit) and picking
Monthly, Quarterly, or Yearly makes a campaign self-renewing: the moment it completes —
manually, by every item being decided, or by the due-date auto-revoke sweep — a brand-new
campaign is created automatically with the identical scope, reviewer, and fallback
settings, due at the next cadence point, and linked back to the one that spawned it. This
repeats indefinitely until someone edits the newest campaign to uncheck recurrence, or
simply stops it from completing (it can't recur while still `ACTIVE`).

When a cycle completes and the next one is created, **the reviewer (and the campaign's
creator, if different) gets a notification** — "`<name>` completed its cycle — the next one
is already scheduled, due `<date>`" — so they know ahead of time and can plan other
campaigns or work around it, rather than only finding out when the next one lands in their
queue.

If the automatic re-creation itself fails for some reason (e.g. a `SPECIFIC_RESOURCE`
target was deleted since the last run), the reviewer instead gets a notification explaining
what happened — you'd then create the next one by hand if it's still needed.

**Recurrence is off by default.** Nothing recurs unless you explicitly turn it on.

## 9. Access Review Dashboard

The top of the Access Reviews page shows a live summary panel: total campaigns, how many
are still active vs. completed, how many are set to recur, how many items are still
pending, and how much access has actually been removed across every campaign — plus the
groups and applications that show up most often across every review. It's recomputed on
every load and refreshes automatically in the background (no manual reload needed to see a
decision someone else just made land in the numbers).

There's deliberately no "high risk users" figure here, unlike some vendors' equivalent
dashboards — AccessPilot has no sign-in-risk scoring today, and a fabricated number would
be worse than none.

## 10. Reviewer suggestions, packages, and package owners

- **Reviewer suggestions** (shown under the Reviewer field with a one-click "Use"): for a specific **Package**,
  **Application**, or **Group** scope, that resource's owners (package and application owners are AccessPilot
  records; a group's owners are read live from Entra and are simply absent if the group has none). A **manager**
  suggestion also appears for a single-user scope, or for a group (the manager of most members). A suggestion
  only pre-fills an empty Reviewer; it never overrides one you chose.
- **Package-granted access is grouped for reviewers.** Items a user holds through the same access package show as
  one row (`📦 <package> · N items`) with **Approve all / Revoke all**; expand it to decide each item individually.
  Every item also has a **Granted via** column (package, birthright policy, group role mapping, or direct).
- **Package owners** (set by an Admin when creating/editing a package) get a **My Packages** page where they can
  rename the package and remove items from it — nothing else (no adding items, eligibility, approvers, assigning,
  or deleting). A package must keep at least one item. Removing an item only affects future assignments.

## 11. Fixed day-and-time recurrence, and group owners

- **Two ways to repeat** (tick "Make this a recurring campaign", then pick one — they are mutually exclusive; the
  original Monthly/Quarterly/Yearly option is unchanged):
  1. *Repeat after it completes* — the next campaign is created the moment this one closes.
  2. *Start a new one on day D at HH:MM every N months* — e.g. the 15th of every month at 09:00. Any day 1–31 works
     (31 = last day of shorter months); N can be 1, 3, 6 or 12. Times are in the app's configured timezone
     (Security settings). A background worker starts the new campaign at that moment with the same scope,
     reviewer and fallback — a fresh snapshot of who holds access that day — open for the campaign's
     "Due in (days)". If the previous campaign is still open when the slot arrives, that start is **skipped**
     (the reviewer is notified) and the next slot is tried. The list shows "Next start …" for a scheduled campaign.
- **Group owners**: on a group's page, add AccessPilot-side owners (recorded only in AccessPilot — nothing is written
  to Entra). They are suggested as reviewer when a campaign is scoped to that group, alongside any owners Entra has.

## 12. Reading the outcomes

Every reviewed item ends in exactly one outcome, shown with the same colours on the dashboard and in each campaign row:
**Approved** (green — reviewer kept the access), **Revoked** (red — reviewer removed it), **Auto-revoked** (amber — removed
because the deadline passed or the campaign was closed with the item undecided), **Pending** (grey). "Auto-revoked" is
deliberately not counted as a reviewer decision.

## 13. Movers, effective dates and the Movers page

When a person's department or job title changes (seen by directory sync, an edit in AccessPilot, or a CSV import),
AccessPilot removes the access their old attributes granted, grants what the new ones qualify for, and starts a review
of the access **no policy granted** — including any linked privileged (PU) / test (TU) accounts, which are flagged into
that review but never disabled. The reviewer is the person's manager (Org Chart), else the first "lifecycle owner";
the manager and every lifecycle owner are notified. Set this up, and see every move, on **Movers** (admin).

**Effective dating.** A move can take effect later: add an optional `effectiveDate` column (YYYY-MM-DD) to the CSV, or
use **Schedule a move** on the Movers page. Until that date nothing about the person changes; then a worker applies it
all at once (attribute update pushed to the directory, access swap, review). A newer schedule for the same person
replaces the older one; a scheduled move can be cancelled; if the directory rejects the update the move shows as FAILED.
`effectiveDate` only affects an existing person whose department/title actually changes.

**Leavers and joiners.** When directory sync sees someone switched from active to disabled in Entra/Okta, all their
AccessPilot access is revoked (including in Entra) and their linked PU/TU accounts are disabled, exactly like a CSV
termination; the manager and lifecycle owners are notified. This can be switched off on the Movers page
("Leavers: revoke all access…") — the leaver is then only recorded. New people first seen by sync or created by a CSV
import are recorded as joiners. The Movers page can list Movers, Joiners or Leavers.

## 14. Leaver dates and leaver policies

Give a person a **leaver date** (their user page, or a `leaverDate` column in a CSV import). On that date, at the matching
**leaver policy**'s time (app timezone), the leaver process runs by itself. Manager and lifecycle owners get reminders
first (default 7 and 1 days before). Policies (Movers page) choose who they apply to (everyone / a department / an
employment type), the time of day, the reminders and the actions: revoke all access, disable the account in every
connected IdP, disable linked PU/TU accounts, remove from all groups. The first active policy by priority whose scope
matches wins; the Default covers the rest. The same process runs for a CSV termination, when the directory shows someone
disabled, and from the **Start leaver process now** button on the user page — and it now disables the real accounts,
which a CSV termination previously did not.

## 15. The joiner process

**Joiners** (admin): fill in the new person once — name, work email, employee ID, department, job title, manager, org-chart role,
employment type, start date/time, optional leaver date — and tick the IdPs to create accounts in (each connected directory has a
default "joiner target" switch). AccessPilot creates the account in every chosen IdP **disabled**, shows each **one-time temporary
password** (never stored), and on the start date a worker enables all accounts, marks the person active, makes their birthright access
eligible and notifies the manager/lifecycle owners. "Start immediately" does all of that at once. If one IdP fails, the others still
succeed and the joiner is PARTIAL — **Retry failed** creates the missing account. A scheduled joiner can be cancelled; accounts already
created stay in the directories, disabled. The leaver date entered here feeds the leaver process (§14).

## 16. If nobody decides by the due date

When creating a campaign you can choose what happens to items still undecided at the due date: **Revoke the access** (default, unchanged behaviour: items become Auto-revoked) or **Keep the access** (items are auto-approved). Recurring and scheduled campaigns carry the choice forward. Note: the *Inactive users* scope needs Microsoft Entra ID P1/P2 licensing in addition to the AuditLog.Read.All permission.

## 17. After the leaver process: re-enable, deletion, report

- **Re-enable needs approval.** Once the leaver process has run, the Enable buttons on the user's Accounts panel ask for a valid reason (min. 10 characters) and create a request. The person's manager approves it (lifecycle owners when there is no manager; admins can also decide; nobody approves their own request). Managers see requests on *My Access Reviews*; admins see all on the *Movers* page. Approval enables the accounts in every IdP and resets the leaver cycle; access is NOT restored. The leaver date can no longer be edited after the process ran.
- **Delete accounts after N days.** Each leaver policy has *Delete accounts from all IdPs after (days)* (blank = never). When the time comes a worker deletes the person's account in every IdP (irreversible; Entra keeps deleted users 30 days). The person stays in AccessPilot labelled *Deleted*, with an audit entry. Deletion waits while a re-enable request is pending, and a deleted person can only return as a new joiner.
- **JML report.** *Movers* page: **Download JML report (PDF)**: summary, policies, joiners with accounts, movers with revoked items and reviews, leavers with per-IdP results, scheduled leavers, deletions, re-enable requests.

## 18. Manual "Start leaver process now" (approval flow)

Button on the user page (and *Run now* in Scheduled leavers): **justification** (min. 10 characters) -> the person's accounts are **disabled in every IdP immediately** -> the **manager** (lifecycle owners if none; admins can also decide; the initiator cannot) **approves or denies**. *Approved*: the leaver process runs (access revoked etc., per the leaver policy). *Denied*: the accounts that were active are **enabled again** and the admin who started it is notified. While a request is pending the account cannot be enabled by hand. If no directory can disable the account, no request is created. Managers decide on *My Access Reviews*; admins see all on *Movers* > Leaver requests. Automatic leavers (leaver date, CSV, directory-disabled) do not need approval. If a processed leaver is enabled directly in the directory, sync alerts the manager and lifecycle owners (nothing is changed automatically).

## 19. Leavers has its own page

*Leavers* is now a separate sidebar page (`/admin/leavers`), next to *Joiners* and *Movers* — no longer a tab mixed into Movers. It has the manual leaver requests waiting on a manager, re-enable requests, scheduled leaver dates, leaver policies, the JML PDF report button, and the leaver-only activity log. The Movers page keeps the shared global settings (revoke access on directory-disable, lifecycle owners) since those also drive Leaver approver fallback.

## 20. Re-enabling a leaver's account is scoped to what you clicked

Clicking **Enable** on one IdP account for a leaver asks for a reason and sends the manager (or a lifecycle owner) an approval request scoped to **just that one account** — approving it enables only that account, the others stay exactly as they are. Clicking **Enable in all IdPs** does the same but scoped to every account. The pending request (visible on the user's Leaver tab and on the Leavers page) shows which scope it covers. The person is only treated as fully "returned" (leaver cycle reset, a new leaver date can be set) once every account is active again — a partial approval keeps them recorded as a leaver until the rest come back too.

## 21. Business Roles (Entitlement Management, Step 1)

*Business Roles* (`/admin/business-roles`, next to Access Packages) is a named, owned bundle of real entitlements — map an Entra group, directory role, or application role onto one business-facing role (e.g. "Finance Analyst") instead of managing five separate group memberships. Each mapping row carries an optional **IT Role label** (what IT itself calls that technical access level) and inherits the underlying resource's **Resource Code** and **Naming Convention** — two purely cosmetic reference fields (editable per-resource via "Code/Naming" on any mapped item, or "Edit reference" on the Unmapped entitlements list below the table) that cross-reference AccessPilot's own record of that resource, never used for any internal lookup. One raw entitlement can only belong to one Business Role at a time. Lifecycle: DRAFT → ACTIVE → DISABLED → ARCHIVED (archived once it has assignment history; deletable outright until then).

## 22. Assigning a Business Role (Step 3)

On the Business Roles page, an **ACTIVE** role gets an **Assign** button: pick a person, Permanent or Temporary (with an expiration), an optional approver (leave blank to land the grant directly ELIGIBLE), and a justification. This fans out to one ordinary access grant per mapped item — the same approval/activation/SoD path every other grant in AccessPilot uses, so nothing is actually real until it's activated. The **Holders** column shows how many people currently hold the role; clicking it expands the list with each person's per-item status. A role with holders archives instead of deleting outright when removed.

If an approver is set, each pending item on the admin **Assignments** page and on the approver's **My Approvals** page shows which Business Role it belongs to (a small "🏷 Role Name" line under the resource), so an approver reviewing a raw group/role/application grant can tell at a glance it's part of a named Business Role rather than a one-off request. This also shows on decided (already approved/denied) rows for the same reason it already shows the package name.

When a Business Role has more than one mapped item, assigning it no longer shows one separate row per item — the Assignments list and My Approvals both collapse it into a single "🏷 Role Name (N items)" row, click to expand and see each underlying item's own status, with **Approve all / Reject all / Revoke all** acting on every item at once. This is the same grouping Access Packages already use.

## 23. Business Roles are now a first-class citizen everywhere else (Steps 4–5)

A Business Role can now be referenced anywhere a Package, Group, Role, or Application already could:

- **Conditional auto-assignment**: a **Birthright policy** can grant a Business Role (match on department/job title, "Grant" = Business Role) — every mapped item is granted automatically, tagged so it reconciles (auto-revokes if the person stops matching) exactly like any other birthright grant. A **Group-role mapping** can do the same, membership-triggered instead of attribute-triggered. Either way, a role that's DRAFT/DISABLED/ARCHIVED simply grants nothing — the safe default.
- **Separation of Duties**: an SoD rule can name a Business Role as one side of a conflict (next to Group/Role/Application/Package) — it's resolved live against the role's current items, so editing the role's mapping is reflected the next time a conflict check runs, no need to update the SoD rule itself.
- **Access Review**: a campaign can be scoped to one specific Business Role (or include it in a mixed multi-resource campaign) — it captures only the grants that actually came from that role, reports "Business Role: X" (plus "via birthright: Y" when relevant) as the source, and Business Role owners are suggested as reviewers the same way package/application owners already are. A role's several items collapse into one expandable row in the review, the same batching the Assignments/My Approvals pages already do.

## 24. Business Role Analytics and the owner portal (Step 6 — plan complete)

The admin **Business Roles** page now opens with an **analytics panel**: total roles by status (Draft/Active/Disabled/Archived), privileged roles, how many people currently hold any role, unmapped entitlements, roles with no owner, roles with an **open** SoD conflict (a real violation right now — not just "referenced in a rule"), and the most-held roles. Everything here is computed live on every page load, never stored.

Business Role **owners** (set on the create/edit form) get a narrow self-service portal at **My Business Roles** (visible to everyone; empty for non-owners) — the same model Access Packages already use. An owner can **rename** the role and **remove one mapped item** at a time (never emptying it completely); everything else — status, approvers, owners, assigning it to people, deleting it — stays Admin-only. This closes out the full Business Role plan from Steps 1 through 6.

## 25. "Dormant access" on the Security Operations dashboard

The **Security Operations** dashboard (`AccessPilot.SoCAdmin` only) has a new built-in widget: **Dormant access (90+ days)** — every currently-ACTIVE grant that was activated 90 or more days ago and never touched since. This is the honest proxy this app can offer without real sign-in/usage telemetry (the same limitation already noted for the Inactive Users review scope) — it flags standing access that's been sitting active a long time and may be worth a fresh look, not necessarily unused access in a literal sense. Click the card to see the real list behind the count, same as every other widget on this dashboard.

## 26. Workflow / Approval Engine — a new, standalone multi-stage approval capability

Admins can now build real **multi-stage approval workflows** under **Workflow Definitions** (Admin) — an ordered list of stages, each with:
- **Any one approver, or every approver** (`ANY_OF` / `ALL_OF`) must decide before the stage moves on.
- **Fallback approvers + an escalation window** (optional) — if nobody named has decided within N hours, the fallback approvers are notified and may step in; once they do, their decision finalizes the stage outright, the same way a fallback approver already works everywhere else in AccessPilot.
- **A condition** (optional) — the stage only activates if a detail on the request matches a value (e.g. only run a "Finance sign-off" stage when the requester filled in `department = Finance`); otherwise it's skipped automatically. If every stage in a workflow gets skipped, the request is approved immediately with no human decision needed.

Any user can submit a request against an **ACTIVE** workflow from the **Workflow Requests** page — pick the workflow, give it a title and justification, optionally fill in extra details the workflow's conditions might check, and submit. The same page shows **My requests** (status and full stage-by-stage history) and **Pending my decision** (an inline approve/reject with justification for anything waiting on you).

**Multiple items from the same request bundle into one card.** A multi-item Access Package (or Business Role) assignment, or a multi-item workflow-routed Access Review campaign, gives each item its own independent approval chain so they can each move at their own pace — but on this page they show up together as one bundle (each item's own status still visible) with a single shared justification and **Approve all / Reject all**, instead of as separate unrelated-looking cards.

This is a deliberately **separate, new capability** — it does not replace or get wired into the approval steps Assignments, Access Packages, Business Roles, or Access Reviews already use; those keep working exactly as they always have. A decision can never be taken back once made, and a request can only be cancelled by its requester or an Admin while it's still pending.

## 27. A real Group/Role/Application/Package/Business Role grant can be routed through a Workflow

Everywhere you could already pick an "Approver (optional)" when assigning access — the admin **Assignments** page, an **Access Package**'s Assign panel, a **Business Role**'s Assign panel — there's now a third choice alongside "No approval required" / "Single approver": **Workflow**. Pick one of your ACTIVE workflow definitions instead, and the grant waits on that multi-stage chain instead of one person.

A few things worth knowing:
- The grant lands **PENDING_APPROVAL** with no single approver shown — the workflow's own stages are the approval. Once every stage finishes, it becomes **ELIGIBLE** (never straight to ACTIVE) for the person to self-activate, exactly like a single-approver grant already works.
- Any condition a workflow stage checks (e.g. "only run this stage if department = Finance") is matched against the **real target person's own directory record** — their actual department, job title, employment type, and account type — never anything typed into a form. When authoring a condition in **Workflow Definitions**, typing "department" as the field swaps the value box to a real department picker, the same one Birthright policies already use.
- Cancelling a workflow-routed grant (or having it rejected at any stage) sets the assignment to REJECTED — the exact same outcome a human-rejected request already produces.

## 28. Editing a person's department or job title can be routed through a Workflow too

On a user's **User Detail** page, the "Department & job title" edit now has an **Approval** choice: apply immediately (today's behavior — a real write pushes to Entra/Okta right away and birthright access re-evaluates on the spot), or route it through a workflow instead.

Pick a workflow and save: nothing happens to the real directory record yet. The edit sits pending — the page shows a banner saying so — until the workflow's stages finish. Only once it's **approved** does the real write actually go to Entra/Okta, the local record update, and the usual birthright "mover" re-evaluation (losing access a policy no longer grants, gaining anything newly matching) all happen — exactly the same sequence as the instant path, just held until a human (or chain of humans) signs off. If it's **rejected**, the person's record is left completely untouched.

This is explicitly scoped to the attribute edit on an existing person. Workflow-routing was NOT added to Joiner (new-hire onboarding) or to Birthright Policy/Group-Role-Mapping's own automated grants — those keep working exactly as they do today.

## 29. Access Review Campaigns can be routed through a Workflow

When creating a campaign under **Access Reviews**, the **Reviewer** field is now a choice: a **Single reviewer** (today's behavior — one person, plus an optional fallback after a wait period) or a **Workflow**. A campaign always needs one or the other — unlike a one-off access grant, a review can't be left with no decision path at all.

Pick a workflow and every item the campaign snapshots gets its **own independent, multi-stage approval chain** — reviewing 20 people's access with a workflow starts 20 separate chains, each able to move at its own pace, not one shared decision for the whole campaign. Certifying an item (workflow approved) never touches the real grant, exactly like a human reviewer's "Approve" today; the workflow rejecting an item revokes the real access for real, exactly like "Revoke." Decisions on these items happen from the **Workflow Requests** page, not the review item's own Approve/Revoke buttons (which show "Decide from Workflow Requests" instead once an item is routed this way).

If the campaign closes — its due date passes, or an admin closes it early — before an item's workflow has finished, that item is resolved exactly the way an undecided reviewer-mode item already is: per the campaign's **"If nobody decides by the due date"** setting, either kept (certified) or auto-revoked for real. A workflow-routed campaign's reviewer can't be reassigned after creation (there's no single reviewer slot to reassign) — everything else about it (name, due date, recurrence) stays editable as before.

## 30. Access Packages: workflow-routing extended to group assignment and self-service requests

Two gaps closed from the Access Package workflow-routing added earlier:

- **Assigning a package to everyone in a group** can now also be routed through a Workflow (previously only assigning to one named person could be) — each group member gets their own independent approval chain, the same as assigning to a group with a single approver already works.
- **Self-service requests** — when an eligible person requests a package themselves from **Request Packages** — can now be routed through a Workflow too. A package's **Approval** setting (set when creating it, or changed later from its eligibility panel) is now a 3-way choice: no approval required, a single default approver, or a workflow. Picking a workflow means every self-service request for that package starts its own approval chain instead of waiting on one person.
- **Admin's "Assign" panel now defaults to the package's own workflow too** (added 2026-10-05, after a real "the workflow isn't working" report traced to an admin forgetting to pick it manually): opening **Assign** for a package that already has a configured workflow pre-selects "Workflow" with that workflow chosen — for both a single person and a whole group. It's still just a default, not a lock — the admin can switch it to a single approver or no approval for that one assignment if they need to.
- **A workflow made only of conditional stages has no catch-all.** If a stage's condition doesn't match (e.g. "department = Finance" and the person's department is blank or different), that stage is skipped — and if every stage gets skipped, the request auto-approves with **zero human decisions**, which can look like "the workflow did nothing." If you want a workflow to always require someone's sign-off regardless of who's asking, give it a final stage with **no condition** as a catch-all.

## 31. Group Label — classify a Group as Standard, Privileged, or a custom name

When creating a new group, there's now a **Label** field alongside its name and description: the two built-ins **Standard** or **Privileged**, or any custom label your team has defined. This is purely an AccessPilot governance classification — it is never written to Entra/Okta, and it doesn't change anything about the group's real permissions. It shows up on the group's own detail page and in the admin Groups list.

Custom labels are managed from **Policies → Group Labels** — the same "small managed list" pattern Departments already uses: type a name, add it, and it's immediately selectable the next time someone creates a group. A label is only picked **at the moment a group is created** — there's currently no way to relabel a group afterward, and a group synced in from Entra (rather than created in AccessPilot) has no label until that capability is added.

## 32. Joiner is now the only way to create an identity in AccessPilot

The Users page's "Add user" button is gone. From now on, every new identity in AccessPilot is created through the **Joiner** process — accounts created disabled across every chosen directory, switched on automatically at the person's start date. This closes the gap where an identity could previously be created directly, bypassing scheduling, multi-directory provisioning, and the manager/department data Joiner already collects.

## 33. Joiner: Manager is now required, and it's suggested from the person's Department

Submitting a new joiner now requires a **Manager** — it's no longer optional. To make picking one easier, **Policies → Org Chart** has a new **Department → Manager mapping** section: set one manager per department once, and from then on, picking that department on the Joiner form automatically fills in the matching manager (you can still change it for any individual joiner — it's a suggestion, not a lock).

## 34. Joiner submissions notify the manager, for audit/awareness

The moment a joiner is submitted, their assigned manager gets a notification that someone new reports to them and when they're starting — purely informational, there's nothing for the manager to approve or act on. This fires immediately on submission, even for a joiner who doesn't start for weeks, so the manager always knows ahead of time rather than only finding out on the actual start date.

## 35. Access Review items now come with a suggestion

Reviewing access one item at a time with no guidance is slow and tends to turn into rubber-stamping. Each pending item in an Access Review now shows a small "⚠ suggest revoke — \<reason\>" note when one of two signals fires: the item currently has an **open SoD conflict** with something else the person holds, or the real grant has sat **unused for 90+ days** (the same threshold the Security Operations dashboard's "Dormant access" widget already uses). An SoD conflict always wins over dormancy when both are true, since it's the stronger signal.

This is a nudge from data the app already tracks, not a prediction — if neither signal fires, **no suggestion is shown at all**, deliberately. An absence of red flags isn't treated as a positive "this looks fine," since that would be a confidence the app doesn't actually have.

An **"Apply all suggestions"** button appears whenever a campaign has one or more flagged items — one shared justification, then every flagged item is decided in one pass (reusing the same bundled-decision mechanism Workflow Requests uses for multi-item approvals). Items without a suggestion are left for the reviewer to look at individually, same as before.

## 36. Security Operations dashboard: Access outliers widget

A new built-in SoC widget, "Access outliers," flags grants that are unusually rare within a person's own department — the same "peer group" idea every modern IGA tool now leads with, built here as a lightweight prevalence check rather than full behavioral analytics.

For every real (ACTIVE, NORMAL-account) grant, the widget computes what fraction of the holder's department holds that exact same entitlement (same resource, same app role where relevant). A holder is flagged when their department's prevalence for that entitlement is 10% or less — e.g. one person in a 20-person department holding an admin role nobody else on their team has. Departments with fewer than 5 real people are skipped entirely, since prevalence percentages aren't meaningful (and tend to produce false positives) in a tiny group.

Like the existing "Dormant access" and "Open SoD violations" widgets, this is computed live on every view — nothing is stored or pre-materialized, so it's always current. Add it from the SoC dashboard's widget builder like any other built-in panel; click through for the full list of flagged holders, their department's prevalence percentage, and how many people in that department hold the same thing.

## 37. Entitlement Catalog

A new **Entitlement Catalog** admin page (under Governance) gives every real Group, Role, and Application AppRole a plain-English description, a risk tier (Low/Medium/High/Critical), and an accountable owner — independent of whether it's bundled into a Package or Business Role yet. Without this, a reviewer or requester only ever sees a raw technical name like "grp-finance-approvers"; the catalog is what turns that into "Finance Approvers — grants PO approval up to $50k (Risk: High, Owner: Jane)."

The catalog starts complete automatically: the first time anyone opens the page, every real entitlement in the tenant gets a blank starter row (Low risk, no description yet) if it doesn't already have one — there's no setup step, and a newly synced group or a newly added AppRole gets its own row the next time the page is opened, with nothing going stale. An Admin fills in the description, risk tier, and owner inline, right in the table; everything else keeps working exactly as before until someone does.

Once set, a description and risk tier also appear directly on Access Review items (for the reviewing Admin) next to the resource name — so a reviewer deciding whether to keep or revoke something no longer has to go guess what a cryptic group name actually grants.

## 38. Separation of Duties: starter rule templates

Building a new SoD rule used to mean starting from a completely blank form. The SoD page now has a **"Start from a template"** gallery of 12 recognizable conflict shapes (payroll processing vs. payroll approval, user provisioning vs. access approval, AP processing vs. vendor master maintenance, and nine more across Finance, IT/Identity, HR, and Operations).

Picking a template pre-fills the new policy's name, description, and a sensible default severity, and relabels each side of the form with the template's own concrete language (e.g. "Payroll processing / data entry" instead of a generic "Side A") — you still map each side to your own real Groups, Roles, or Applications before saving, the same as building a rule from scratch. Nothing about creating a rule manually changes; the gallery is purely a faster starting point.

## 39. Group Owners can now self-service their group's description and label

A Group Owner (set from the group's Owners list, same as before) can now edit their own group's description and Group Label from a new **My Groups** page — no Admin role needed. This mirrors the self-service portal Package and Business Role owners already had: narrow, cosmetic-only editing, nothing that touches membership, privilege, or the group's real identity.

The group's real name always stays whatever the directory (Entra/Okta) has it as — that never becomes owner-editable, or Admin-editable either; description and Group Label are the only two fields a Group has ever had an edit path for at all. Everything else about managing a group — who else owns it, deleting it, anything structural — still requires an Admin.

## 40. Access Review campaigns can now be exported as a PDF report or CSV

Every Access Review campaign's detail page now has **"Report (PDF)"** and **"Export (CSV)"** buttons — a polished, shareable attestation record for an auditor or compliance team, so the record doesn't only live inside AccessPilot's own UI.

The PDF covers the campaign's metadata (status, scope, reviewer, due date), summary counts (approved/revoked/auto-revoked/pending), and every item with its decision, justification, decider, and when it was decided. The CSV carries the same per-item data for anyone who'd rather process it than file it. Both work on a campaign at any stage — completed or still in progress — and reflect exactly what the campaign's own detail page already shows.

## 41. Entitlement risk tier now actually does something: workflow-required routing + auto-review offer

Previously the Entitlement Catalog's risk tier (Low/Medium/High/Critical) was purely informational. It now has two real effects:

- **Assigning a HIGH or CRITICAL-risk entitlement via a Package or Business Role — admin direct-assign, group fan-out, or self-service request — now requires a workflow.** If no workflow is attached, the assign/request is rejected with a clear error naming the entitlement and its risk tier, instead of silently going through on a single approver (or no approval at all). An entitlement nobody has ever risk-rated still defaults to Low and is never blocked.
- **Marking an entry CRITICAL (with an owner already set) now offers to set up a recurring Access Review for it** — every 90 days, reviewed by the entry's owner. This is a confirm-dialog offer, never silent: decline it and nothing changes; there's also no offer at all if the entry has no owner set yet.

## 42. Security Operations dashboard: Identity Risk Posture widget

A new built-in SoC widget, "Identity Risk Posture," rolls up every open risk signal this app already tracks into one number: open SoD violations, dormant access (90+ days), access outliers, and Access Review campaigns past their due date. Deliberately **not** a fabricated 0-100 score — just an honest total of real counts, since this app has no sign-in-risk model to back a weighted score with. Click through for the breakdown by category.

## 43. Entitlement Catalog: unclassified-entries nudge

The Entitlement Catalog page now shows a banner when entitlements are still sitting at their auto-created default (Low risk, no description, no owner) — "N entitlements still unclassified" — with a one-click toggle to filter the table down to just those. Without this, the catalog could look complete at a glance while almost nothing in it had actually been reviewed by anyone.

A matching "Unclassified entitlements" widget is also available on the Security Operations dashboard, for governance-health visibility alongside the other SoC signals.

## 44. Active Directory connector — Phase 1: agent connectivity

The first piece of a new on-premises Active Directory connector, scoped deliberately: **this phase does not read or write anything in Active Directory yet.** It proves the one thing every later phase depends on — that a small on-prem agent can reach AccessPilot at all.

**Why an agent, when Entra/Okta don't need one:** both of those are cloud APIs AccessPilot calls directly. On-prem AD's LDAP port is normally only reachable from inside the customer's own network, so instead of AccessPilot reaching in, a small agent process runs inside that network and calls **out** to AccessPilot over plain HTTPS — no inbound firewall rule ever needed on the customer's side. Same pattern Entra Connect and Okta's own AD agent use.

**What's there today:**
- A new **"Active Directory"** card on the Providers page. Adding one just needs a name — no LDAP configuration exists yet, since there's nothing to connect to in this phase.
- **"Generate agent key"** — a one-time-shown credential (never stored or shown again, only its hash is kept) the agent authenticates with. Regenerating it immediately invalidates whatever key an already-running agent is using.
- **"Download agent script"** — a single, dependency-free Python file (standard library only, works out of the box on Windows Server or Linux) that heartbeats AccessPilot every 60 seconds once given the provider's URL, id, and key as environment variables. A `--once` flag sends a single heartbeat and exits, for a quick connectivity test.
- A live **"Agent connected"** / **"Agent not responding"** badge, based on whether a heartbeat has landed in the last few minutes — not a one-time confirmation, a continuous recency check.

**What's explicitly not built yet** (later phases): LDAP configuration (server address, base DN, bind credentials), reading real users/groups, group membership writes, user provisioning. Those all build on this same agent process once it's proven it can reach AccessPilot reliably.

## 45. Active Directory connector — dynamic LDAP configuration + Phase 2: real read-only sync

Two more pieces landed on top of Phase 1, against a real test Active Directory domain controller:

**Dynamic configuration.** The Active Directory provider card now has a real **"LDAP connection"** panel — LDAP URL, base DN, bind username/DN, and bind password, all entered and saved from the Providers page itself (no config files, no environment variables). The password is Fernet-encrypted the exact same way Entra's own Graph client secret already is. A **"Test connection"** button performs a real LDAPS bind with whatever is currently saved.

**Phase 2: real read-only sync.** The connector now actually reads your directory — real users (with department/title/enabled-or-disabled state) and real groups (with direct membership), flowing through the exact same sync pipeline Entra/Okta already use, with zero changes to that pipeline. A few honest, deliberate scope boundaries for this pass:
- **Direct group membership only** — not nested/transitive ("is this person in this group via another group"), which needs a different, more expensive query and is a clearly-flagged later phase.
- **No "roles" or "applications" yet** — plain on-premises Active Directory has no native equivalent to Entra's directory roles or enterprise applications. Rather than guess at a mapping (e.g. treating certain privileged groups as "roles"), this returns empty for now; a few well-known privileged groups (Domain Admins, Enterprise Admins, Schema Admins, Administrators) are still flagged on the Group itself.
- **No writes yet** — group membership changes, enabling/disabling a user, and provisioning a new account in Active Directory are all still ahead.

Verified for real against a live test domain: a real sync run correctly created real User and Group rows (including a disabled `krbtgt`/`Guest` correctly recognized as disabled, and `Administrators`' real members correctly resolved) — then fully cleaned up afterward.

## 46. Active Directory: a "Sync now" button, and a Source label on every User/Group

Two more pieces, closing the loop on Phase 2:

- **"Sync now" directly on the Active Directory provider card** — the existing Sync page only ever shows one provider (Entra, or the first one if Entra isn't configured), so a second directory had no way to trigger a sync. Rather than change that shared page, the Active Directory card now has its own "Sync now" button and a plain summary of the last run (users/groups/roles processed, any errors).
- **Every User and Group now shows which connector it actually came from.** The Groups list and detail page gained a new **Source** column/field (Groups never had this at all before); Users already showed this on the list and detail pages, unchanged. An Active Directory-sourced group gets a visually distinct badge, so it's easy to tell apart from an Entra- or Okta-synced one at a glance — useful once more than one directory is connected.

Verified for real: ran a real sync against the test domain, confirmed every synced user and group came back correctly labeled `ACTIVE_DIRECTORY`, then fully cleaned up.

## 47. Active Directory connector — Phase 3: writes, and every User-page button checked for real

Phase 3 makes Active Directory a two-way connector instead of read-only, and closes out a full audit of "does every button on the User page actually work for an AD-sourced person."

**What was built:**
- Editing a person's **Department**/**Job title**, enabling/disabling their account, setting their **manager**, and deleting a user all now perform a real write against the directory for AD, matching what Entra/Okta already do.
- Adding/removing someone from a group now really happens in AD — and critically, this is also what makes the **Activate / Deactivate / Revoke** buttons on Assignments and My Access work for AD-backed group access. That wiring was missing even after the underlying group-membership code was written; it's fixed now, verified against the real test domain.
- Every write was checked against real AD behavior first rather than guessed — for example, AD doesn't raise an error when you ask it to do something that's already true (add someone already in a group, disable an already-disabled account); that's treated as success, the same convention already used for Entra and Okta.
- Fixed a real bug where a directory write that failed (person not found, directory rejected the change) would have surfaced as a raw server error instead of a clean message — confirmed fixed by deliberately triggering that failure against the real test domain and watching it come back clean.

**What this means for the User page**: Department, Job title, and Enable/Disable now work for AD users exactly as they do for Entra/Okta users. Activate/Deactivate/Revoke (reached from Assignments/My Access) now work for AD-backed group access too. No existing attribute was missing — Department, Job title, status, email, name, and manager already covered everything the buttons needed.

**Still not built** (Phase 4, a separate future step): creating brand-new AD users or groups from AccessPilot. Nested/transitive group membership and multi-domain support also remain out of scope.

Verified: 3 new tests confirming the error-handling fix, 771 tests passing total, zero regressions. Live-verified against the real test domain — deliberately triggered a "not found" write and confirmed a clean error, and separately confirmed real AD group membership is granted and revoked correctly through the real Activate/Deactivate buttons' own code path. Backend restarted, no migration needed.

## 48. Cross-provider account mapping

With two real directories now connected (Entra and Active Directory), a real question came up: if someone's main account is in Entra but they get assigned a group that lives in AD, does it actually work? The honest answer, before this round, was **no** — every grant/revoke in the system quietly used the person's *main* account's ID no matter which directory the group actually belonged to. It happened to work until now only because every resource had always lived in the same directory as a person's main account.

**What changed:**
- A grant or revoke now looks up the person's own account in the **resource's own directory**, not their main one. If they don't have one there yet, AccessPilot creates it automatically, right then, using the same username rules Joiner already uses — no separate manual step needed.
- When someone is submitted through Joiner, AccessPilot now also looks at what their department's birthright policies would grant them and automatically creates accounts in **every** directory those policies touch — not just the directories someone manually checked a box for.
- A directory that can't create accounts yet (Active Directory provisioning is still a later step) fails that one target cleanly with a clear message, instead of crashing the whole submission.
- Groups already showed which directory they came from; Roles and Applications now show the same thing too, right in the picker when building an Access Package or Business Role — so it's clear at a glance whether an entitlement is an Entra one or an AD one.

Verified with 9 new tests (779 total passing, zero regressions) and live, twice, against the real tenant and the real test Active Directory server: a real cross-directory grant correctly used the right account and was fully revoked, and the auto-detection correctly identified a real Active Directory group's policy as needing a real AD account. Everything created for testing was cleaned up afterward. No migration needed; backend restarted.

## 49. Active Directory connector — Phase 4: creating real accounts and groups

The last piece of the original four-phase plan: AccessPilot can now actually create a new account or a new group in Active Directory, not just read from it or change existing ones.

- **Creating a person**: a brand-new AD account is created safely disabled first, given a real one-time password, and only switched on once that password is in place — the same sequence any careful AD administrator follows, done automatically.
- **Creating a group**: a real, proper security group, ready to have members added to it.
- A duplicate username or group name is caught cleanly with a clear message, the same way it already works for Entra and Okta.

This was the missing piece behind the account-mapping work from the previous round: before today, telling Joiner "this person needs an Active Directory account too" could correctly figure out that they needed one, but couldn't actually create it — it would fail with "doesn't support creating accounts yet." That gap is now closed.

Verified with 6 new tests (785 total passing, zero regressions) and live against the real test Active Directory server: created a real enabled account and confirmed its one-time password genuinely works to sign in, created a real disabled account, confirmed a duplicate is rejected, and created a real security group. Then, as the real end-to-end proof, submitted a real new-hire through Joiner with only Microsoft Entra picked by hand — and confirmed Active Directory was still detected automatically and a real, working AD account was created for them too, exactly as designed. Everything created during testing (including a real Entra account the test itself triggered, since Joiner genuinely provisions for real) was fully cleaned up and independently re-confirmed gone. No migration needed; backend restarted.

**The Active Directory connector is now complete across all four original phases**: connect, read, write, and create.

## 51. Leaver account deletion: a save bug fixed, and the log now shows what's waiting, deleted, or cancelled

A screenshot from the real Leaver Policy screen showed "The request contains invalid data" when trying to save a deletion schedule — a real bug, now fixed. The built-in Default policy carries an internal priority value the save screen was quietly re-sending even though its own priority field is hidden for that policy; it no longer does.

The Leaver log also now shows three things that used to be invisible:
- **Accounts deleted** — only the people whose accounts were actually removed from every directory, with when that was originally scheduled for.
- **Scheduled deletion cancelled** — someone who came back before their waiting period ended. Nothing was deleted, and now there's a record saying so, instead of it just quietly not happening.
- A new **"Scheduled account deletions"** list on the Leavers page shows everyone currently disabled and waiting it out, with each connected directory's own status — so if one directory is slow or temporarily unreachable, that shows as "waiting", not as a silent gap.

Verified with real accounts: a deletion that completes is logged correctly with the right timing; a person re-enabled mid-wait is logged as cancelled with their account genuinely untouched. Also confirmed, live, that the background process which performs these deletions runs entirely on its own — a real pending deletion on a real account finished automatically within a minute of the directory coming back online, with no one needing to click anything.

One thing to decide: the Default policy's real "delete after" setting is currently 30 days (set while confirming the fix), not the 90 chosen earlier — let me know which you'd like kept.

## 52. Editing someone's department or job title now updates every directory they're in

Found from a real screenshot of "Aman Sign," a person with both a Microsoft Entra ID and an Active Directory
account: changing their department on the User Detail page was only ever reaching Entra. The Active Directory
side silently kept whatever it had before — in this real case, it was still showing "Finance" days after the
department had actually been changed to "AppDev" in Entra.

Editing department or job title now pushes the same change to **every** directory the person has an account in,
not just the primary one. If a second directory happens to be unreachable at that moment, the main change still
goes through — it isn't held hostage by one directory being briefly unavailable — and that one gets recorded so
it's not silently missed.

Verified with 2 new tests and live on the real account from the report: pushed a real change through and confirmed
it landed in both Entra and Active Directory. As a direct result, Aman Sign's Active Directory record — which had
genuinely drifted out of sync before this fix — is now correctly back in step with Entra. 793 backend tests
passing, zero regressions, no migration. Backend restarted.

## 53. A global policy for when Joiner accounts actually get created

Until now, submitting a new hire through Joiner created their real account in every chosen directory **the
instant the form was submitted** — disabled until their start date, but the account itself already existed from
day one, no matter how far out that start date was.

There's now a single admin setting, on the Movers page: **"Create joiner accounts (days after submission)."**
Leave it blank and nothing changes. Set it to, say, 14, and a new hire submitted a month early won't get a real
account in Entra or Active Directory until 14 days after their submission — not the moment someone fills in the
form.

One deliberate guarantee: this setting can never get in its own way. If someone's start date is sooner than the
delay would allow — including a "start immediately" new hire — their account is still created right away, exactly
as it always has been. The delay only ever applies when there's genuine room for it.

A joiner waiting on this shows up in the Joiners list clearly labeled "Pending provisioning," and can still be
cancelled at that stage like any other joiner that hasn't started yet.

Verified with 5 new tests — including one that caught a real bug in the first version of this logic, where a
near-term joiner was incorrectly deferred instead of created immediately, fixed before anything shipped — plus a
full live run against the real tenant: a real account provably did not exist, then was provably created only once
the delay had elapsed. 798 backend tests passing, zero regressions. Migrated live, backend restarted.

## 50. Sync scheduling, more account fields at creation, and a full system check

Three more pieces closing out this arc, plus a full re-test of everything built so far.

- **Sync on a schedule**: the Active Directory provider card now has a "Sync automatically every (minutes)" field. Leave it blank and nothing changes — sync stays manual. Set a number and AccessPilot keeps that directory current on its own.
- **More fields when creating someone**: Joiner's "New joiner" form has a new, optional section — office, company, mobile phone, address, and (for Active Directory) a description. These apply once, at creation, to whichever directories the person gets an account in.
- **A full check of everything**: all four Active Directory phases were re-tested against the real tenant and the real test server, plus two new combined tests — one Access Package holding both an Entra group and an Active Directory group assigned to a single person, and one new-hire created with accounts in both directories from a single Joiner submission. Every check passed. One Microsoft-side delay (a group-membership check reading stale for a few seconds right after a real change) was investigated and confirmed to be Microsoft's own normal behavior, not an AccessPilot issue.

A full written report of this check — what was tested, how the account-mapping actually works, a self-test walkthrough, and the handful of known limits that remain — was delivered separately.
