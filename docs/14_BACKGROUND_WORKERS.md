# AccessPilot V1 — Background Workers

## Workers

V1 requires:

```text
Expiration Worker
Sync Worker
```

Reconciliation worker can be enabled after the core connector is stable.

## 1. Expiration Worker

Purpose:

Find active assignments whose expiration has passed.

Concept:

```text
Every minute
 -> query ACTIVE + expiration <= now
 -> lock row
 -> verify still active
 -> provider revoke/deactivate
 -> update EXPIRED
 -> audit
```

## Concurrency

Use database locking or equivalent concurrency control.

Two workers must not process the same assignment simultaneously.

## Provider failure

If provider operation fails:

```text
Do not falsely claim success.
Retry according to policy.
Create error/audit record.
```

## 2. Sync Worker

Purpose:

Synchronize provider state into PostgreSQL.

Flow:

```text
Start sync
 -> create sync_run
 -> fetch users
 -> fetch groups
 -> fetch memberships
 -> fetch roles
 -> fetch assignments
 -> upsert normalized state
 -> record errors
 -> complete sync
```

## Sync safety

Do not delete local state merely because a provider request failed.

Use explicit reconciliation logic.

## Retry

Retry transient errors:

```text
timeout
429
temporary 5xx
```

Do not blindly retry:

```text
401
403
invalid request
```

## 3. Reconciliation Worker

Later phase.

Detect:

```text
AccessPilot state != Provider state
```

Record:

```text
DRIFT_DETECTED
```

Do not automatically remediate all drift in V1.

## Observability

Every worker run must expose:

```text
start
finish
duration
records processed
success count
failure count
retry count
```
