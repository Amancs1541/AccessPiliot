# AccessPilot V1 — Architecture

## 1. Layers

```text
Presentation
  React + TypeScript

API
  FastAPI routers

Application
  Services / Use Cases

Domain
  Policies / State Machines / Authorization

Infrastructure
  PostgreSQL / Redis / Provider Connectors

External
  Microsoft Graph / Entra
```

## 2. Backend layering

```text
routers/
services/
domain/
providers/
repositories/
models/
schemas/
workers/
security/
```

Routers must not contain business logic.

## 3. Provider abstraction

```text
IdentityProvider
   |
   +-- EntraProvider
   |
   +-- OktaProvider (future)
```

Services depend on `IdentityProvider`, not Microsoft Graph SDK classes.

## 4. Data flow

```text
React
 -> FastAPI
 -> Authentication
 -> Authorization
 -> Policy
 -> Service
 -> Provider Connector
 -> External Provider
 -> Persist
 -> Audit
```

## 5. Authentication

Frontend uses MSAL.

Backend validates:

- signature
- issuer
- audience
- tenant
- expiry
- required claims

## 6. Authorization

```text
JWT roles
  -> AccessPilot permission mapping
  -> endpoint authorization
  -> resource-level authorization
```

Normal users must be restricted to their own resources.

## 7. Graph authentication

Backend uses application authentication for provider management.

Never send the user's password to AccessPilot.

## 8. PostgreSQL

PostgreSQL stores normalized provider state and AccessPilot governance state.

## 9. Background processing

Workers handle:

- expiration
- synchronization
- reconciliation

## 10. Reliability

Provider failures must not silently create false ACTIVE state.

A provider mutation must be verified before the local assignment becomes ACTIVE.

## 11. Future connector

Adding Okta should require:

```text
new connector
new provider configuration
provider mapping tests
```

and should not require rewriting:

```text
policy engine
approval engine
audit
JIT state machine
React pages
core database
```
