from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from installers.theraskill import (
    ReleaseInstallError,
    doctor,
    download_release,
    enable_profile,
    install_archive,
    install_release,
    rollback_release,
)


def _write_release(path, release_id: str = "r1"):
    path.mkdir()
    artifact = path / "indexes" / "retrieval.jsonl"
    artifact.parent.mkdir()
    artifact.write_text("{}\n", encoding="utf-8")
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "theraskill_platform_contract_v1",
                "release_id": release_id,
                "status": "complete",
                "artifacts": {"retrieval": "indexes/retrieval.jsonl"},
            }
        ),
        encoding="utf-8",
    )


def test_install_release_and_activate_current_slot(tmp_path) -> None:
    source = tmp_path / "candidate"
    _write_release(source)
    root = tmp_path / "platform-data"

    destination = install_release(source, root, activate=True)

    assert destination == root / "releases" / "r1"
    assert (root / "current" / "manifest.json").is_file()
    assert doctor(root)["status"] == "complete"

    with pytest.raises(ReleaseInstallError, match="immutable"):
        install_release(source, root, activate=True)


def test_install_rejects_absolute_artifact_path(tmp_path) -> None:
    source = tmp_path / "candidate"
    _write_release(source)
    payload = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    payload["artifacts"]["retrieval"] = "/tmp/retrieval.jsonl"
    (source / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReleaseInstallError, match="release-relative"):
        install_release(source, tmp_path / "platform-data")


def test_install_validates_checksum_and_rolls_back(tmp_path) -> None:
    source = tmp_path / "candidate"
    _write_release(source, "r2")
    artifact = source / "indexes" / "retrieval.jsonl"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    payload["checksums"] = {"retrieval": f"sha256:{digest}"}
    (source / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    root = tmp_path / "platform-data"
    install_release(source, root, activate=True)

    second = tmp_path / "candidate-second"
    _write_release(second, "r3")
    install_release(second, root)
    assert rollback_release(root, "r3")["release_id"] == "r3"
    assert (root / "current" / "manifest.json").is_file()


def test_install_administrator_uploaded_archive(tmp_path) -> None:
    source = tmp_path / "phase6_v1"
    _write_release(source, "phase6_v1")
    archive = tmp_path / "theraskill-phase6_v1.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    root = tmp_path / "platform-data"
    destination = install_archive(
        archive,
        root,
        release_id="phase6_v1",
        activate=True,
        archive_sha256=digest,
    )

    assert destination == root / "releases" / "phase6_v1"
    assert (root / "current" / "indexes" / "retrieval.jsonl").is_file()
    assert doctor(root)["status"] == "complete"


def test_install_archive_rejects_wrong_package_checksum(tmp_path) -> None:
    source = tmp_path / "release"
    _write_release(source)
    archive = tmp_path / "theraskill-r1.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)

    with pytest.raises(ReleaseInstallError, match="archive checksum mismatch"):
        install_archive(archive, tmp_path / "platform-data", archive_sha256="0" * 64)


def test_install_archive_rejects_path_traversal(tmp_path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"outside"
        member = tarfile.TarInfo("../../outside.txt")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    with pytest.raises(ReleaseInstallError, match="unsafe archive member path"):
        install_archive(archive, tmp_path / "platform-data")

    assert not (tmp_path / "outside.txt").exists()


def test_download_release_uses_manifest_sources(tmp_path) -> None:
    asset = tmp_path / "asset.jsonl"
    asset.write_text("downloaded\n", encoding="utf-8")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "release_id": "downloaded",
                "status": "complete",
                "artifacts": {"retrieval": "retrieval/data.jsonl"},
                "sources": {"retrieval": asset.as_uri()},
                "checksums": {"retrieval": digest},
            }
        ),
        encoding="utf-8",
    )
    destination = download_release(manifest.as_uri(), tmp_path / "platform-data", activate=True)
    assert (destination / "retrieval/data.jsonl").read_text(encoding="utf-8") == "downloaded\n"
    assert doctor(tmp_path / "platform-data")["status"] == "complete"


def test_enable_profile_materializes_hermes_owned_profile(tmp_path) -> None:
    result = enable_profile(tmp_path / "platform-data")
    assert result["domain"] == "theraskill"
    assert (tmp_path / "platform-data/profile.yaml").is_file()
    assert (tmp_path / "platform-data/profile-install.json").is_file()
