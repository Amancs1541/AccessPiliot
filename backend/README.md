# AccessPilot Backend Foundation

Backend Foundation V1 provides the FastAPI application, normalized PostgreSQL schema, Alembic migration, provider abstraction, and mock connector. Authentication, Microsoft Graph, PIM, real JIT, and provider mutations are intentionally deferred.

## Setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `DATABASE_URL` to a reachable PostgreSQL database. `PROVIDER_MODE=mock` is the only enabled provider mode in this phase.

## Run

```powershell
.venv\Scripts\alembic upgrade head
.venv\Scripts\uvicorn app.main:app --reload
```

Health checks:

- `GET http://localhost:8000/health`
- `GET http://localhost:8000/api/v1/health`

The API returns `X-Request-ID` and accepts a caller-provided `X-Request-ID`. No authentication or privileged operation endpoints are enabled yet.

## Tests

```powershell
.venv\Scripts\pytest
```
