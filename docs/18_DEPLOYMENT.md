# AccessPilot V1 — Deployment

## 1. Recommended production architecture

```text
Internet
   |
HTTPS
   |
React SPA
   |
HTTPS
   v
FastAPI
   |
   +--> PostgreSQL
   |
   +--> Redis
   |
   +--> Microsoft Graph
```

## 2. Components

### Frontend

Static React application.

### Backend

FastAPI application.

### Database

PostgreSQL.

### Worker

Separate process/container running background jobs.

### Redis

Optional but recommended for:

- task coordination
- rate limiting
- distributed locks
- background job queues

## 3. Environment separation

Use:

```text
development
test
staging
production
```

Each environment should have separate:

```text
database
credentials
Entra app registrations where practical
redirect URIs
provider configuration
```

## 4. HTTPS

Production must use HTTPS.

Never send:

```text
access tokens
credentials
provider secrets
```

over plain HTTP.

## 5. Entra redirect URI

Production redirect URI must exactly match the deployed frontend configuration.

Update Entra before production login testing.

## 6. Secrets

Production secrets must be stored in a secure secret-management system.

Never:

```text
commit secrets
put secrets in React
put secrets in Docker image
put secrets in logs
```

## 7. Database

Use:

```text
managed PostgreSQL or secured PostgreSQL deployment
```

Enable:

- encrypted connections
- backups
- monitoring
- restricted network access
- least-privileged database user

## 8. Migrations

Run Alembic migrations as a controlled deployment step.

Never manually edit production schema.

## 9. Monitoring

Monitor:

```text
API latency
5xx errors
401/403 rates
Graph failures
Graph throttling
sync failures
worker failures
database connections
expiration failures
```

## 10. Health checks

Provide:

```text
/health
/api/v1/health
```

Do not expose sensitive configuration in health responses.

## 11. Backup

Database backups must include:

```text
governance data
requests
assignments
audit
policies
provider metadata
```

Test restoration.

## 12. Deployment sequence

```text
Build
 -> Unit tests
 -> Security tests
 -> Build frontend
 -> Build backend
 -> Apply migrations
 -> Deploy backend
 -> Deploy worker
 -> Deploy frontend
 -> Configure Entra redirect URI
 -> Smoke test
 -> Monitor
```

## 13. Rollback

Application deployments must be rollbackable.

Database migrations must be designed carefully so application rollback does not corrupt governance data.

## 14. Production acceptance

Before release:

```text
[ ] HTTPS
[ ] Entra login
[ ] Correct redirect URI
[ ] Graph permissions reviewed
[ ] Secrets secured
[ ] Database backups
[ ] Audit enabled
[ ] Workers enabled
[ ] Monitoring enabled
[ ] Security tests passed
[ ] Admin/User authorization verified
[ ] JIT expiration tested
```
