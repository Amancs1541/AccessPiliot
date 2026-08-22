# AccessPilot V1 — Product Requirements

## 1. Product vision

AccessPilot is a centralized identity governance console that gives administrators visibility and controlled management over identity-provider access while giving normal users safe self-service JIT access.

## 2. Primary personas

### Normal User

Needs temporary access without becoming an administrator.

Can:

- Sign in
- View own identity
- View own active access
- View eligible access
- Request access
- Provide justification
- Provide ticket number when required
- Activate eligible access
- Revoke own access where policy allows
- View own request history

Cannot:

- Manage other users
- Manage providers
- Change policies
- Approve requests
- Modify arbitrary group membership
- Manage other users' privileged access

### Admin

Can:

- View users
- View groups
- View roles
- Manage supported group membership
- Review access requests
- Approve/reject requests
- Manage assignments
- Configure policies
- Manage provider configuration
- Run synchronization
- View audit logs

## 3. V1 user journey

```text
Login
 -> Dashboard
 -> My Access
 -> Select eligible access
 -> Evaluate
 -> Request
 -> Approval if required
 -> Activate
 -> Active until expiration
 -> Automatic expiration
```

## 4. V1 admin journey

```text
Login
 -> Admin Dashboard
 -> Users / Groups / Roles
 -> Access Requests
 -> Approve or Reject
 -> Assignments
 -> Policies
 -> Audit
 -> Provider / Sync
```

## 5. Core entities

```text
Provider
User
Group
Role
Group Membership
Role Assignment
Access Request
Approval
Access Assignment
Policy
Audit Event
Sync Run
```

## 6. V1 scope

### In scope

- Microsoft Entra ID
- Microsoft Graph
- PostgreSQL
- React
- FastAPI
- MSAL
- JIT access
- Approval
- Time-bound assignments
- Audit
- Sync
- Policy rules

### Out of scope

- Okta implementation
- Google Workspace
- SailPoint
- PAM
- Mobile application
- AI assistant
- ML risk scoring
- Full access reviews
- Full entitlement catalog

## 7. Product principles

1. Least privilege
2. Backend is security authority
3. Provider operations are audited
4. Time-bound access expires automatically
5. Provider-specific logic stays behind a connector
6. Every privileged mutation must be traceable
7. No secret is exposed to the browser
