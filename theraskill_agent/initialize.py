"""Create a writable workspace without copying models or knowledge releases."""

from pathlib import Path
import shutil

from .paths import resource_root


def initialize_project(destination: str | Path) -> dict:
    root = Path(destination).expanduser().resolve()
    created = []
    templates = {
        "examples/knowledge/demo.jsonl": "examples/knowledge/demo.jsonl",
        "config.yaml": "configs/project.example.yaml",
        "configs/theraskill/providers.yaml": "configs/theraskill/providers.yaml",
        "configs/theraskill/retrieval.yaml": "configs/theraskill/retrieval.yaml",
    }
    for target, source in templates.items():
        path = root / target
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resource_root() / source, path)
            created.append(target)
    for directory in ("data/theraskill", "skills/local", "state", "logs", "artifacts"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    example = root / ".env.example"
    if not example.exists():
        example.write_text(
            "# Optional credentials for the profiles in configs/theraskill/providers.yaml\n"
            "THERASKILL_LOCAL_API_KEY=\nTHERASKILL_EMBEDDING_API_KEY=\nTHERASKILL_REMOTE_API_KEY=\n",
            encoding="utf-8",
        )
        created.append(".env.example")
    return {"project_root": str(root), "created": created, "environment": {"THERASKILL_AGENT_ROOT": str(root)}}
