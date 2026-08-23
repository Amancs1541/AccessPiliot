# AccessPilot V1 — Environment Configuration

## Frontend

```env
VITE_ENTRA_TENANT_ID=
VITE_ENTRA_CLIENT_ID=
VITE_ENTRA_REDIRECT_URI=
VITE_ACCESSPILOT_API_SCOPE=
VITE_API_BASE_URL=
```

## Backend

```env
APP_NAME=AccessPilot
ENVIRONMENT=development

DATABASE_URL=
REDIS_URL=

ENTRA_TENANT_ID=
ENTRA_API_CLIENT_ID=
ENTRA_API_CLIENT_SECRET=
ENTRA_AUTHORITY=

GRAPH_BASE_URL=https://graph.microsoft.com/v1.0

FRONTEND_URL=http://localhost:5173

PROVIDER_MODE=mock

PROVIDER_CREDENTIAL_KEY=
```

`PROVIDER_CREDENTIAL_KEY` (added Phase 4) is a symmetric encryption key (Fernet) used only to encrypt/decrypt provider connector credentials (e.g. the Microsoft Graph client secret) stored in `identity_providers.graph_client_secret_encrypted`. It protects database-at-rest credential data; it is not itself a provider credential. Losing or rotating it invalidates previously stored provider secrets, which must then be re-entered via the Providers UI.

## Rules

Frontend variables must never contain:

```text
client secret
database password
Graph application token
private key
```

Backend secrets must not be committed.

## `.env.example`

Commit an example file with blank values.

Do not commit:

```text
.env
.env.local
production secrets
```

## Secret management

Development:

```text
environment variables
```

Production:

```text
secure secret manager / platform secret store
```

## Environment separation

Maintain:

```text
development
test
staging
production
```

Do not connect development code to production Entra resources.

## Configuration validation

Backend should fail fast when required variables are missing.

Never silently use unsafe defaults for:

```text
tenant
database
credentials
provider mode
```
