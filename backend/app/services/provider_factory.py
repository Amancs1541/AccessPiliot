from app.core.config import Settings
from app.providers.base import IdentityProvider
from app.providers.entra import EntraProvider
from app.providers.mock import MockProvider


def get_provider(settings: Settings) -> IdentityProvider:
    if settings.provider_mode == "mock":
        return MockProvider()
    return EntraProvider()
