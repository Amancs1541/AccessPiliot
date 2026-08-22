# AccessPilot V1 — Provider Connector Contract

## Purpose

Hide provider-specific implementation behind a stable interface.

## Interface

Conceptually:

```python
class IdentityProvider:

    async def test_connection(self): ...

    async def get_users(self, query): ...

    async def get_user(self, external_id): ...

    async def get_groups(self, query): ...

    async def get_group(self, external_id): ...

    async def get_group_members(self, external_id): ...

    async def add_group_member(self, group_external_id, user_external_id): ...

    async def remove_group_member(self, group_external_id, user_external_id): ...

    async def get_roles(self, query): ...

    async def get_role(self, external_id): ...

    async def get_role_assignments(self, external_role_id): ...

    async def activate_assignment(self, request): ...

    async def revoke_assignment(self, assignment): ...

    async def extend_assignment(self, assignment, duration): ...

    async def sync(self): ...
```

Exact signatures can be adapted to the domain models.

## Implementations

```text
IdentityProvider
  |
  +-- EntraProvider
  |
  +-- OktaProvider (future)
```

## Rules

Business services must not know:

```text
Graph URLs
Graph SDK objects
Okta SDK objects
provider-specific HTTP details
```

They should call:

```python
provider.get_users(...)
provider.activate_assignment(...)
```

## Provider result

Return normalized domain objects.

Do not expose raw Graph responses to React.

## Provider errors

Map provider failures into stable AccessPilot errors.

```text
authentication failure
permission denied
not found
conflict
rate limit
timeout
unavailable
unknown
```

## Idempotency

Provider mutations should be safely retryable where possible.

## Verification

After a mutation, verify provider state before claiming success.

## Mock connector

A `MockProvider` must exist for local development and automated tests.

Configuration:

```text
PROVIDER_MODE=mock
```

Real connector:

```text
PROVIDER_MODE=entra
```

## Future Okta

Okta implementation must conform to this interface.

Core services must not change merely because the provider changes.
