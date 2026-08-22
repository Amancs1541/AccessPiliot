# AccessPilot V1 — API Contract

Base:

```text
/api/v1
```

## Current user

```http
GET /me
GET /me/access
GET /me/requests
GET /me/audit
```

## Dashboard

```http
GET /dashboard/admin
GET /dashboard/user
```

## Users

```http
GET /users
GET /users/{userId}
GET /users/{userId}/groups
GET /users/{userId}/roles
GET /users/{userId}/assignments
GET /users/{userId}/requests
GET /users/{userId}/audit
```

## Groups

```http
GET /groups
GET /groups/{groupId}
GET /groups/{groupId}/members
GET /groups/{groupId}/assignments
GET /groups/{groupId}/policies
```

Write operations for group membership:

```http
POST /groups/{groupId}/members
DELETE /groups/{groupId}/members/{userId}
```

## Roles

```http
GET /roles
GET /roles/{roleId}
GET /roles/{roleId}/assignments
GET /roles/{roleId}/policies
```

## Providers

```http
GET /providers
POST /providers
GET /providers/{providerId}
PATCH /providers/{providerId}
DELETE /providers/{providerId}
POST /providers/{providerId}/sync
GET /providers/{providerId}/sync-runs
GET /providers/{providerId}/health
```

## Access evaluation

```http
POST /access/evaluate
```

Request:

```json
{
  "providerId": "uuid",
  "resourceType": "ROLE",
  "resourceId": "uuid",
  "requestedDurationMinutes": 120
}
```

## Access requests

```http
GET /access-requests
POST /access-requests
GET /access-requests/{requestId}
POST /access-requests/{requestId}/approve
POST /access-requests/{requestId}/reject
POST /access-requests/{requestId}/cancel
```

## Assignments

```http
GET /assignments
POST /assignments
GET /assignments/{assignmentId}
POST /assignments/{assignmentId}/activate
POST /assignments/{assignmentId}/revoke
POST /assignments/{assignmentId}/extend
GET /assignments/{assignmentId}/history
```

## Policies

```http
GET /policies
POST /policies
GET /policies/{policyId}
PATCH /policies/{policyId}
DELETE /policies/{policyId}
```

## Audit

```http
GET /audit-logs
GET /audit-logs/{auditId}
```

## Sync

```http
GET /sync-runs
GET /sync-runs/{syncId}
```

## Health

```http
GET /health
GET /api/v1/health
GET /api/v1/health/providers
```

## Standard success

```json
{
  "data": {},
  "meta": {}
}
```

## Standard error

```json
{
  "error": {
    "code": "ACCESS_DENIED",
    "message": "You do not have permission to perform this action.",
    "requestId": "uuid"
  }
}
```

## Rules

- Frontend never calls Graph directly.
- Backend re-evaluates policy for privileged mutations.
- Backend derives actor from token.
- User IDs supplied by clients never override token identity.
- Use pagination consistently.
- Use UTC.
- Use idempotency for suitable mutation operations.
