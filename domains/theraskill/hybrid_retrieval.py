"""Shared runtime adapter for TheraSkill's policy-aware hybrid retriever."""

from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from theraskill_agent.query_understanding import SUPPORTED_RETRIEVAL_SKILLS, understand_query

THERASKILL_RETRIEVAL_SKILLS = set(SUPPORTED_RETRIEVAL_SKILLS)

_RETRIEVER_MODULE_CACHE: dict[Path, Any] = {}
_RETRIEVER_MODULE_CACHE_LOCK = threading.Lock()


class HybridRetrievalError(RuntimeError):
    """Raised when the installed hybrid retrieval surface cannot be used."""


def search_policy_lexical(project_root: Path, db: Path, query: str, *, skill: str, top_k: int = 8, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Use the existing skill policies and evidence roles with SQLite alone."""
    from types import SimpleNamespace

    if skill not in THERASKILL_RETRIEVAL_SKILLS:
        raise HybridRetrievalError(f"unsupported retrieval skill: {skill}")
    if not db.is_file():
        raise HybridRetrievalError("knowledge index is not installed; install a knowledge release before using knowledge-grounded support")
    script = project_root / "scripts/AdvancedRetrieval/retrieve_advanced_v2.py"
    with _RETRIEVER_MODULE_CACHE_LOCK:
        module = _RETRIEVER_MODULE_CACHE.get(script)
        if module is None:
            for directory in (script.parent, project_root / "scripts/_common"):
                if str(directory) not in sys.path:
                    sys.path.insert(0, str(directory))
            spec = importlib.util.spec_from_file_location("theraskill_policy_lexical", script)
            if spec is None or spec.loader is None:
                raise HybridRetrievalError(f"cannot load lexical retrieval: {script}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _RETRIEVER_MODULE_CACHE[script] = module
    understanding = understand_query(query, skill=skill, filters=filters)
    bundle = module.build_bundle(SimpleNamespace(
        db=str(db), query=understanding["normalized_query"], filters=understanding["filters"], skill=skill, top_k=max(1, min(int(top_k), 50)), candidate_multiplier=30,
    ))
    bundle["retrieval_mode"] = "policy_lexical_fallback"
    bundle["query_understanding"] = understanding
    return bundle


@dataclass(frozen=True)
class HybridRetrievalPaths:
    sqlite_fts: Path
    metadata_v2: Path
    compact_label: Path
    faiss_shards: Path
    model_config: Path
    cache_root: Path

    def validate(self) -> None:
        required = {
            "sqlite_fts": self.sqlite_fts,
            "metadata_v2": self.metadata_v2,
            "compact_label": self.compact_label,
            "faiss_shards": self.faiss_shards,
            "model_config": self.model_config,
        }
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            raise HybridRetrievalError(
                f"hybrid retrieval artifact(s) unavailable: {', '.join(missing)}"
            )


def selected_retrieval_paths(model_config: Path, cache_root: Path) -> HybridRetrievalPaths:
    """Resolve the active manifest without replacing a historical data release."""
    from theraskill_agent.resources import active_index
    from theraskill_agent.indexing.common import safe_path
    from domains.theraskill.runtime import runtime_root

    selected = active_index()
    if selected:
        directory, manifest = selected
        artifacts = manifest["artifacts"]
        return HybridRetrievalPaths(
            sqlite_fts=safe_path(directory, artifacts["sqlite_fts"]),
            metadata_v2=safe_path(directory, artifacts["metadata_v2"]),
            compact_label=safe_path(directory, artifacts["compact_label"]),
            faiss_shards=safe_path(directory, artifacts["faiss_shards"]).parent if artifacts.get("faiss_shards") else directory / "no-vector-index",
            model_config=model_config, cache_root=cache_root / manifest["index_id"],
        )
    directory = runtime_root() / "current" / "retrieval"
    return HybridRetrievalPaths(directory / "phase6_retrieval_fts.sqlite", directory / "metadata_v2.sqlite",
                                directory / "compact_label_v1.sqlite", directory / "faiss_bge_m3_shards_v1",
                                model_config, cache_root)


class TheraSkillHybridRetriever:
    """Run the shared hybrid retrieval implementation for TheraSkillAgent."""

    DENSE_METADATA_RELATIVE_PATH = Path(
        "outputs/knowledge_engine/indexes/dense_bge_m3/metadata.json"
    )

    def __init__(self, project_root: str | Path, paths: HybridRetrievalPaths) -> None:
        self.project_root = Path(project_root).resolve()
        self.paths = paths
        self.paths.validate()
        # A release may ship either the historical memmap index or the complete
        # FAISS shard bundle. The latter is self-contained and does not need the
        # training-time dense metadata file from the old build tree.
        dense_metadata = self.project_root / self.DENSE_METADATA_RELATIVE_PATH
        shard_manifest = self.paths.faiss_shards / "manifest.json"
        if not dense_metadata.is_file() and not shard_manifest.is_file():
            raise HybridRetrievalError(
                "dense metadata or FAISS shard manifest not found: "
                f"{dense_metadata} / {shard_manifest}"
            )
        missing = [name for name in ("numpy", "faiss") if importlib.util.find_spec(name) is None]
        if missing:
            raise HybridRetrievalError(
                "hybrid retrieval requires the optional retrieval extra; "
                "install with: pip install 'theraskill-agent[retrieval]' "
                f"(missing: {', '.join(missing)})"
            )
        self.paths.cache_root.mkdir(parents=True, exist_ok=True)
        self._module = self._load_retriever()

    def _load_retriever(self):
        advanced_dir = self.project_root / "scripts" / "AdvancedRetrieval"
        agent_skill_dir = self.project_root / "scripts" / "AgentSkill"
        common_dir = self.project_root / "scripts" / "_common"
        script = advanced_dir / "retrieve_hybrid_v3_llm_structured.py"
        if not script.is_file():
            raise HybridRetrievalError(f"hybrid retrieval implementation not found: {script}")
        script = script.resolve()
        with _RETRIEVER_MODULE_CACHE_LOCK:
            cached = _RETRIEVER_MODULE_CACHE.get(script)
            if cached is not None:
                return cached
        for directory in (advanced_dir, agent_skill_dir, common_dir):
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location(
            "theraskill_shared_hybrid_retrieval_v3", script
        )
        if spec is None or spec.loader is None:
            raise HybridRetrievalError(f"cannot load hybrid retrieval implementation: {script}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise HybridRetrievalError(
                f"cannot initialize hybrid retrieval implementation: {exc}"
            ) from exc
        with _RETRIEVER_MODULE_CACHE_LOCK:
            _RETRIEVER_MODULE_CACHE[script] = module
        return module

    def search(self, query: str, *, skill: str, top_k: int = 8, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        understanding = understand_query(query, skill=skill, filters=filters)
        clean_query = understanding["normalized_query"]
        if not clean_query:
            raise HybridRetrievalError("query is empty")
        if understanding['needs_clarification']:
            return {'query': clean_query, 'skill': skill, 'items': [],
                    'query_understanding': understanding,
                    'quality_checks': {'needs_clarification': True}}
        if skill not in THERASKILL_RETRIEVAL_SKILLS:
            raise HybridRetrievalError(f"unsupported retrieval skill: {skill}")
        top_k = max(1, min(int(top_k), 50))
        skill_cache = self.paths.cache_root / skill
        skill_cache.mkdir(parents=True, exist_ok=True)
        argv = [
            "--db", str(self.paths.sqlite_fts),
            "--metadata-v2-db", str(self.paths.metadata_v2),
            "--compact-label-db", str(self.paths.compact_label),
            "--faiss-shards-dir", str(self.paths.faiss_shards),
            "--model-config", str(self.paths.model_config),
            "--embedding-cache-db", str(skill_cache / "embedding_cache.sqlite"),
            "--scope-count-cache-db", str(skill_cache / "scope_count_cache.sqlite"),
            "--skill", skill,
            "--query", clean_query,
            "--top-k", str(top_k),
            "--dense-backend", "faiss_shards",
            "--dense-pool-n", "120",
            "--dense-top-n", "30",
            "--bm25-top-n", "80",
            "--label-top-n", "40",
            "--required-family-top-n", "80",
            "--max-wide-scope-channel-scan", "250000",
            "--max-compact-label-scope", "250000",
            "--max-scoped-dense-ids", "250000",
        ]
        try:
            args = self._module.parse_args(argv)
            args.query_understanding = understanding
            bundle = self._module.build_bundle(args)
            bundle["query_understanding"] = understanding
            return bundle
        except HybridRetrievalError:
            raise
        except Exception as exc:
            raise HybridRetrievalError(f"hybrid retrieval failed: {exc}") from exc
