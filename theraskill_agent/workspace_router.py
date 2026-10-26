"""Fast, high precision routing from assistant requests to workspaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Pattern


@dataclass(frozen=True)
class _WorkspaceRule:
    workspace: str
    intent: str
    label_zh: str
    label_en: str
    reason_zh: str
    reason_en: str
    patterns: tuple[Pattern[str], ...]
    skill_hint: str


@dataclass(frozen=True)
class WorkspaceRoute:
    workspace: str
    intent: str
    confidence: float
    handoff_ready: bool
    matched_terms: tuple[str, ...]
    label_zh: str
    label_en: str
    reason_zh: str
    reason_en: str
    skill_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["matched_terms"] = list(self.matched_terms)
        value["href"] = f"/?workspace={self.workspace}"
        return value


def _pattern(*values: str) -> tuple[Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


_RULES = (
    _WorkspaceRule("learning", "course_design", "课程与学习", "Courses & learning", "课程设计、学习目标和练习需要在课程工作区完成。", "Course design, learning objectives and practice belong in the learning workspace.", _pattern(r"课程(?:设计|草案|生成|安排)", r"(?:设计|创建|生成).{0,20}课程", r"学习(?:目标|进展)", r"微练习", r"课程\s*(?:案例|内容)", r"course", r"curriculum", r"learning objectives?"), "course-agent"),
    _WorkspaceRule("transcript", "transcript_analysis", "对话分析", "Dialogue analysis", "对话整理、规范化和多技能分析需要保留原文证据与逐轮结果。", "Dialogue normalization and multi-skill analysis need source evidence and turn-level results.", _pattern(r"对话(?:分析|整理|规范化|评估)", r"(?:患者.{0,8}医师|医师.{0,8}患者).{0,8}(?:对话|转录)", r"语音转录", r"transcript", r"dialogue analysis"), "transcript-pipeline"),
    _WorkspaceRule("extraction", "knowledge_extraction", "知识抽取", "Knowledge extraction", "知识抽取需要先确定 schema、样例和证据要求，再运行候选治理流程。", "Knowledge extraction needs a schema, examples and evidence rules before candidate governance.", _pattern(r"知识抽取", r"抽取知识", r"提取知识", r"设计\s*(?:schema|字段)", r"schema\s*(?:设计|抽取)", r"knowledge extraction"), "theraskill_extraction"),
    _WorkspaceRule("simulation", "simulation_design", "情景与模拟患者", "Simulation & patients", "模拟患者和训练情景需要分阶段生成、检查并保存版本。", "Simulated patients and training scenarios need staged generation, checks and versioning.", _pattern(r"模拟患者", r"生成患者", r"患者人设", r"情景(?:模拟|生成|设计)", r"训练情景", r"simulated patient", r"scenario generation"), "patient-simulation"),
    _WorkspaceRule("knowledge", "knowledge_management", "知识与索引", "Knowledge & indexes", "知识编辑、索引重建和 embedding 版本管理需要知识工作区的修订保护。", "Knowledge editing, index rebuilds and embedding versions use the revision guarded knowledge workspace.", _pattern(r"编辑知识", r"知识(?:条目|索引)", r"重建(?:索引|embedding)", r"重新索引", r"知识库\s*(?:管理|维护)", r"knowledge index", r"re-?index"), "knowledge-curation"),
    _WorkspaceRule("evaluation", "behavioral_evaluation", "效果评估", "Behavioral evaluation", "模型回应、技能和索引的效果评估需要固定案例、比较基线和门禁记录。", "Model, skill and index evaluation needs fixed cases, baselines and gate records.", _pattern(r"模型(?:评估|评价)", r"回应(?:评分|评估)", r"回归(?:测试|评估)", r"llm\s*(?:as\s*)?judge", r"behavioral evaluation", r"效果回归"), "behavior-evaluation"),
    _WorkspaceRule("settings", "provider_configuration", "模型与连接", "Models & connections", "模型、API 地址和 embedding 配置可以在设置工作区分别测试并保存。", "Model, API and embedding settings can be tested and saved separately in settings.", _pattern(r"模型配置", r"配置\s*(?:llm|embedding)", r"(?:llm|embedding).{0,12}(?:地址|密钥|api)", r"provider settings?", r"model connection"), "provider-settings"),
    _WorkspaceRule("tasks", "task_history", "任务与历史", "Tasks & history", "任务中心用于查看执行状态、补充输入、恢复或重试已有任务。", "Tasks & history shows execution state and lets you provide input, resume or retry a run.", _pattern(r"任务(?:中心|历史|记录)", r"恢复任务", r"重试任务", r"task history", r"resume task"), "task-runtime"),
)


def route_workspace(text: str, *, language: str = "zh") -> WorkspaceRoute:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return WorkspaceRoute("support", "support", 0.0, False, (), "助手", "Assistant", "", "")
    candidates: list[tuple[int, _WorkspaceRule, list[str]]] = []
    for rule in _RULES:
        matched = [pattern.pattern for pattern in rule.patterns if pattern.search(normalized)]
        if matched:
            candidates.append((len(matched), rule, matched))
    if not candidates:
        return WorkspaceRoute("support", "support", 0.0, False, (), "助手", "Assistant", "", "")
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, rule, matched = candidates[0]
    tied = len(candidates) > 1 and candidates[1][0] == score
    confidence = 0.92 if score >= 2 else 0.86
    ready = not tied
    return WorkspaceRoute(
        workspace=rule.workspace if ready else "support",
        intent=rule.intent if ready else "support",
        confidence=confidence if ready else 0.0,
        handoff_ready=ready,
        matched_terms=tuple(matched),
        label_zh=rule.label_zh if ready else "助手",
        label_en=rule.label_en if ready else "Assistant",
        reason_zh=rule.reason_zh if ready else "继续在助手中对话。",
        reason_en=rule.reason_en if ready else "Continue in the Assistant.",
        skill_hint=rule.skill_hint if ready else None,
    )
