# AccessPilot V1 — Error Contract

## Standard response

```json
{
  "error": {
    "code": "ACCESS_DENIED",
    "message": "You do not have permission to perform this action.",
    "requestId": "uuid",
    "details": {}
  }
}
```

## Authentication errors

```text
AUTHENTICATION_REQUIRED
INVALID_TOKEN
TOKEN_EXPIRED
INVALID_AUDIENCE
INVALID_ISSUER
INVALID_TENANT
```

HTTP:

```text
401
```

## Authorization

```text
ACCESS_DENIED
RESOURCE_ACCESS_DENIED
```

HTTP:

```text
403
```

## Validation

```text
VALIDATION_ERROR
INVALID_DURATION
INVALID_STATE
MISSING_JUSTIFICATION
MISSING_TICKET
```

HTTP:

```text
400 / 422
```

## Resource

```text
RESOURCE_NOT_FOUND
USER_NOT_FOUND
GROUP_NOT_FOUND
ROLE_NOT_FOUND
REQUEST_NOT_FOUND
ASSIGNMENT_NOT_FOUND
PROVIDER_NOT_FOUND
```

HTTP:

```text
404
```

## Workflow

```text
APPROVAL_REQUIRED
REQUEST_ALREADY_PROCESSED
ASSIGNMENT_NOT_ELIGIBLE
ASSIGNMENT_ALREADY_ACTIVE
ASSIGNMENT_EXPIRED
ASSIGNMENT_REVOKED
POLICY_DENIED
```

HTTP:

```text
409 or 422
```

## Provider

```text
PROVIDER_AUTHENTICATION_FAILED
PROVIDER_PERMISSION_DENIED
PROVIDER_RESOURCE_NOT_FOUND
PROVIDER_CONFLICT
PROVIDER_UNAVAILABLE
PROVIDER_TIMEOUT
GRAPH_THROTTLED
```

Possible HTTP mappings:

```text
502
503
429
```

## Server

```text
INTERNAL_ERROR
```

HTTP:

```text
500
```

Do not expose stack traces.

## Frontend behavior

The frontend should translate stable error codes into human-readable messages.

Never build UI logic around raw provider error strings.
