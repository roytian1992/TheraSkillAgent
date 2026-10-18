"""TheraSkillAgent domain registration and capability catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domains.theraskill.provider_config import ProviderConfigError, public_provider_info
from domains.theraskill.runtime import service_specs


THERASKILL_CAPABILITIES = (
    "resources",
    "knowledge_search",
    "support",
    "simulation",
    "response_review",
    "contribution",
    "extraction",
    "transcript",
)


@dataclass(frozen=True)
class TheraSkillPlatform:
    """Stable host-facing identity for the built-in TheraSkill platform."""

    platform_id: str = "theraskill"
    display_name: str = "TheraSkillAgent"
    first_party: bool = True

    def capabilities(self) -> tuple[str, ...]:
        return THERASKILL_CAPABILITIES

    def provider_snapshot(self) -> dict[str, Any]:
        """Return a secret-free provider snapshot for status pages and tools."""
        try:
            return {
                "llm": public_provider_info("llm"),
                "embedding": public_provider_info("embedding"),
            }
        except ProviderConfigError as exc:
            return {"error": str(exc)}

    def runtime_snapshot(self) -> dict[str, Any]:
        specs = service_specs()
        return {
            "platform_id": self.platform_id,
            "display_name": self.display_name,
            "first_party": self.first_party,
            "capabilities": list(self.capabilities()),
            "services": {
                name: {"module": str(spec["module"]), "transport": "inprocess", "api_path": f"/api/{name}"}
                for name, spec in specs.items()
            },
            "providers": self.provider_snapshot(),
        }


THERASKILL_PLATFORM = TheraSkillPlatform()


def register_theraskill_surface(ctx: Any) -> None:
    """Register the built-in tools, views and skills.

    This is intentionally the one registration path. The legacy plugin
    ``register`` function delegates here so existing plugin discovery and new
    TheraSkillAgent bootstrap code observe identical tools, skills and apps.
    """
    from plugins.theraskill.tools import TOOL_DEFINITIONS, check_theraskill_requirements
    from theraskill_agent.apps import DashboardApp, register_app

    for name, schema, handler, emoji, description in TOOL_DEFINITIONS:
        ctx.register_tool(
            name=name,
            toolset="theraskill",
            schema=schema,
            handler=handler,
            check_fn=check_theraskill_requirements,
            description=description,
            emoji=emoji,
        )

    apps = (
        DashboardApp(
            app_id="theraskill.support", label="TheraSkill Support",
            path="/theraskill/support", description="Support assistant sessions and consent-aware memory.",
            allowed_views=("home", "session"), context_keys=("session_id",),
            required_roles=("user", "learner", "researcher", "system"), icon="MessageCircleHeart",
        ),
        DashboardApp(
            app_id="theraskill.simulation", label="TheraSkill Simulation",
            path="/theraskill/simulation", description="Patient simulation sessions, turns, review, and training feedback.",
            allowed_views=("home", "session", "review"), context_keys=("session_id", "scenario_id"),
            required_roles=("learner", "instructor", "researcher", "system"), icon="Drama",
        ),
        DashboardApp(
            app_id="theraskill.expert", label="TheraSkill Expert Studio",
            path="/theraskill/expert", description="Expert review, case workspaces, and knowledge governance.",
            allowed_views=("queue", "case", "governance"), context_keys=("job_id", "case_id", "contribution_id"),
            required_roles=("expert", "knowledge_curator", "adjudicator", "release_manager", "admin"), icon="ShieldCheck",
        ),
    )
    for app in apps:
        register_app(app)

    skills_root = Path(__file__).resolve().parents[1] / "plugins" / "theraskill" / "skills"
    for skill_name in (
        "emotional-support", "risk-triage", "cognitive-reframing-support",
        "rebt-belief-work-support", "synthetic-dialogue-builder",
        "therapy-response-reviewer", "dynamic-knowledge-extraction",
        "theraskill-agent-operator", "transcript-normalizer",
        "transcript-quality-reviewer", "transcript-evaluator", "knowledge-index-manager",
    ):
        skill_path = skills_root / skill_name / "SKILL.md"
        ctx.register_skill(
            skill_name,
            skill_path,
            skill_path.read_text(encoding="utf-8").split("\n", 4)[2].strip(),
        )


def register(ctx: Any) -> None:
    """PluginContext-compatible entry point for the built-in loader."""
    register_theraskill_surface(ctx)
