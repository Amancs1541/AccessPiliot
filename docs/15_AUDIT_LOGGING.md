# AccessPilot V1 — Audit Logging

## Purpose

Every security-sensitive action must be traceable.

## Events

Authentication:

```text
LOGIN_SUCCESS
LOGIN_FAILURE
```

Requests:

```text
ACCESS_REQUEST_CREATED
ACCESS_REQUEST_APPROVED
ACCESS_REQUEST_REJECTED
ACCESS_REQUEST_CANCELLED
```

Assignments:

```text
ASSIGNMENT_CREATED
ASSIGNMENT_ACTIVATED
ASSIGNMENT_REVOKED
ASSIGNMENT_EXTENDED
ASSIGNMENT_EXPIRED
```

Provider mutations:

```text
GROUP_MEMBER_ADDED
GROUP_MEMBER_REMOVED
ROLE_ASSIGNED
ROLE_REMOVED
```

Administration:

```text
POLICY_CREATED
POLICY_UPDATED
POLICY_DELETED
PROVIDER_CREATED
PROVIDER_UPDATED
PROVIDER_DELETED
```

Operations:

```text
SYNC_STARTED
SYNC_COMPLETED
SYNC_FAILED
DRIFT_DETECTED
```

## Event fields

```text
id
timestamp
actor
action
target
provider
requestId
result
IP if appropriate
user agent if appropriate
metadata
```

## Security

Never log:

```text
access tokens
refresh tokens
client secrets
private keys
passwords
authorization headers
```

## Audit immutability

Normal application APIs must not update or delete historical audit records.

## Correlation

Every API request should have a request/correlation ID.

The same ID should be traceable across:

```text
API
service
provider
audit
worker
```

## Result

Use:

```text
SUCCESS
FAILURE
DENIED
```

## Audit timing

Create the audit event after the operation result is known.

For failed attempts, audit the failure too.
