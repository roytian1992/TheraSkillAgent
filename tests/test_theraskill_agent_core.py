from __future__ import annotations

import json
from pathlib import Path

import pytest

from theraskill_agent.cli import main
from theraskill_agent.skills import SkillManager
from theraskill_agent.text import chunk_text, extract_sections, normalize_text, redact_secrets


def _write_skill(root: Path, name: str = "demo", body: str | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: %s\ndescription: demo skill\n---\n\n%s\n" % (
            name,
            body or "# 用途\n处理文本。\n\n## 输入\n文本。\n\n## 输出\n结果。\n\n## 步骤\n执行。\n\n## 安全\n不泄露密钥。\n\n## 验证\n检查结果。",
        ),
        encoding="utf-8",
    )
    return directory


def test_text_processing_is_deterministic_and_redacts_secrets():
    assert normalize_text("Ａ\r\n\r\n B  ") == "A\n\nB"
    chunks = chunk_text("第一段内容。\n\n第二段内容。\n\n第三段内容。", max_chars=8, overlap=2)
    assert chunks and all(len(item.text) <= 8 for item in chunks)
    assert chunks[0].index == 0
    assert extract_sections("# A\n甲\n## B\n乙")[1]["title"] == "B"
    assert "sk-abc123456789" not in redact_secrets("key=sk-abc123456789")


def test_skill_manager_lists_user_before_bundled_and_installs(tmp_path: Path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_skill(bundled)
    _write_skill(user, body="# 用户覆盖\n更优版本。")
    manager = SkillManager([bundled], user_root=user)
    records = manager.list_skills()
    assert len(records) == 1 and records[0].source == "user"

    source = _write_skill(tmp_path / "source", "installed")
    installed = manager.install_local(source, name="custom-installed")
    assert installed.name == "custom-installed"
    assert (user / "custom-installed" / "SKILL.md").exists()

    with pytest.raises(ValueError):
        manager.install_local(source, name="../escape")


def test_skill_quality_report_can_add_metadata(tmp_path: Path):
    root = tmp_path / "skills"
    skill = root / "raw"
    skill.mkdir(parents=True)
    path = skill / "SKILL.md"
    path.write_text("# 用途\n先做一件事。\n", encoding="utf-8")
    manager = SkillManager([], user_root=root)
    report = manager.optimize(path)
    assert report.score < 100
    changed = manager.optimize(path, write=True)
    assert "frontmatter" in changed.changed
    assert path.read_text(encoding="utf-8").startswith("---\nname: raw\n")


def test_cli_status_and_sections(capsys, tmp_path: Path):
    assert main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["project"] == "TheraSkillAgent"
    section_file = tmp_path / "doc.md"
    section_file.write_text("# 标题\n正文\n", encoding="utf-8")
    assert main(["text", "sections", str(section_file)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["title"] == "标题"
