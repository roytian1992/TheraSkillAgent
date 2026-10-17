"""Candidate validation and current-slot activation for TheraSkill releases."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.request import urlopen

from theraskill_agent.paths import data_root, resource_path

from plugins.theraskill.contracts import ArtifactManifest, ContractError


class ReleaseInstallError(RuntimeError):
    """Raised when a release cannot be installed or activated."""


def enable_profile(root: str | Path | None = None) -> dict[str, Any]:
    """Materialize the bundled TheraSkill profile below the project data directory."""
    target_root = Path(root or data_root()).expanduser()
    target_root.mkdir(parents=True, exist_ok=True)
    source = resource_path("configs/theraskill/profile.yaml")
    if not source.is_file():
        raise ReleaseInstallError(f"bundled TheraSkill profile not found: {source}")
    profile_path = target_root / "profile.yaml"
    shutil.copyfile(source, profile_path)
    metadata = {
        "schema_version": "theraskill_profile_installation_v1",
        "domain": "theraskill",
        "profile_path": str(profile_path),
        "artifact_root": str(target_root),
    }
    (target_root / "profile-install.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def _read_manifest(source: Path) -> tuple[ArtifactManifest, dict[str, Any]]:
    manifest_path = source / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ArtifactManifest.from_mapping(payload)
    except FileNotFoundError as exc:
        raise ReleaseInstallError(f"manifest.json not found in {source}") from exc
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise ReleaseInstallError(f"invalid release manifest in {source}: {exc}") from exc
    return manifest, payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_sha256(value: str, field_name: str) -> str:
    digest = str(value).strip()
    if digest.startswith("sha256:"):
        digest = digest.removeprefix("sha256:")
    if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        raise ReleaseInstallError(f"{field_name} has an invalid sha256 checksum")
    return digest.lower()


def _normalize_archive_parts(member_name: str) -> list[str]:
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)
    if (
        not normalized_name
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ReleaseInstallError(f"unsafe archive member path: {member_name}")
    parts = [part for part in posix_path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ReleaseInstallError(f"unsafe archive member path: {member_name}")
    return parts


def _safe_extract_release_archive(archive: Path, destination: Path) -> None:
    """Extract a gzip tar release without path traversal or link members."""
    seen: set[tuple[str, ...]] = set()
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                parts = _normalize_archive_parts(member.name)
                key = tuple(parts)
                if key in seen:
                    raise ReleaseInstallError(f"duplicate archive member: {member.name}")
                seen.add(key)
                target = destination.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ReleaseInstallError(f"unsupported archive member type: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise ReleaseInstallError(f"cannot read archive member: {member.name}")
                with extracted, target.open("wb") as handle:
                    shutil.copyfileobj(extracted, handle)
                try:
                    target.chmod(member.mode & 0o777)
                except OSError:
                    pass
    except (tarfile.TarError, OSError) as exc:
        raise ReleaseInstallError(
            f"cannot extract release archive {archive}; expected .tar.gz or .tgz: {exc}"
        ) from exc


def _archive_release_root(staging: Path) -> Path:
    if (staging / "manifest.json").is_file():
        return staging
    entries = list(staging.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "manifest.json").is_file():
        return entries[0]
    raise ReleaseInstallError(
        "release archive must contain manifest.json at its root or in one top-level directory"
    )


def _validate_artifacts(source: Path, manifest: ArtifactManifest, raw_payload: dict[str, Any] | None = None) -> None:
    source_root = source.resolve()
    checksums = (raw_payload or {}).get("checksums") or {}
    for name, relative_path in manifest.artifacts.items():
        path = Path(relative_path)
        if path.is_absolute():
            raise ReleaseInstallError(f"artifact {name!r} must use a release-relative path")
        resolved = (source / path).resolve()
        if source_root not in resolved.parents:
            raise ReleaseInstallError(f"artifact {name!r} escapes release directory")
        if not resolved.is_file():
            raise ReleaseInstallError(f"artifact {name!r} not found: {relative_path}")
        declared = checksums.get(name)
        if isinstance(declared, dict):
            declared = declared.get("sha256")
        if declared is not None:
            declared = _normalize_sha256(str(declared), f"artifact {name!r}")
            actual = _sha256_file(resolved)
            if actual.lower() != declared:
                raise ReleaseInstallError(
                    f"artifact {name!r} checksum mismatch: expected {declared}, got {actual}"
                )


def _activate(root: Path, release_dir: Path) -> None:
    root = root.expanduser().resolve()
    release_dir = release_dir.expanduser().resolve()
    try:
        relative_release = release_dir.relative_to(root)
    except ValueError as exc:
        raise ReleaseInstallError(
            f"release directory must be inside the project data root: {release_dir}"
        ) from exc
    current = root / "current"
    temp_link = root / f".current.{os.getpid()}.tmp"
    try:
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(relative_release, target_is_directory=True)
        os.replace(temp_link, current)
    except OSError as exc:
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        raise ReleaseInstallError(f"cannot activate current slot: {exc}") from exc


def install_release(
    source: str | Path,
    root: str | Path | None = None,
    *,
    release_id: str | None = None,
    activate: bool = False,
    link_artifacts: bool = False,
) -> Path:
    """Validate and copy one complete release into an immutable slot."""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_dir():
        raise ReleaseInstallError(f"release source is not a directory: {source_path}")
    manifest, raw_payload = _read_manifest(source_path)
    _validate_artifacts(source_path, manifest, raw_payload)
    target_root = Path(root or data_root()).expanduser()
    target_root.mkdir(parents=True, exist_ok=True)
    releases_root = target_root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True)
    selected_id = release_id or manifest.release_id
    if selected_id != manifest.release_id:
        raise ReleaseInstallError(
            f"release_id mismatch: requested {selected_id!r}, manifest has {manifest.release_id!r}"
        )
    if not selected_id or selected_id in {".", ".."} or "/" in selected_id or "\\" in selected_id:
        raise ReleaseInstallError(f"invalid release_id: {selected_id!r}")
    destination = releases_root / selected_id
    if destination.exists():
        raise ReleaseInstallError(f"release slot already exists and is immutable: {destination}")

    staging = Path(tempfile.mkdtemp(prefix=f".{selected_id}.", dir=str(releases_root)))
    try:
        if link_artifacts:
            def _copy_or_link(src: str, dst: str) -> str:
                if Path(src).name == "manifest.json":
                    return shutil.copy2(src, dst)
                os.link(src, dst)
                return dst

            shutil.copytree(source_path, staging, dirs_exist_ok=True, copy_function=_copy_or_link)
        else:
            shutil.copytree(source_path, staging, dirs_exist_ok=True)
        (staging / "manifest.json").write_text(
            json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ReleaseInstallError(f"cannot install release {selected_id}: {exc}") from exc

    if activate:
        _activate(target_root, destination)
    (target_root / "install-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "theraskill_installation_v1",
                "release_id": selected_id,
                "release_path": str(destination),
                "activated": bool(activate),
                "artifacts": dict(manifest.artifacts),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def install_archive(
    archive: str | Path,
    root: str | Path | None = None,
    *,
    release_id: str | None = None,
    activate: bool = False,
    archive_sha256: str | None = None,
) -> Path:
    """Install one administrator-uploaded, immutable release archive."""
    archive_path = Path(archive).expanduser().resolve()
    if not archive_path.is_file():
        raise ReleaseInstallError(f"release archive is not a file: {archive_path}")
    if archive_sha256:
        expected = _normalize_sha256(archive_sha256, "release archive")
        actual = _sha256_file(archive_path)
        if actual.lower() != expected:
            raise ReleaseInstallError(
                f"release archive checksum mismatch: expected {expected}, got {actual}"
            )

    target_root = Path(root or data_root()).expanduser()
    releases_root = target_root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".archive.", dir=str(releases_root)))
    try:
        _safe_extract_release_archive(archive_path, staging)
        source = _archive_release_root(staging)
        return install_release(
            source,
            target_root,
            release_id=release_id,
            activate=activate,
            link_artifacts=True,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def download_release(
    manifest_url: str,
    root: str | Path | None = None,
    *,
    activate: bool = False,
) -> Path:
    """Download a manifest-declared release into an immutable TheraSkillAgent slot.

    The manifest must contain a ``sources`` object mapping artifact names to
    URLs.  Each downloaded file is checked against the manifest checksum before
    the normal immutable install path is used.
    """
    target_root = Path(root or data_root()).expanduser()
    with urlopen(manifest_url, timeout=60) as response:
        raw_payload = json.loads(response.read().decode("utf-8"))
    manifest = ArtifactManifest.from_mapping(raw_payload)
    sources = raw_payload.get("sources") or {}
    if not isinstance(sources, dict):
        raise ReleaseInstallError("manifest sources must be an object")
    staging = Path(tempfile.mkdtemp(prefix=f".{manifest.release_id}.download-"))
    try:
        (staging / "manifest.json").write_text(
            json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for name, relative_path in manifest.artifacts.items():
            source_url = sources.get(name)
            if not source_url:
                raise ReleaseInstallError(f"manifest does not provide a source URL for artifact {name!r}")
            destination = staging / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with urlopen(str(source_url), timeout=600) as response, destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        return install_release(staging, target_root, release_id=manifest.release_id, activate=activate)
    except ReleaseInstallError:
        raise
    except Exception as exc:
        raise ReleaseInstallError(f"cannot download release {manifest.release_id}: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def doctor(root: str | Path | None = None, release_id: str = "current") -> dict[str, Any]:
    """Validate one installed release and return machine-readable checks."""
    target_root = Path(root or data_root()).expanduser()
    manifest_path = target_root / "current" / "manifest.json" if release_id == "current" else target_root / "releases" / release_id / "manifest.json"
    checks: dict[str, bool] = {"manifest_exists": manifest_path.is_file()}
    if not checks["manifest_exists"]:
        return {"status": "failed", "release_id": release_id, "checks": checks}
    try:
        manifest, _ = _read_manifest(manifest_path.parent)
        raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_artifacts(manifest_path.parent, manifest, raw_payload)
        checks.update({"manifest_valid": True, "artifacts_present": True})
        return {"status": "complete", "release_id": manifest.release_id, "checks": checks}
    except ReleaseInstallError:
        checks.update({"manifest_valid": False, "artifacts_present": False})
        return {"status": "failed", "release_id": release_id, "checks": checks}


def rollback_release(root: str | Path | None, release_id: str) -> dict[str, Any]:
    """Activate an already-installed immutable release by id."""
    target_root = Path(root or data_root()).expanduser()
    destination = target_root / "releases" / release_id
    if not destination.is_dir():
        raise ReleaseInstallError(f"release slot not found: {release_id}")
    manifest, raw_payload = _read_manifest(destination)
    _validate_artifacts(destination, manifest, raw_payload)
    _activate(target_root, destination)
    return {"status": "complete", "release_id": manifest.release_id, "path": str(destination)}
