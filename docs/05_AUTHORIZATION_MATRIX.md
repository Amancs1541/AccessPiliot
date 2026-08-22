# AccessPilot V1 — Authorization Matrix

## Role legend

```text
U = AccessPilot.User
A = AccessPilot.Admin
```

| Operation | U | A |
|---|---:|---:|
| Read own profile | Yes | Yes |
| Read own access | Yes | Yes |
| Create own request | Yes | Yes |
| Cancel own request | Yes | Yes |
| Activate own eligible assignment | Yes | Yes |
| Revoke own access | Policy | Yes |
| Read all users | No | Yes |
| Read all groups | No | Yes |
| Manage group membership | No | Yes |
| Read roles | No | Yes |
| Manage provider | No | Yes |
| Run sync | No | Yes |
| Read all requests | No | Yes |
| Approve request | No | Yes |
| Reject request | No | Yes |
| Create assignment for another user | No | Yes |
| Revoke another user's assignment | No | Yes |
| Manage policies | No | Yes |
| Read audit | No | Yes |

## Endpoint policy

Authorization must happen server-side.

A request is allowed only when:

```text
authenticated
AND app permission
AND resource ownership/scope
AND policy
AND state transition
```

## Normal user object-level rule

A user may only access:

```text
their own profile
their own requests
their own assignments
their own audit subset
```

unless a specific future permission says otherwise.

## Privileged operation rule

For:

```text
approve
activate
revoke
extend
group mutation
role mutation
policy mutation
provider mutation
```

the backend must:

1. authenticate
2. authorize
3. validate state
4. evaluate policy
5. perform provider operation
6. verify result
7. persist
8. audit

## IDOR protection

Never trust:

```text
userId
requesterId
actorId
```

from the frontend to determine identity.

The backend derives the current actor from the validated token.
