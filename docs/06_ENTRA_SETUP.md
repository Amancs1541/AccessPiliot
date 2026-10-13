# AccessPilot — Microsoft Entra Setup Guide

This covers the **portal sign-in** integration — how a real person signs into AccessPilot with their Microsoft
account and ends up with the right role (`AccessPilot.Admin` / `AccessPilot.User`). This is a *separate* thing
from the **directory connector** (Admin → Providers), which syncs users/groups/roles from your tenant for
governance purposes — the two can use the same app registration or entirely different ones; nothing here depends
on how that one is set up.

Every step below says *why*, because almost every real failure in this flow is silent — Entra doesn't error out,
it just quietly issues a token that doesn't have what AccessPilot needs, and the only symptom is a 401 that looks
identical to a dozen unrelated problems. The "Troubleshooting" section at the end is a direct map from symptom to
cause, built from a real debugging session.

## The one decision that shapes everything else: one app, or two?

Every sign-in needs a **client app** (the thing a user "signs into") and a **resource app** (the thing the
access token is actually *for* — whose App Roles end up in the token). Most of the time these are **the same
app registration**, and the whole setup below collapses: one app, with a redirect URI, an exposed API scope,
and App Roles, all on itself.

Sometimes they end up being **different** app registrations — for example, a connector/API app was already set
up for the directory sync integration, and a separate, newer app registration got created for actual user
sign-in. AccessPilot supports this (it's exactly what the advanced **Audience** field below is for), but it adds
two extra steps (an API permission + consent on the client app, and remembering which manifest's
`accessTokenAcceptedVersion` actually matters) that are easy to miss. If you're setting this up for the first
time, **use one app for both roles** unless you have a specific reason not to — it's simpler and there's nothing
to get out of sync later.

The steps below are written for the general (two-app-capable) case; skip anything that says "only if using a
separate resource app" if you're keeping it to one app.

## 1. Create the client (sign-in) app registration

In the Entra admin center → **App registrations** → **New registration**.

- **Supported account types**: single tenant (your org only), unless you specifically need multi-tenant.
- **Redirect URI**: add it now or later, but it must be registered as a **Single-page application (SPA)**
  platform — not "Web". AccessPilot's frontend is a browser SPA doing the OAuth redirect/popup flow itself; if
  the redirect URI is registered under "Web" instead, sign-in fails with
  `AADSTS900023: Cross-origin token redemption is permitted only for the 'Single-Page Application' client-type`
  — a confusing error that gives no hint the platform type is the problem. The URI itself must match exactly
  what your browser shows, e.g. `https://192.168.0.101` or `https://accesspilot.example.com` — no trailing
  slash, no path.

Record this app's **Application (client) ID** and your tenant's **Directory (tenant) ID** — you'll need both
when configuring AccessPilot in step 8.

## 2. Expose an API (on the resource app)

If using one app for everything, this is the same app from step 1. If using a separate resource app, create or
open *that* one instead.

**Expose an API** → if no Application ID URI is set, click **Add** and accept the default
(`api://<this-app's-client-id>`) → **Add a scope**:
- Scope name: `access_as_user`
- Who can consent: **Admins and users**
- Admin consent display name/description: anything descriptive (e.g. "Access AccessPilot API")
- State: **Enabled**

This scope is what AccessPilot's frontend actually requests during sign-in — it's what makes Entra issue an
access token *for your app* (carrying your App Roles) instead of a generic token for some default resource.

## 3. App roles (on the resource app)

Still on the resource app (same-app or separate, as in step 2) → **App roles** → **Create app role** for each of:
```
AccessPilot.User
AccessPilot.Admin
```
(see `docs/04_FINAL_APP_ROLES.md` for exactly what each grants). **Allowed member types**: Users/Groups.

## 4. If using a separate resource app: authorize the client to request its scope

Only needed when the client app (step 1) and resource app (steps 2-3) are different registrations. Skip this if
they're the same app.

On the **client** app → **API permissions** → **Add a permission** → **My APIs** → select the resource app →
**Delegated permissions** → check `access_as_user` → **Add permissions**. Then click **Grant admin consent for
[your tenant]** and confirm it shows a green check — "Not granted" silently breaks sign-in later (see
Troubleshooting).

**Why this step is invisible when it's missing**: if this isn't done, Entra doesn't refuse to sign the user in —
it just silently substitutes a default Microsoft Graph token (audience
`00000003-0000-0000-c000-000000000000`, scopes like `openid profile email` or `User.Read`) instead of the one
you asked for. The sign-in *looks* successful. Every subsequent API call then 401s with no useful error, because
that Graph token has no `roles` claim at all.

## 5. Assign real people to the App Roles

**Entra ID → Enterprise applications** → find the resource app (search by its name, not the client app's name if
they differ) → **Users and groups** → **Add user/group** → assign each person (or, better, a group) to
`AccessPilot.Admin` or `AccessPilot.User`.

**This is the step that actually produces the `roles` claim in the token.** Granting the API permission in step
4 only lets the client *ask* for a token for this resource — it says nothing about what roles that person has.
Skipping this step produces a token with the right audience and scope, but an empty `roles` claim, and
AccessPilot rejects it the same way (401, no valid AccessPilot role) — same symptom as step 4's mistake, totally
different cause.

A role change here can take anywhere from a few seconds up to a couple of minutes to actually show up in a newly
issued token, even forcing a refresh — this is normal Entra propagation delay, not a bug. If "Refresh my access"
(on AccessPilot's own Profile page) doesn't pick it up right away, wait a minute and retry, or sign out and back
in for a guaranteed fresh token.

## 6. Token version — v1 vs v2 (only matters if you're hitting issuer errors)

Skip this unless something downstream complains about the issuer. AccessPilot's backend assumes your access
token is in **v2.0 format**, whose issuer looks like:
```
https://login.microsoftonline.com/<tenant-id>/v2.0
```
Some app registrations (depending on history/configuration) instead issue **v1.0-format** tokens, whose issuer
looks like:
```
https://sts.windows.net/<tenant-id>/
```
These don't match, and the mismatch is **governed by the resource app's manifest, not the client app's** — it's
easy to edit the wrong one and see no change (ask how we know).

To force v2.0 (recommended — matches AccessPilot's default assumption, no extra config needed): open the
**resource** app's **Manifest**, set `"accessTokenAcceptedVersion": 2`, save. Sign out and back in to get a
freshly issued token in the new format.

If you'd rather leave it as v1.0, AccessPilot can be told to expect that instead — see the **Issuer** field in
step 8.

## 7. Verify with a real token before going further

You don't have to guess whether all of the above actually worked. Sign in, open your browser's DevTools →
Network tab, find any request to AccessPilot's own API (e.g. `/api/v1/notifications`), and look at its
`Authorization: Bearer <token>` header. Paste the token (just the long dot-separated string) into
[jwt.io](https://jwt.io) or decode it yourself — the payload should show:
```json
{
  "aud": "<your resource app's client ID, or its api://... URI>",
  "iss": "https://login.microsoftonline.com/<tenant-id>/v2.0",
  "roles": ["AccessPilot.Admin"],   // or AccessPilot.User
  "scp": "access_as_user"
}
```
If any of these look wrong, the Troubleshooting table below tells you exactly which step to revisit.

## 8. Configure AccessPilot itself

This is done once during the **first-time setup wizard**, and can be corrected later from the **Break-Glass
Dashboard** (reachable via the hidden emergency-access URL — see `backend/app/cli.py`'s `emergency-url` command)
if sign-in ever breaks. Both forms have the same fields:

| Field | Value | Notes |
|---|---|---|
| Tenant ID | your Directory (tenant) ID | |
| Client ID | the **client/sign-in** app's Application ID (step 1) | not the resource app's, if they differ |
| Authority | `https://login.microsoftonline.com/<tenant-id>` | auto-filled from Tenant ID |
| Scope *(optional, advanced)* | `api://<resource-app-client-id>/access_as_user` | leave blank only if you want a basic sign-in-only token with no API access at all — fine for `AccessPilot.BreakGlassAdmin`-style recovery, not for real use |
| Audience *(optional, advanced)* | the **resource** app's client ID | **only set this when the resource app is a different registration than Client ID above.** Leave blank if they're the same app — it defaults to Client ID automatically. |
| Issuer *(optional, advanced)* | `https://sts.windows.net/<tenant-id>/` | **only set this if you chose to keep v1.0 tokens in step 6.** Leave blank for the standard v2.0 issuer (derived automatically from Authority). |

Save, then do a real sign-in and confirm against step 7's token decode.

## 9. Break-Glass: your safety net

A Break-Glass account is a local username/password login created during setup, dormant until it's needed. If a
mistake anywhere above locks out real sign-in, the emergency URL (never linked anywhere in the app, generated
only via `python -m app.cli emergency-url` — see that file's own docstring) gets you into a minimal recovery
dashboard that can fix the IDP configuration above or rotate the Break-Glass password — nothing else in
AccessPilot is reachable from that session. Keep that URL somewhere safe outside the app itself.

## Reference: information worth recording as you go

```text
Tenant ID
Tenant primary domain
Client (sign-in) app: Client ID, Object ID, redirect URI(s)
Resource (API) app: Client ID, Object ID, Application ID URI, exposed scope name
App Role IDs (AccessPilot.User / AccessPilot.Admin)
Test group/user IDs
Graph permissions + admin consent status (directory connector, separate from all of the above)
```
Never record: client secrets, private keys, access/refresh tokens, passwords.

## Troubleshooting: symptom → real cause

| Symptom | What's actually wrong |
|---|---|
| `AADSTS900023: Cross-origin token redemption is permitted only for the 'Single-Page Application' client-type` | The redirect URI is registered under **Web**, not **Single-page application**, on the client app (step 1). |
| Sign-in *looks* successful, but every API call 401s; decoded token shows `"aud": "00000003-0000-0000-c000-000000000000"` and no `roles` claim | That's Microsoft Graph's fixed app ID, not yours — Entra silently substituted a default token. Either the **Scope** field (step 8) isn't actually saved, or the client app was never granted + consented the resource app's `access_as_user` permission (step 4). |
| Decoded token has the right `aud` and `scp`, but `roles` is empty or missing | The signed-in user was never assigned to an App Role on the resource app (step 5) — API permission consent alone doesn't do this. |
| `INVALID_ISSUER` / "Token issuer is not configured" | Can be misleading: if you have an active dynamic IDP config (the setup-wizard flow), this now means *that config's own* issuer check failed — almost always a v1.0-vs-v2.0 mismatch (step 6), not a missing config. Decode the token and compare its real `iss` against what AccessPilot expects. |
| A role you just assigned doesn't show up immediately, even after "Refresh my access" | Normal Entra propagation delay (seconds to a couple of minutes). Wait and retry, or sign out/in fully for a guaranteed fresh token. |
| A person's "My Access" page shows nothing, even though an admin can see an eligible assignment for them | Compare the token's `oid` claim against that person's `external_id` in the `users` table (`docker/db-query.sh "SELECT external_id FROM users WHERE email = '...';"`) — if they don't match, the account was created with a placeholder/different identity than the real Entra account now signing in. |
| Edited `accessTokenAcceptedVersion` but the token format didn't change | You edited the **client** app's manifest. This setting is read from whichever app's URI is in the token's `aud` — the **resource** app, if they differ. |
