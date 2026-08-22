# AccessPilot V1 — Final App Roles

## Final V1 roles

Only:

```text
AccessPilot.User
AccessPilot.Admin
```

## AccessPilot.User

Purpose:

Normal self-service user.

Permissions:

```text
ME_READ
DASHBOARD_USER_READ
ACCESS_REQUEST_CREATE
ACCESS_REQUEST_READ_SELF
ACCESS_REQUEST_CANCEL_SELF
ASSIGNMENT_READ_SELF
ASSIGNMENT_ACTIVATE_SELF
ASSIGNMENT_REVOKE_SELF
```

Cannot:

```text
Manage users
Manage groups
Manage roles
Manage providers
Manage policies
Approve requests
View all audit
Create assignments for others
```

## AccessPilot.Admin

Purpose:

Full AccessPilot administrative role.

Permissions:

```text
ME_READ
DASHBOARD_ADMIN_READ
USER_READ
GROUP_READ
GROUP_MANAGE
ROLE_READ
ROLE_MANAGE
PROVIDER_READ
PROVIDER_MANAGE
PROVIDER_SYNC
ACCESS_REQUEST_READ
ACCESS_REQUEST_APPROVE
ACCESS_REQUEST_REJECT
ACCESS_REQUEST_CANCEL
ASSIGNMENT_READ
ASSIGNMENT_CREATE
ASSIGNMENT_REVOKE
ASSIGNMENT_EXTEND
POLICY_READ
POLICY_CREATE
POLICY_UPDATE
POLICY_DELETE
AUDIT_READ
SYNC_READ
```

## Important distinction

```text
AccessPilot.Admin
```

does not mean:

```text
Global Administrator
Privileged Role Administrator
```

Microsoft Entra directory roles are separate from AccessPilot app roles.

## Assignment recommendation

Prefer assigning app roles to Entra groups:

```text
AccessPilot-Admins -> AccessPilot.Admin
AccessPilot-Users  -> AccessPilot.User
```

## V2 candidates

Do not implement in V1:

```text
AccessPilot.Approver
AccessPilot.Auditor
AccessPilot.ReadOnlyAdmin
AccessPilot.ProviderAdmin
```
