"""TheraSkillAgent's neutral local development kernel.

The package deliberately keeps skill lifecycle and text processing small and
dependency-light.  Domain services can import these primitives without
depending on a particular host application.
"""

from .skills import SkillManager, SkillQualityReport, SkillRecord
from .text import TextChunk, chunk_text, extract_sections, normalize_text, redact_secrets

__all__ = [
    "SkillManager",
    "SkillQualityReport",
    "SkillRecord",
    "TextChunk",
    "chunk_text",
    "extract_sections",
    "normalize_text",
    "redact_secrets",
]

from .contracts import AgentResult, AgentTask, SkillExecutionContext, SkillManifest
from .workflows import SkillWorkflow, interaction_for_hermes
from .transcript import TranscriptDocument, TranscriptTurn, analyze_transcript, build_bundle, load_transcript, parse_transcript
from .reviews import build_review_plan, run_skill_reviews
from .paths import active_release_id, data_root, project_root
from .runtime import AgentRuntime
from .eval import EvalCase, EvalRunner, load_cases
from .source_adapters import (
    SourceAdapterProtocol,
    SourceInfo,
    SourceRecord,
    adapter_registry,
    get_adapter,
    inspect_source,
    register_adapter,
    sample_source,
    unregister_adapter,
)

__all__ += ["AgentResult", "AgentTask", "AgentRuntime", "EvalCase", "EvalRunner", "load_cases", "SkillExecutionContext", "SkillManifest", "SkillWorkflow", "interaction_for_hermes", "TranscriptDocument", "TranscriptTurn", "analyze_transcript", "build_bundle", "load_transcript", "parse_transcript", "build_review_plan", "run_skill_reviews", "SourceAdapterProtocol", "SourceInfo", "SourceRecord", "adapter_registry", "get_adapter", "inspect_source", "register_adapter", "sample_source", "unregister_adapter"]
__all__ += ["active_release_id", "data_root", "project_root"]

__version__ = "0.4.0"
