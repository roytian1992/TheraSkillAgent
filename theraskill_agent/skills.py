"""Skill discovery, safe local installation and quality checks."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .paths import project_root

try:
    import yaml
except Exception:  # pragma: no cover - the project declares PyYAML
    yaml = None


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)\s*$")
_FRONTMATTER_MARKER = "---"


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: str
    source: str
    headings: tuple[str, ...] = ()
    line_count: int = 0
    version: str = "0.1.0"
    capabilities: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    checksum: str = ""
    bundle_checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["headings"] = list(self.headings)
        result["capabilities"] = list(self.capabilities)
        result["dependencies"] = list(self.dependencies)
        return result


@dataclass(frozen=True)
class SkillQualityReport:
    name: str
    path: str
    score: int
    issues: tuple[dict[str, str], ...] = ()
    changed: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["issues"] = list(self.issues)
        result["changed"] = list(self.changed)
        return result


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _frontmatter(text: str) -> tuple[dict[str, Any], int, int]:
    """Return frontmatter mapping and its line boundaries."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_MARKER:
        return {}, -1, -1
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_MARKER:
            raw = "\n".join(lines[1:index])
            if yaml is None:
                return {}, 0, index
            parsed = yaml.safe_load(raw) or {}
            return (dict(parsed) if isinstance(parsed, Mapping) else {}), 0, index
    return {}, -1, -1


def _heading_values(text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _HEADING_RE.finditer(text))


def _infer_description(text: str, name: str) -> str:
    metadata, _, end = _frontmatter(text)
    if metadata.get("description"):
        return str(metadata["description"]).strip()
    body = text.splitlines()[end + 1 :] if end >= 0 else text.splitlines()
    for line in body:
        cleaned = line.strip().lstrip("#").strip()
        if cleaned and not cleaned.startswith("<!--"):
            return cleaned[:240]
    return f"TheraSkillAgent skill: {name}"


def _skill_metadata(skill_file: Path, *, source: str) -> SkillRecord:
    text = skill_file.read_text(encoding="utf-8")
    metadata, _, _ = _frontmatter(text)
    directory_name = skill_file.parent.name
    name = str(metadata.get("name") or directory_name).strip()
    description = str(metadata.get("description") or _infer_description(text, name)).strip()
    capabilities = metadata.get("capabilities") or metadata.get("triggers") or []
    dependencies = metadata.get("dependencies") or []
    if isinstance(capabilities, str):
        capabilities = [capabilities]
    if isinstance(dependencies, str):
        dependencies = [dependencies]
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
    bundle = hashlib.sha256()
    for entry in sorted(skill_file.parent.rglob('*')):
        if entry.is_file() and not entry.is_symlink() and not any(p in {'.git', '__pycache__'} for p in entry.relative_to(skill_file.parent).parts):
            bundle.update(entry.relative_to(skill_file.parent).as_posix().encode('utf-8') + b'\0')
            bundle.update(hashlib.sha256(entry.read_bytes()).digest())
    return SkillRecord(
        name=name,
        description=description,
        path=str(skill_file.parent),
        source=source,
        headings=_heading_values(text),
        line_count=len(text.splitlines()),
        version=str(metadata.get("version") or "0.1.0"),
        capabilities=tuple(str(item) for item in capabilities if str(item).strip()),
        dependencies=tuple(str(item) for item in dependencies if str(item).strip()),
        checksum=checksum,
        bundle_checksum=bundle.hexdigest(),
    )


def _set_frontmatter_name(text: str, name: str) -> str:
    """Set the installed skill name while preserving the rest of its text."""

    metadata, start, end = _frontmatter(text)
    if start < 0:
        return f"---\nname: {name}\n---\n\n{text.lstrip()}"
    lines = text.splitlines()
    name_line = re.compile(r"^\s*name\s*:")
    for index in range(1, end):
        if name_line.match(lines[index]):
            lines[index] = f"name: {name}"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    lines[end:end] = [f"name: {name}"]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def discover_skill_file(path: str | Path) -> Path:
    """Resolve a skill directory or SKILL.md path, rejecting ambiguous files."""

    candidate = Path(path).expanduser()
    if candidate.is_file() and candidate.name == "SKILL.md":
        return candidate.resolve()
    if candidate.is_dir():
        skill_file = candidate / "SKILL.md"
        if skill_file.is_file():
            return skill_file.resolve()
    raise FileNotFoundError(f"skill must be a directory or SKILL.md: {path}")


