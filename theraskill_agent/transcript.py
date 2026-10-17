"""Transcript ingestion, normalization and evidence-preserving analysis.

The module is deliberately deterministic.  It never silently overwrites ASR
text and it does not make clinical judgments.  Hermes or another host may
call the returned review plan to run model-backed skills after the document
has been normalized and sampled.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


TRANSCRIPT_SCHEMA_VERSION = "theraskill_transcript_v1"

_SPEAKER_RE = re.compile(
    r"^\s*(?:\[(?P<bracket>[^\]]{1,80})\]|(?P<label>[^:：\-—]{1,80})\s*[:：\-—])\s*(?P<text>.*)$"
)
_TIME_RE = re.compile(r"^\s*(?:\[(?P<bracket>\d{1,2}:\d{2}(?::\d{2})?)\]|(?P<plain>\d{1,2}:\d{2}(?::\d{2})?))\s*")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_RISK_PATTERNS = (
    ("urgent", ("正在自杀", "马上自杀", "现在就死", "无法保证安全", "immediate danger", "kill myself now")),
    ("high", ("想自杀", "要自杀", "自伤", "割腕", "结束生命", "suicidal", "kill myself", "self harm", "self-harm")),
    ("medium", ("不想活", "活着没意义", "伤害自己", "不如死了", "don't want to live", "no reason to live")),
)
_ROLE_ALIASES = {
    "患者": "patient", "病人": "patient", "来访者": "client", "来访": "client", "client": "client",
    "patient": "patient", "咨询师": "therapist", "治疗师": "therapist", "心理师": "therapist",
    "医师": "clinician", "医生": "clinician", "doctor": "clinician", "therapist": "therapist",
    "counselor": "counselor", "咨询员": "counselor", "system": "system", "系统": "system",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _json_dialogue_text(value: Any) -> str:
    """Convert common message/turn JSON shapes to labelled dialogue text."""

    if isinstance(value, Mapping):
        records = value.get("turns") or value.get("messages") or value.get("dialogue") or value.get("segments")
        if records is None and any(key in value for key in ("text", "content", "transcript")):
            records = [value]
    else:
        records = value
    if not isinstance(records, list):
        raise ValueError("JSON transcript must contain a turns, messages, dialogue or segments array")
    lines: list[str] = []
    for record in records:
        if isinstance(record, str):
            lines.append(f"unknown: {record}")
            continue
        if not isinstance(record, Mapping):
            continue
        speaker = record.get("speaker") or record.get("role") or record.get("author") or record.get("speaker_label") or "unknown"
        content = record.get("text") or record.get("content") or record.get("transcript") or ""
        timestamp = record.get("timestamp") or record.get("start") or record.get("time")
        if not str(content).strip():
            continue
        prefix = f"[{timestamp}] " if timestamp is not None and str(timestamp).strip() else ""
        lines.append(f"{prefix}{speaker}: {content}")
    return "\n".join(lines)


def _subtitle_dialogue_text(text: str) -> str:
    """Convert SRT/VTT cues into turns while preserving cue timestamps."""

    lines = [line.strip() for line in str(text).replace("\r", "").split("\n")]
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.upper() in {"WEBVTT", "NOTE", "STYLE", "REGION"} or line.isdigit():
            index += 1
            continue
        match = re.match(r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?)(?:[,.]\d{1,3})?\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)(?:[,.]\d{1,3})?", line)
        if match:
            timestamp = match.group("start")
            index += 1
            cue: list[str] = []
            while index < len(lines) and lines[index]:
                cue.append(lines[index])
                index += 1
            if cue:
                output.append(f"[{timestamp}] unknown: {' '.join(cue)}")
            continue
        # A malformed cue is retained as an unlabeled line for review.
        output.append(f"unknown: {line}")
        index += 1
    return "\n".join(output)


def detect_language(text: str) -> str:
    cjk = len(_CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk and latin:
        return "mixed"
    if cjk:
        return "zh"
    if latin:
        return "en"
    return "unknown"


def normalize_text(text: str) -> str:
    """Normalize layout while keeping wording and possible ASR errors intact."""

    text = unicodedata.normalize("NFKC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def canonical_role(label: str) -> str:
    clean = re.sub(r"\s+", "", str(label or "")).strip("[]()（）")
    lowered = clean.lower()
    if lowered in _ROLE_ALIASES:
        return _ROLE_ALIASES[lowered]
    return _ROLE_ALIASES.get(clean, "unknown")


def _speaker_id(label: str, seen: dict[str, str]) -> str:
    clean = re.sub(r"\s+", " ", str(label or "")).strip() or "unknown"
    key = clean.lower()
    if key not in seen:
        seen[key] = f"speaker_{len(seen) + 1}"
    return seen[key]


def _timestamp(line: str) -> tuple[str | None, str]:
    match = _TIME_RE.match(line)
    if not match:
        return None, line
    value = match.group("bracket") or match.group("plain")
    return value, line[match.end():]


def _correction_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    # Repeated short tokens are common in ASR transcripts.  They are surfaced
    # for review and never applied automatically.
    for match in re.finditer(r"(?P<token>[\u3400-\u9fffA-Za-z]{1,4})(?P=token){2,}", text, flags=re.IGNORECASE):
        token = match.group("token")
        candidates.append({
            "kind": "repetition",
            "original": match.group(0),
            "suggested": token,
            "start": match.start(),
            "end": match.end(),
            "confidence": 0.55,
            "auto_applied": False,
            "reason": "possible ASR repetition or disfluency",
        })
    for token in ("嗯嗯", "呃呃", "uh uh", "um um"):
        if token.lower() in text.lower():
            candidates.append({
                "kind": "filler",
                "original": token,
                "suggested": "",
                "confidence": 0.35,
                "auto_applied": False,
                "reason": "possible filler; retain when clinically relevant",
            })
    return candidates


@dataclass
class TranscriptTurn:
    turn_id: str
    speaker_id: str
    speaker_label: str
    speaker_role: str
    raw_text: str
    normalized_text: str
    corrected_text: str | None = None
    timestamp: str | None = None
    source_line_start: int = 0
    source_line_end: int = 0
    language: str = "unknown"
    correction_candidates: list[dict[str, Any]] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptDocument:
    document_id: str
    source_path: str
    source_encoding: str
    language: str
    raw_text_sha256: str
    normalization_version: str
    turns: list[TranscriptTurn]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_raw: bool = False, raw_text: str | None = None) -> dict[str, Any]:
        payload = asdict(self)
        payload["turns"] = [turn.to_dict() for turn in self.turns]
        if include_raw and raw_text is not None:
            payload["raw_text"] = raw_text
        return payload


def parse_transcript(text: str, *, source_path: str = "", encoding: str = "utf-8", language: str = "auto", document_id: str | None = None, speaker_role_overrides: Mapping[str, str] | None = None) -> TranscriptDocument:
    raw = str(text)
    normalized_source = normalize_text(raw)
    lines = normalized_source.splitlines() or ([normalized_source] if normalized_source else [])
    seen: dict[str, str] = {}
    overrides = {str(key).strip().lower(): str(value).strip().lower() for key, value in (speaker_role_overrides or {}).items()}
    turns: list[TranscriptTurn] = []
    warnings: list[str] = []
    current: TranscriptTurn | None = None
    unlabeled_lines = 0

    def flush() -> None:
        nonlocal current
        if current is not None and current.normalized_text.strip():
            current.correction_candidates = _correction_candidates(current.normalized_text)
            current.quality_flags = list(dict.fromkeys(current.quality_flags))
            turns.append(current)
        current = None

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        timestamp, without_time = _timestamp(line)
        match = _SPEAKER_RE.match(without_time)
        if match:
            flush()
            label = (match.group("bracket") or match.group("label") or "unknown").strip()
            content = match.group("text").strip()
            role = overrides.get(label.lower(), canonical_role(label))
            # Common aliases such as 患者/Patient should normally refer to the
            # same participant. Unknown labels remain separate so we do not
            # invent identity from weak evidence.
            speaker_id = _speaker_id(role if role != "unknown" else label, seen)
            flags = []
            if role == "unknown":
                flags.append("speaker_role_unresolved")
            if not content:
                flags.append("empty_turn")
            current = TranscriptTurn(
                turn_id=f"turn_{len(turns) + 1:04d}",
                speaker_id=speaker_id,
                speaker_label=label,
                speaker_role=role,
                raw_text=content,
                normalized_text=normalize_text(content),
                timestamp=timestamp,
                source_line_start=index,
                source_line_end=index,
                language=detect_language(content),
                quality_flags=flags,
            )
            continue
        unlabeled_lines += 1
        if current is not None:
            current.raw_text += "\n" + line
            current.normalized_text = normalize_text(current.normalized_text + " " + line)
            current.source_line_end = index
            if timestamp and not current.timestamp:
                current.timestamp = timestamp
        else:
            label = "unknown"
            current = TranscriptTurn(
                turn_id=f"turn_{len(turns) + 1:04d}",
                speaker_id=_speaker_id(label, seen),
                speaker_label=label,
                speaker_role="unknown",
                raw_text=line,
                normalized_text=normalize_text(line),
                timestamp=timestamp,
                source_line_start=index,
                source_line_end=index,
                language=detect_language(line),
                quality_flags=["speaker_label_missing"],
            )
    flush()
    if unlabeled_lines:
        warnings.append(f"{unlabeled_lines} line(s) had no explicit speaker label and may need review")
    if not turns and normalized_source:
        warnings.append("no dialogue turns could be detected")
    detected = detect_language(normalized_source)
    return TranscriptDocument(
        document_id=document_id or f"transcript_{uuid4().hex[:16]}",
        source_path=str(source_path),
        source_encoding=encoding,
        language=detected if language in {"", "auto", None} else str(language),
        raw_text_sha256=_sha256(raw),
        normalization_version=TRANSCRIPT_SCHEMA_VERSION,
        turns=turns,
        warnings=warnings,
        metadata={"turn_count": len(turns), "speaker_count": len(seen)},
    )


def load_transcript(path: str | Path, *, language: str = "auto", speaker_role_overrides: Mapping[str, str] | None = None) -> tuple[TranscriptDocument, str]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"transcript source is not a file: {source}")
    text, encoding = _read_text(source)
    source_format = source.suffix.lower().lstrip(".") or "txt"
    parse_text = text
    if source_format == "json":
        parse_text = _json_dialogue_text(json.loads(text))
    elif source_format == "jsonl":
        records = []
        for line in text.splitlines():
            if line.strip():
                records.append(json.loads(line))
        parse_text = _json_dialogue_text(records)
    elif source_format in {"srt", "vtt"}:
        parse_text = _subtitle_dialogue_text(text)
    document = parse_transcript(parse_text, source_path=str(source), encoding=encoding, language=language, speaker_role_overrides=speaker_role_overrides)
    # Hash and retain the original source representation even when a
    # structured/subtitle adapter creates a canonical parse view.
    document.raw_text_sha256 = _sha256(text)
    document.metadata.update({"source_format": source_format, "parse_view": "canonical_dialogue" if parse_text != text else "source_text"})
    return document, text


def analyze_transcript(document: TranscriptDocument) -> dict[str, Any]:
    """Create a deterministic screening and review plan with evidence links."""

    role_counts: dict[str, int] = {}
    speaker_counts: dict[str, int] = {}
    risk_flags: list[dict[str, Any]] = []
    questions = 0
    empathy_markers = 0
    for turn in document.turns:
        role_counts[turn.speaker_role] = role_counts.get(turn.speaker_role, 0) + 1
        speaker_counts[turn.speaker_id] = speaker_counts.get(turn.speaker_id, 0) + 1
        text = turn.normalized_text.lower()
        questions += text.count("?") + text.count("？")
        empathy_markers += sum(marker in text for marker in ("理解", "听起来", "谢谢你告诉", "that sounds", "i hear you", "thank you for sharing"))
        for level, patterns in _RISK_PATTERNS:
            matched = [pattern for pattern in patterns if pattern.lower() in text]
            if matched:
                risk_flags.append({
                    "level": level,
                    "turn_id": turn.turn_id,
                    "speaker_id": turn.speaker_id,
                    "matched_terms": matched,
                    "evidence": turn.normalized_text,
                    "screening_only": True,
                })
    therapist_turns = sum(role_counts.get(role, 0) for role in ("therapist", "clinician", "counselor"))
    client_turns = sum(role_counts.get(role, 0) for role in ("patient", "client"))
    review_skills = ["transcript-quality-reviewer", "transcript-evaluator", "therapy-response-reviewer"]
    if risk_flags:
        review_skills.insert(0, "risk-triage")
    return {
        "schema_version": "theraskill_transcript_analysis_v1",
        "document_id": document.document_id,
        "source_sha256": document.raw_text_sha256,
        "quality": {
            "turn_count": len(document.turns),
            "speaker_count": len(speaker_counts),
            "role_counts": role_counts,
            "speaker_counts": speaker_counts,
            "unresolved_speaker_roles": sum(turn.speaker_role == "unknown" for turn in document.turns),
            "turns_with_correction_candidates": sum(bool(turn.correction_candidates) for turn in document.turns),
            "warnings": list(document.warnings),
        },
        "conversation": {
            "language": document.language,
            "questions": questions,
            "empathy_marker_count": empathy_markers,
            "therapist_turns": therapist_turns,
            "client_turns": client_turns,
            "turn_balance": {"therapist": therapist_turns, "client": client_turns},
        },
        "risk_screening": {
            "status": "review_required" if risk_flags else "no_lexical_flags_found",
            "flags": risk_flags,
            "disclaimer": "Lexical screening only; not a diagnosis or final clinical risk assessment.",
        },
        "recommended_skills": review_skills,
        "model_review_plan": {
            "required": True,
            "tasks": [
                {"skill": skill, "scope": "turns_with_evidence", "output": "structured_review_with_evidence"}
                for skill in review_skills
            ],
            "instruction": "Model reviewers must cite turn_id and preserve the distinction between observed text, inference and recommendation.",
        },
    }


def build_bundle(
    document: TranscriptDocument,
    analysis: Mapping[str, Any],
    output_dir: str | Path,
    *,
    include_raw: bool = True,
    raw_text: str | None = None,
    reviews: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    transcript_path = destination / "transcript.json"
    turns_path = destination / "turns.jsonl"
    analysis_path = destination / "analysis.json"
    reviews_path = destination / "reviews.json"
    manifest_path = destination / "manifest.json"
    transcript_path.write_text(json.dumps(document.to_dict(include_raw=include_raw, raw_text=raw_text), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    turns_path.write_text("".join(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n" for turn in document.turns), encoding="utf-8")
    analysis_path.write_text(json.dumps(dict(analysis), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "document_id": document.document_id,
        "source_path": document.source_path,
        "source_sha256": document.raw_text_sha256,
        "artifacts": {"transcript": transcript_path.name, "turns": turns_path.name, "analysis": analysis_path.name},
        "warnings": list(document.warnings),
    }
    if reviews is not None:
        reviews_path.write_text(json.dumps(dict(reviews), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["artifacts"]["reviews"] = reviews_path.name
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = {"directory": str(destination), "transcript": str(transcript_path), "turns": str(turns_path), "analysis": str(analysis_path), "manifest": str(manifest_path)}
    if reviews is not None:
        artifacts["reviews"] = str(reviews_path)
    return artifacts
