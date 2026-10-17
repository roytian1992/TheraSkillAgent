from __future__ import annotations

import pytest

from domains.theraskill.hybrid_retrieval import (
    HybridRetrievalError,
    HybridRetrievalPaths,
    TheraSkillHybridRetriever,
)


def test_retriever_fails_when_dense_and_shard_metadata_are_missing(tmp_path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    paths = HybridRetrievalPaths(
        sqlite_fts=artifact_dir / "retrieval.sqlite",
        metadata_v2=artifact_dir / "metadata.sqlite",
        compact_label=artifact_dir / "labels.sqlite",
        faiss_shards=artifact_dir / "faiss",
        model_config=artifact_dir / "retrieval.yaml",
        cache_root=tmp_path / "cache",
    )
    for path in (
        paths.sqlite_fts,
        paths.metadata_v2,
        paths.compact_label,
        paths.model_config,
    ):
        path.touch()
    paths.faiss_shards.mkdir()

    with pytest.raises(HybridRetrievalError, match="dense metadata or FAISS shard manifest"):
        TheraSkillHybridRetriever(tmp_path, paths)


def test_retriever_accepts_project_owned_faiss_shard_manifest(tmp_path, monkeypatch) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    paths = HybridRetrievalPaths(
        sqlite_fts=artifact_dir / "retrieval.sqlite",
        metadata_v2=artifact_dir / "metadata.sqlite",
        compact_label=artifact_dir / "labels.sqlite",
        faiss_shards=artifact_dir / "faiss",
        model_config=artifact_dir / "retrieval.yaml",
        cache_root=tmp_path / "cache",
    )
    for path in (paths.sqlite_fts, paths.metadata_v2, paths.compact_label, paths.model_config):
        path.touch()
    paths.faiss_shards.mkdir()
    (paths.faiss_shards / "manifest.json").write_text('{"shards": []}', encoding="utf-8")
    monkeypatch.setattr(TheraSkillHybridRetriever, "_load_retriever", lambda self: object())
    retriever = TheraSkillHybridRetriever(tmp_path, paths)
    assert retriever.paths.faiss_shards == paths.faiss_shards