class SkillManager:
    """Manage bundled and user-installed skills without network side effects."""

    def __init__(self, roots: Iterable[str | Path] | None = None, *, user_root: str | Path | None = None) -> None:
        repo = _repo_root()
        default_roots = (
            repo / "plugins" / "theraskill" / "skills",
        )
        self.user_root = Path(user_root or os.environ.get("THERASKILL_AGENT_SKILLS_HOME", str(project_root() / "skills" / "local"))).expanduser()
        if not self.user_root.is_absolute():
            self.user_root = project_root() / self.user_root
        self.roots = tuple(Path(root).expanduser() for root in (roots or default_roots))
        self.lock_path = self.user_root / "theraskill.skills.lock.json"
        self.backup_root = self.user_root / ".backups"

    def _candidate_records(self) -> Iterable[SkillRecord]:
        # User skills win over bundled roots.  The first occurrence is retained.
        seen: set[str] = set()
        for root, source in ((self.user_root, "user"), *[(p, "bundled") for p in self.roots]):
            if not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                try:
                    record = _skill_metadata(skill_file, source=source)
                except (OSError, UnicodeError, ValueError):
                    continue
                if record.name in seen:
                    continue
                seen.add(record.name)
                yield record

    def list_skills(self) -> list[SkillRecord]:
        return list(self._candidate_records())

    def resolve(self, target: str | Path) -> Path:
        candidate = Path(str(target)).expanduser()
        if candidate.exists():
            return discover_skill_file(candidate)
        requested = str(target).strip()
        for record in self.list_skills():
            if record.name == requested or Path(record.path).name == requested:
                return Path(record.path) / "SKILL.md"
        raise FileNotFoundError(f"skill not found: {target}")

    def inspect(self, target: str | Path) -> SkillRecord:
        path = self.resolve(target)
        source = "user" if self.user_root in path.parents else "bundled"
        return _skill_metadata(path, source=source)

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name).strip()
        if not _NAME_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError(f"invalid skill name: {name!r}")
        return value

    def install_local(self, source: str | Path, *, name: str | None = None, overwrite: bool = False, report_id: str | None = None) -> SkillRecord:
        skill_file = discover_skill_file(source)
        source_dir = skill_file.parent
        source_record = _skill_metadata(skill_file, source="local")
        target_name = self._validate_name(name or source_record.name)
        if any(path.is_symlink() for path in source_dir.rglob("*")):
            raise ValueError("skill installation does not accept symbolic links")

        from .skills_guard import scan_skill, should_allow_install

        scan = scan_skill(source_dir, source="community")
        allowed, reason = should_allow_install(scan)
        if not allowed:
            raise ValueError(f"skill security scan blocked installation: {reason}")

        self.user_root.mkdir(parents=True, exist_ok=True)
        destination = self.user_root / target_name
        if source_dir.resolve() == destination.resolve():
            raise ValueError("Use a separate candidate directory for skill installation")
        previous = next((record for record in self.list_skills() if record.name == target_name), None)
        if destination.exists() or previous:
            if not overwrite:
                raise FileExistsError(f"skill already installed: {destination}")
            if not report_id:
                raise ValueError('Replacing a skill requires a reviewed behavior comparison report')
            if target_name != source_record.name:
                raise ValueError('Set the candidate skill name before evaluation when replacing a skill')
            from .behavior_eval import verify_gate
            report = verify_gate(report_id, {})
            if source_record.bundle_checksum not in report['target']['skill_checksums'].values():
                raise ValueError('Report did not evaluate this skill bundle')
            self.backup_root.mkdir(parents=True, exist_ok=True)
            prior_dir = destination if destination.exists() else Path(previous.path)
            backup = self.backup_root / f"{target_name}.{_skill_metadata(prior_dir / 'SKILL.md', source='user').bundle_checksum[:12]}"
            if not backup.exists():
                shutil.copytree(prior_dir, backup)
            if destination.exists():shutil.rmtree(destination)
        shutil.copytree(source_dir, destination)
        if target_name != source_record.name:
            installed_path = destination / "SKILL.md"
            installed_path.write_text(
                _set_frontmatter_name(installed_path.read_text(encoding="utf-8"), target_name),
                encoding="utf-8",
            )
        installed = _skill_metadata(destination / "SKILL.md", source="user")
        self._update_lock(installed, source=str(source_dir))
        return installed

    def _update_lock(self, record: SkillRecord, *, source: str) -> None:
        payload: dict[str, Any] = {"schema_version": "theraskill_skill_lock_v1", "skills": {}}
        if self.lock_path.exists():
            try:
                loaded = json.loads(self.lock_path.read_text(encoding="utf-8"))
                if isinstance(loaded, Mapping):
                    payload.update({"schema_version": loaded.get("schema_version", payload["schema_version"]), "skills": dict(loaded.get("skills") or {})})
            except (OSError, json.JSONDecodeError):
                pass
        payload["skills"][record.name] = {"version": record.version, "checksum": record.checksum, "bundle_checksum": record.bundle_checksum, "source": source, "path": record.path, "dependencies": list(record.dependencies)}
        self.user_root.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def lock_snapshot(self) -> dict[str, Any]:
        if not self.lock_path.exists():
            return {"schema_version": "theraskill_skill_lock_v1", "skills": {}}
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid skill lock: {exc}") from exc

    def rollback(self, name: str) -> SkillRecord:
        target = self.resolve(name)
        destination = target.parent
        backups = sorted(self.backup_root.glob(f"{destination.name}.*"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not backups:
            raise FileNotFoundError(f"no rollback backup for skill: {name}")
        backup = backups[0]
        shutil.rmtree(destination)
        shutil.copytree(backup, destination)
        record = _skill_metadata(destination / "SKILL.md", source="user")
        self._update_lock(record, source=str(backup))
        return record

    def optimize(self, target: str | Path, *, write: bool = False) -> SkillQualityReport:
        skill_file = self.resolve(target)
        text = skill_file.read_text(encoding="utf-8")
        metadata, start, end = _frontmatter(text)
        name = str(metadata.get("name") or skill_file.parent.name).strip()
        headings = [value.casefold() for value in _heading_values(text)]
        issues: list[dict[str, str]] = []

        def issue(code: str, message: str, severity: str = "medium") -> None:
            issues.append({"code": code, "message": message, "severity": severity})

        if start < 0:
            issue("missing_frontmatter", "缺少可被工具读取的 YAML frontmatter", "low")
        if not metadata.get("name"):
            issue("missing_name", "缺少 name 元数据", "low")
        if not metadata.get("description"):
            issue("missing_description", "缺少 description 元数据", "medium")
        heading_text = " ".join(headings)
        groups = {
            "purpose": ("用途", "目标", "purpose", "goal", "overview"),
            "input": ("输入", "input", "触发", "trigger"),
            "output": ("输出", "output", "产出", "结果"),
            "workflow": ("步骤", "流程", "workflow", "process", "执行"),
            "safety": ("安全", "边界", "限制", "safety", "guardrail", "风险"),
            "validation": ("验证", "检查", "质量", "validation", "evaluation", "验收"),
        }
        for code, aliases in groups.items():
            if not any(alias in heading_text for alias in aliases):
                issue(f"missing_{code}", f"建议增加 {code} 章节", "medium" if code in {"safety", "workflow"} else "low")
        if len(set(headings)) != len(headings):
            issue("duplicate_headings", "存在重复章节标题，建议合并或区分", "low")
        long_lines = sum(1 for line in text.splitlines() if len(line) > 240)
        if long_lines:
            issue("long_lines", f"有 {long_lines} 行超过 240 字符，影响阅读和审计", "low")
        placeholders = re.findall(r"\b(?:TODO|TBD|FIXME)\b", text, flags=re.IGNORECASE)
        if placeholders:
            issue("placeholders", f"仍有 {len(placeholders)} 个待补充占位符", "medium")

        penalty = sum({"low": 4, "medium": 8, "high": 18}.get(item["severity"], 8) for item in issues)
        score = max(0, 100 - penalty)
        changed: list[str] = []
        if write and (start < 0 or not metadata.get("name") or not metadata.get("description")):
            generated_description = _infer_description(text, name)
            if start < 0:
                text = f"---\nname: {name}\ndescription: {json.dumps(generated_description, ensure_ascii=False)}\n---\n\n{text.lstrip()}"
            else:
                lines = text.splitlines()
                insert_at = end
                additions: list[str] = []
                if not metadata.get("name"):
                    additions.append(f"name: {name}")
                if not metadata.get("description"):
                    additions.append(f"description: {json.dumps(generated_description, ensure_ascii=False)}")
                lines[insert_at:insert_at] = additions
                text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
            skill_file.write_text(text, encoding="utf-8")
            changed.append("frontmatter")
        return SkillQualityReport(name=name, path=str(skill_file.parent), score=score, issues=tuple(issues), changed=tuple(changed))
