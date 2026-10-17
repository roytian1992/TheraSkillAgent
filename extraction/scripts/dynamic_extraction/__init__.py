"""Dynamic, goal-directed TheraSkill knowledge extraction."""

from pathlib import Path
import sys

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from .contracts import ExtractionPlan, PlanError
from .planner import build_plan, plan_with_model, suggest_schema
from .executor import extract_records, load_source_records

__all__ = [
    "ExtractionPlan",
    "PlanError",
    "build_plan",
    "plan_with_model",
    "suggest_schema",
    "extract_records",
    "load_source_records",
]
