from fastapi import APIRouter

router = APIRouter()

# Contract modules are intentionally reserved for authenticated service implementations.
for resource in ("access", "assignments", "policies", "audit-logs", "sync-runs"):
    router.add_api_route(f"/{resource}", lambda resource=resource: {"data": [], "meta": {"resource": resource, "status": "foundation_only"}}, methods=["GET"], include_in_schema=False)
