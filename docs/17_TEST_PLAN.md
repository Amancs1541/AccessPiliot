# AccessPilot V1 — Test Plan

## 1. Unit tests

Test:

```text
permission mapping
policy engine
duration validation
state transitions
expiration calculation
request validation
error mapping
provider abstraction
```

## 2. Authentication tests

```text
valid token -> 200
missing token -> 401
expired token -> 401
wrong audience -> 401
wrong issuer -> 401
wrong tenant -> 401
```

## 3. Authorization tests

```text
User -> admin endpoint -> 403
User -> another user's request -> 403
User -> provider management -> 403
User -> policy management -> 403
Admin -> authorized admin endpoint -> success
```

## 4. JIT tests

```text
eligible -> activation allowed
non-eligible -> denied
expired -> denied
revoked -> denied
approval required -> cannot activate before approval
duration too high -> denied
missing justification -> denied
missing ticket -> denied
```

## 5. Provider tests

Mock provider:

```text
get users
get groups
add member
remove member
get roles
activate
revoke
timeout
429
403
404
500
```

## 6. Database tests

Test:

- foreign keys
- unique constraints
- transactions
- concurrent updates
- indexes
- migration up/down where appropriate

## 7. API tests

For every endpoint:

```text
success
validation failure
unauthorized
forbidden
not found
provider failure
```

## 8. Frontend tests

Test:

```text
login
route protection
Admin navigation
User navigation
loading
empty state
error state
request form
approval
activation
countdown
revoke
```

## 9. End-to-end scenarios

### Scenario A

```text
User login
 -> see dashboard
 -> request access
 -> pending
```

### Scenario B

```text
Admin login
 -> see request
 -> approve
 -> assignment becomes eligible/active according to design
```

### Scenario C

```text
User activates
 -> provider operation
 -> active
 -> expiration
 -> expired
```

### Scenario D

```text
User attempts admin API
 -> 403
```

## 10. Security testing

Must include:

```text
IDOR
privilege escalation
token validation
replay
race condition
excessive duration
approval bypass
secret leakage
SQL injection
XSS
```

## 11. Release gate

All critical/high security tests must pass.

No known critical authorization vulnerability may remain open.
