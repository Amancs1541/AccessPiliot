# AccessPilot — General-Purpose Notifications

**Status: implemented and live.** This document describes the general, org-wide notification system built 2026-09-03, distinct from the SoD-specific notification system documented in `docs/19_SOD_ENGINE.md` §16. Tested (12 new backend tests: `test_notifications.py` plus two in `test_sod.py`), 347 tests passing across the whole backend suite.

## 1. Why a separate system from SoD's notifications

`sod_notifications` (§16 of the SoD doc) already existed, but its design is specifically correct only for a small Admin/SoDAdmin team: read state is a **single global flag** shared by everyone who can see it — whoever marks a notification read marks it read for every other Admin/SoDAdmin too. That is the right tradeoff for a handful of people jointly governing SoD rules, and the wrong one the moment *every ordinary end user* needs their own personal notifications (their own request got approved, they were assigned something, someone needs their approval) — those must have their own, private unread state, not a shared one.

Rather than bend `sod_notifications` into something it isn't, this is a **new, separate table and model** (`Notification` / `notifications`), with its own API and its own row-per-recipient semantics. The two systems are independent: `sod_notifications` keeps working exactly as before (§16), and this document only covers the new one.

## 2. Data model

One new table (migration `0029_notifications`):

- **`notifications`** — `id`, `user_id` (the recipient — a real internal `User.id`, indexed), `notification_type`, `message`, `link` (optional frontend route), `read_at` (nullable), `created_at`.

This is deliberately simpler than `sod_notifications`: there is no `resolved_at` / reconciliation concept here. Every SoD notification represents a *currently-true condition* that can later stop being true (a violation gets fixed, an exception is revoked) and needs to be diffed against reality on every read. A general notification represents a **discrete event that already happened** — "your request was approved," "you were granted X" — which never becomes un-true after the fact. A row is created once, and only ever transitions unread → read. No reconciliation pass exists or is needed for this table.

## 3. What generates a notification

All wired into the shared assignment lifecycle functions in `backend/app/services/assignments.py`, so package items get this for free exactly like every other cross-cutting concern in this app (SoD checks, audit logging) — package assignments funnel through the same `create_assignment()`/`approve_assignment()`/etc. per item, so no package-specific code was needed.

| Event | Function | Recipient | Guard |
|---|---|---|---|
| Assignment created with an approver | `create_assignment()` | The approver, and the fallback approver if one is set | Always (approval-required branch) |
| Assignment created with no approver (ELIGIBLE or bypass-ACTIVE) | `create_assignment()` | The target user | Only if `requested_by != target user` — a self-service request doesn't notify you about your own action |
| Approved | `approve_assignment()` | The target user | Only if `actor != target user` |
| Rejected | `reject_assignment()` | The target user | Only if `actor != target user` |
| Activated | `activate_assignment()` | The target user | Only if `actor != target user` — self-activation is not notified, it would be pure noise |
| Deactivated | `deactivate_assignment()` | The target user | Only if `actor != target user` |
| Revoked | `revoke_assignment()` | The target user | Only if `actor != target user` (in practice always true — revoke is Admin-only) |
| SoD exception request granted | `grant_sod_exception_request()` (`services/sod.py`) | The admin who originally requested the exception (`requested_by`) | If `requested_by` is set |
| SoD exception request denied | `deny_sod_exception_request()` (`services/sod.py`) | Same | Same |

The last two are the direct answer to "if SoD admin approve it send notification to Admin" — before this, granting/denying an exception request only resolved the SoDAdmin-facing `sod_notifications` entry; the requesting Admin had no explicit "your request was decided" notification of their own.

**The `actor != target user` guard is the whole design principle here**: never notify someone about an action they themselves just took. An admin creating, approving, rejecting, or revoking *someone else's* assignment always notifies that person; a user activating or requesting something *for themselves* never notifies themselves.

## 4. API surface

All under `/api/v1/notifications`, gated only by being signed in (`require_authenticated_user`) — there is no permission check beyond that, since every row returned is already scoped to the caller's own internal user id, resolved server-side from their token:

| Endpoint | Purpose |
|---|---|
| `GET /notifications` | The caller's own notifications, newest first, capped at 100 |
| `POST /notifications/{id}/read` | Mark one of the caller's own notifications read (404 if it belongs to someone else) |
| `POST /notifications/read-all` | Mark every one of the caller's own unread notifications read |

## 5. Frontend — one shared Bell, one shared dropdown

The topbar Bell (`Shell` in `src/App.tsx`) now shows for **every signed-in user**, not just Admin/SoDAdmin as before. Clicking it opens a dropdown (`.notif-dropdown` in `src/styles.css`) that merges two sources into one chronological list, capped at the 10 most recent:

- The caller's own general notifications (`GET /notifications`) — visible to everyone.
- The shared SoD notification feed (`GET /sod/notifications`) — only merged in for Admin/SoDAdmin, exactly as before.

Each row shows the message, a relative time, a per-row "Mark read" (routed to the correct backend endpoint depending on which source it came from), and — when the notification carries a `link` — a "View" link that navigates there and closes the dropdown. "Mark all as read" in the footer marks both sources at once for Admin/SoDAdmin, or just the personal feed for a plain end user. An "Open SoD Configuration" link only appears in the footer for Admin/SoDAdmin, for the full settings + complete SoD log.

**Polling cadence, and why they differ**: the personal feed polls every **10 seconds** — shortened from an initial 30s after the user reported the Bell felt more like "click to check" than "pushed" (there is no WebSocket/SSE infrastructure in this app, so this interval, not true push, is what "live" means here; a genuinely instant push would need a new SSE-based delivery mechanism, considered and deliberately deferred as a separate, bigger piece of work). It's a cheap, plain per-user database read, so 10s is safe. The SoD feed stays at 60 seconds — unlike the personal feed, every call to `GET /sod/notifications` re-runs the SoD reconciliation pass, a real ~15-second Graph-read cost when ROLE/APPLICATION rules exist (see `docs/19_SOD_ENGINE.md` §11/§16); polling that anywhere near as fast as the personal feed would leave the scan running almost continuously in the background of every Admin/SoDAdmin session.

## 6. What's deliberately not built

- **No outbound delivery** (email/Teams/webhook) — same reasoning as `sod_notifications`: this app has no email/SMTP infrastructure at all, so real delivery would be new infrastructure, not an extension of an existing pattern.
- **No notification for self-caused events** — by design (§3's guard), not a gap.
- **No batching/digest** — a package assignment with N items produces N separate approval-request notifications to the approver, one per item, matching how every other cross-cutting list in this app (Assignments table, Audit Logs) already treats package items individually rather than as one collapsed row.
- **No push/real-time delivery** — same interval-polling technique as everywhere else "live" in this app (Dashboard, SoD Bell); no WebSocket/SSE infrastructure exists.
