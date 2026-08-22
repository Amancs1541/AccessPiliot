from app.models import AccessAssignment, AccessRequest, ApprovalStep, AuditLog, Group, IdentityProvider, Policy, PolicyTarget, ProviderResource, Role, RoleAssignment, SyncError, SyncRun, User, UserGroup


def test_model_registry_contains_documented_entities() -> None:
    assert all(model.__tablename__ for model in [IdentityProvider, User, Group, Role, UserGroup, RoleAssignment, AccessAssignment, AccessRequest, ApprovalStep, Policy, PolicyTarget, AuditLog, SyncRun, SyncError, ProviderResource])
    assert "uq_users_provider_external" in {constraint.name for constraint in User.__table__.constraints}
