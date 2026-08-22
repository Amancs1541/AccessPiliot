# AccessPilot V1 — Microsoft Entra Setup

## 1. App registrations

Create:

```text
AccessPilot-Frontend
AccessPilot-API
```

## 2. Frontend

Platform:

```text
Single-page application
```

Configure:

```text
Development Redirect URI
Production Redirect URI
Logout Redirect URI
```

Record:

```text
Tenant ID
Client ID
Object ID
Redirect URIs
```

## 3. Backend API

Configure:

```text
Expose an API
Application ID URI:
api://<API_CLIENT_ID>
```

Expose:

```text
access_as_user
```

## 4. App roles

Create:

```text
AccessPilot.User
AccessPilot.Admin
```

See `04_FINAL_APP_ROLES.md`.

## 5. Frontend API permission

Frontend requests:

```text
access_as_user
```

from AccessPilot API.

## 6. Graph application permissions

Initial read-only:

```text
User.Read.All
Group.Read.All
RoleManagement.Read.Directory
```

Add only when required:

```text
Group.ReadWrite.All
RoleManagement.ReadWrite.Directory
```

Do not use broad directory write permissions as a shortcut.

## 7. Test groups

Create:

```text
AccessPilot-Admins
AccessPilot-Users
AP-Test-Users
AP-Test-Production
```

Record each object ID.

## 8. Test users

Record:

```text
Admin UPN
Admin Object ID
User UPN
User Object ID
```

## 9. Information to collect

```text
Tenant ID
Tenant primary domain
Frontend Client ID
Frontend Object ID
API Client ID
API Object ID
Application ID URI
API scope
App Role IDs
Test group IDs
Test user IDs
Redirect URIs
Graph permissions
Admin consent status
```

## 10. Secrets

Never put in this file:

```text
client secret
private key
access token
refresh token
password
```

## 11. Validation

Before Phase 6:

```text
[ ] Login works
[ ] access_as_user works
[ ] User.Read.All consented
[ ] Group.Read.All consented
[ ] RoleManagement.Read.Directory consented
[ ] Backend can obtain Graph token
[ ] Graph user read works
[ ] Graph group read works
[ ] Graph role read works
```
