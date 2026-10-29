#!/usr/bin/env python3
"""Build an offline application ZIP with Python and Linux shared libraries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PINNED_DEPENDENCIES = [
    "PyYAML==6.0.1",
    "python-dotenv==1.2.2",
    "pydantic==2.8.2",
    "fastapi==0.115.9",
    "uvicorn==0.44.0",
    "httpx==0.27.0",
    "json-repair==0.58.7",
    "numpy==1.26.4",
    "faiss-cpu==1.13.2",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_bundle(output: Path, runtime_archive: Path, version: str) -> Path:
    output = output.expanduser().resolve()
    runtime_archive = runtime_archive.expanduser().resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    if not runtime_archive.is_file():
        raise ValueError(f"portable Python runtime does not exist: {runtime_archive}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".theraskill-offline-", dir=output.parent) as temp_name:
        staging = Path(temp_name)
        build_dir = staging / "_wheel-build"
        wheelhouse = staging / "wheelhouse"
        build_dir.mkdir()
        wheelhouse.mkdir()
        run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(build_dir)], cwd=ROOT)
        project_wheel = next(build_dir.glob("theraskill_agent-*.whl"), None)
        if project_wheel is None:
            raise RuntimeError("wheel build did not produce theraskill_agent-*.whl")
        copy_file(project_wheel, staging / "packages" / project_wheel.name)
        copy_file(runtime_archive, staging / "runtime/python-3.12.7-linux-x86_64.tar.gz")
        shutil.rmtree(build_dir)
        run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
             "--disable-pip-version-check", "--dest", str(wheelhouse), *PINNED_DEPENDENCIES])
        for relative in (
            "docs/deployment.en.md", "docs/deployment.zh.md",
            "docs/resource_installation.en.md", "docs/resource_installation.zh.md",
            "docs/deployment_offline.en.md", "docs/deployment_offline.zh.md",
            "docs/user_guide.en.md", "docs/user_guide.zh.md",
        ):
            copy_file(ROOT / relative, staging / relative)
        shutil.copytree(ROOT / "docs/images/user-guide", staging / "docs/images/user-guide")
        copy_file(ROOT / "scripts/TheraSkill/install_offline.sh", staging / "install_offline.sh")
        copy_file(ROOT / "scripts/TheraSkill/start_offline.sh", staging / "start_offline.sh")
        copy_file(ROOT / "configs/theraskill/providers.local.example.yaml",
                  staging / "config/providers.local.example.yaml")
        (staging / "install_offline.sh").chmod(0o755)
        (staging / "start_offline.sh").chmod(0o755)
        (staging / "README.md").write_text(
            f"""# TheraSkillAgent offline bundle {version}

This ZIP installs the application without network access or a preinstalled
Python 3.11+ interpreter. It includes a relocatable Python runtime, its Linux
shared libraries, all Python wheels, and the browser assets. It contains no
model weights, knowledge data, or simulation data.

Run `./install_offline.sh` to install under `~/TheraSkillAgent`, then edit the
project's `configs/theraskill/providers.local.yaml` to point at the existing
vLLM LLM and embedding services.

See `docs/deployment_offline.en.md` or `docs/deployment_offline.zh.md`.
The illustrated guides are `docs/user_guide.en.md` and `docs/user_guide.zh.md`.
""",
            encoding="utf-8",
        )

        files = {}
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name not in {"bundle-manifest.json", "SHA256SUMS"}:
                files[path.relative_to(staging).as_posix()] = {
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
        manifest = {
            "schema_version": "theraskill_offline_bundle_v1",
            "bundle_id": f"offline-{version}",
            "platform": "Ubuntu 22.04 x86_64",
            "python": "bundled CPython 3.12.7",
            "project_wheel": project_wheel.name,
            "python_runtime": "runtime/python-3.12.7-linux-x86_64.tar.gz",
            "files": files,
        }
        (staging / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (staging / "SHA256SUMS").open("w", encoding="utf-8") as checksums:
            for name, item in files.items():
                checksums.write(f"{item['sha256']}  {name}\n")

        with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
            for path in sorted(staging.rglob("*")):
                if not path.is_file():
                    continue
                name = path.relative_to(staging).as_posix()
                compression = zipfile.ZIP_STORED if path.suffix in {".zip", ".whl", ".gz"} else zipfile.ZIP_DEFLATED
                archive.write(path, name, compress_type=compression)
    print(f"Created {output} ({output.stat().st_size} bytes)")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="outer ZIP path")
    parser.add_argument("--runtime-archive", type=Path,
                        default=ROOT / "artifacts/_build/theraskill-python-3.12.7-linux-x86_64.tar.gz",
                        help="conda-pack archive with Python 3.12.7 and Linux shared libraries")
    parser.add_argument("--version", default="20261029")
    args = parser.parse_args()
    build_bundle(args.output, args.runtime_archive, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
