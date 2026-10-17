from __future__ import annotations

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildBusinessAssets(build_py):
    """Ship required business resources without host tools, data or secrets."""

    def run(self):
        super().run()
        source = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / "theraskill_agent" / "_resources"
        if destination.exists():
            shutil.rmtree(destination)
        patterns = (
            "configs/**/*.yaml", "prompts/**/*.md",
            "scripts/Simulation/*.py", "scripts/AdvancedRetrieval/*.py",
            "scripts/AgentSkill/*.py", "scripts/_common/*.py",
            "extraction/scripts/**/*.py", "extraction/prompts/**/*.md",
            "extraction/configs/*.yaml", "extraction/examples/*.jsonl",
            "views/theraskill/*/dist/**/*", "examples/knowledge/*", "releases/catalog.json", "schemas/*.json",
        )
        for pattern in patterns:
            for path in sorted(source.glob(pattern)):
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name.startswith("run_hermes_") or path.name == "run_h6_runtime_pilot.py":
                    continue
                target = destination / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)

# All runtime metadata lives in pyproject.toml. Keep this compatibility shim
# for older build frontends, but do not copy the legacy Hermes skill library
# into every TheraSkillAgent installation.
setup(cmdclass={"build_py": BuildBusinessAssets})
