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
