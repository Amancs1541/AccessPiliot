# AccessPilot V1 — Microsoft Graph Mapping

## Purpose

Map every AccessPilot provider feature to a Microsoft Graph operation and required permission.

The coder must verify the current Microsoft Graph documentation before implementing each operation.

## Read users

AccessPilot:

```text
GET /api/v1/users
GET /api/v1/users/{id}
```

Provider layer:

```text
Microsoft Graph users APIs
```

Initial permission:

```text
User.Read.All
```

## Read groups

AccessPilot:

```text
GET /api/v1/groups
GET /api/v1/groups/{id}
```

Provider layer:

```text
Microsoft Graph groups APIs
```

Permission:

```text
Group.Read.All
```

## Read group membership

AccessPilot:

```text
GET /api/v1/groups/{id}/members
GET /api/v1/users/{id}/groups
```

Permission:

```text
Group.Read.All
```

## Modify group membership

AccessPilot:

```text
POST /api/v1/groups/{id}/members
DELETE /api/v1/groups/{id}/members/{userId}
```

Provider:

```text
Microsoft Graph group membership APIs
```

Permission:

```text
Group.ReadWrite.All
```

Only enable in Phase 9.

## Read directory roles

AccessPilot:

```text
GET /api/v1/roles
GET /api/v1/roles/{id}
```

Provider:

```text
Microsoft Graph role management APIs
```

Permission:

```text
RoleManagement.Read.Directory
```

## Role assignments / PIM

AccessPilot:

```text
POST /api/v1/assignments/{id}/activate
POST /api/v1/assignments/{id}/revoke
POST /api/v1/assignments/{id}/extend
```

Provider:

```text
Microsoft Graph role management / PIM APIs
```

Potential application permission:

```text
RoleManagement.ReadWrite.Directory
```

Exact Graph endpoint, permission and licensing requirements must be verified for the target role-management operation before implementation.

## Provider abstraction rule

The service layer must not contain Graph URLs.

Bad:

```python
graph_client.post("/directory/...")
```

inside business logic.

Good:

```python
await provider.activate_assignment(...)
```

## Graph error mapping

Provider errors must be converted to AccessPilot errors:

```text
401/invalid credential -> PROVIDER_AUTHENTICATION_FAILED
403 -> PROVIDER_PERMISSION_DENIED
404 -> PROVIDER_RESOURCE_NOT_FOUND
409 -> PROVIDER_CONFLICT
429 -> GRAPH_THROTTLED
5xx -> PROVIDER_UNAVAILABLE
timeout -> PROVIDER_TIMEOUT
```

Do not expose raw Graph error bodies to users.
