from __future__ import annotations

import json
import sqlite3

from plugins.theraskill.retrieval import search_knowledge, search_local


def test_search_local_uses_current_hermes_release(monkeypatch, tmp_path) -> None:
    hermes_home = tmp_path / "hermes-home"
    release = hermes_home / "platform-data" / "theraskill" / "releases" / "r1"
    release.mkdir(parents=True)
    db_path = release / "retrieval.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE VIRTUAL TABLE retrieval_fts USING fts5(retrieval_id UNINDEXED, retrieval_text);
        CREATE TABLE retrieval_meta (
          retrieval_id TEXT PRIMARY KEY,
          release_unit_id TEXT NOT NULL,
          release_unit_type TEXT,
          dedup_family TEXT,
          primary_knowledge_type TEXT,
          risk_scope TEXT,
          default_route TEXT,
          retrieval_eligible INTEGER
        );
        """
    )
    connection.execute("INSERT INTO retrieval_fts VALUES (?, ?)", ("ret-1", "reflect the user's emotion before asking a question"))
    connection.execute("INSERT INTO retrieval_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("ret-1", "unit-1", "reviewed_cluster", "therapy_process", "interaction_pattern", "ordinary_or_unspecified", "reviewed_cluster_retrieval", 1))
    connection.commit()
    connection.close()
    (release / "manifest.json").write_text(
        json.dumps({"release_id": "r1", "status": "complete", "artifacts": {"sqlite_fts": "retrieval.sqlite"}}),
        encoding="utf-8",
    )
    (hermes_home / "platform-data" / "theraskill" / "current").symlink_to(release, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    result = search_local("emotion", limit=3)

    assert result["release_id"] == "r1"
    assert result["matches"][0]["retrieval_id"] == "ret-1"
    assert result["matches"][0]["release_unit_id"] == "unit-1"


def test_search_knowledge_reports_lexical_fallback(monkeypatch, tmp_path) -> None:
    hermes_home = tmp_path / "hermes-home"
    release = hermes_home / "platform-data" / "theraskill" / "releases" / "r1"
    release.mkdir(parents=True)
    db_path = release / "retrieval.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE retrieval_fts USING fts5(retrieval_id UNINDEXED, retrieval_text);
            CREATE TABLE retrieval_meta (
              retrieval_id TEXT PRIMARY KEY, release_unit_id TEXT NOT NULL,
              release_unit_type TEXT, dedup_family TEXT, primary_knowledge_type TEXT,
              risk_scope TEXT, default_route TEXT, retrieval_eligible INTEGER
            );
            INSERT INTO retrieval_fts VALUES ('ret-1', 'reflect emotion');
            INSERT INTO retrieval_meta VALUES ('ret-1', 'unit-1', '', '', '', '', '', 1);
            """
        )
    (release / "manifest.json").write_text(
        json.dumps({"release_id": "r1", "status": "complete", "artifacts": {"sqlite_fts": "retrieval.sqlite"}}),
        encoding="utf-8",
    )
    (hermes_home / "platform-data" / "theraskill" / "current").symlink_to(release, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    result = search_knowledge("emotion")

    assert result["retrieval_mode"] == "lexical_fallback"
    assert result["warnings"] == [
        "release lacks hybrid artifacts: compact_label_v1, faiss_shards_manifest, metadata_v2"
    ]


def test_search_knowledge_prefers_shared_hybrid_retriever(monkeypatch, tmp_path) -> None:
    hermes_home = tmp_path / "hermes-home"
    release = hermes_home / "platform-data" / "theraskill" / "releases" / "r1"
    (release / "faiss").mkdir(parents=True)
    for name in ("retrieval.sqlite", "metadata.sqlite", "labels.sqlite", "faiss/manifest.json"):
        (release / name).touch()
    (release / "manifest.json").write_text(
        json.dumps({
            "release_id": "r1", "status": "complete",
            "artifacts": {
                "sqlite_fts": "retrieval.sqlite", "metadata_v2": "metadata.sqlite",
                "compact_label_v1": "labels.sqlite", "faiss_shards_manifest": "faiss/manifest.json",
            },
        }),
        encoding="utf-8",
    )
    (hermes_home / "platform-data" / "theraskill" / "current").symlink_to(release, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    class FakeHybridRetriever:
        def __init__(self, _root, paths) -> None:
            assert paths.faiss_shards == release / "faiss"

        def search(self, query, *, skill, top_k):
            assert (query, skill, top_k) == ("emotion", "emotional-support", 4)
            return {"items": [{"retrieval_id": "hybrid-1"}], "policy_checks": {"ok": True}}

    monkeypatch.setattr("plugins.theraskill.retrieval.TheraSkillHybridRetriever", FakeHybridRetriever)

    result = search_knowledge("emotion", limit=4)

    assert result["retrieval_mode"] == "hybrid_v3"
    assert result["matches"] == [{"retrieval_id": "hybrid-1"}]
