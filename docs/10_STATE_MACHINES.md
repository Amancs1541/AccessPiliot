# AccessPilot V1 — State Machines

## 1. Access Request

```text
CREATED
  |
  v
PENDING
  | \
  |  \
  v   v
APPROVED  REJECTED
  |
  v
ELIGIBLE / READY
  |
  v
ACTIVE
  |
  +--> EXPIRED
  |
  +--> REVOKED
```

Normal request flow:

```text
POST /access-requests
```

Approval:

```text
POST /access-requests/{id}/approve
```

Reject:

```text
POST /access-requests/{id}/reject
```

Cancel is only allowed while cancellation is valid.

## 2. Assignment

```text
PENDING
   |
   v
ELIGIBLE
   |
   v
ACTIVE
  / \
 v   v
EXPIRED REVOKED
```

## 3. Valid transitions

### PENDING

Allowed:

```text
approve
reject
cancel
```

### ELIGIBLE

Allowed:

```text
activate
cancel where policy allows
```

### ACTIVE

Allowed:

```text
revoke
extend
```

### EXPIRED

Terminal for that assignment instance.

### REVOKED

Terminal for that assignment instance.

## 4. Rules

Never allow:

```text
EXPIRED -> ACTIVE
REVOKED -> ACTIVE
REJECTED -> ACTIVE
```

without creating a new authorized request/assignment flow.

## 5. Provider failure

If provider operation fails:

```text
Do not transition to ACTIVE.
```

If provider state is unknown:

```text
mark operation as uncertain
do not claim success
create audit/error record
```

## 6. Expiration

Backend expiration timestamp is authoritative.

Frontend countdown is informational.

Worker must transition:

```text
ACTIVE -> EXPIRED
```

after successful provider deactivation where provider-side deactivation is required.

## 7. Approval

Approval must:

- validate approver
- validate request state
- re-evaluate policy
- verify requested duration
- execute provider action where appropriate
- persist result
- audit

Approval is never enough by itself to mark provider access active if the provider operation fails.
