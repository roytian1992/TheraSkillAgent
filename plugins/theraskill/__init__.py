"""TheraSkillAgent domain tools and artifact contracts."""

from .artifacts import ArtifactStore, ArtifactStoreError
from .contracts import (
    CONTRACT_VERSION,
    ArtifactManifest,
    ContractError,
    TheraSkillRequest,
    TheraSkillResult,
)
from .tools import TOOL_DEFINITIONS, check_theraskill_requirements
from .service import ServiceConfig, service_config
from theraskill_agent.platform import register_theraskill_surface

__all__ = [
    "ArtifactManifest",
    "ArtifactStore",
    "ArtifactStoreError",
    "CONTRACT_VERSION",
    "ContractError",
    "TheraSkillRequest",
    "TheraSkillResult",
    "ServiceConfig",
    "service_config",
]


def register(ctx) -> None:
    """Register the built-in domain surface."""
    register_theraskill_surface(ctx)
