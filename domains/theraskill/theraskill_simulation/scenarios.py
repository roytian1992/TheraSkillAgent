"""Scenario registry access for the simulation session service."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class ScenarioNotFoundError(KeyError):
    pass


class ScenarioRegistry:
    def __init__(self, registry_path: str | Path, generated_registry_path: str | Path | None = None) -> None:
        self.registry_path = Path(registry_path).resolve()
        self.generated_registry_path = (
            Path(generated_registry_path).resolve() if generated_registry_path else None
        )
        self._overlay_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._generated_lock = threading.Lock()

    def _generated_rows(self) -> list[dict[str, Any]]:
        if self.generated_registry_path is None or not self.generated_registry_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.generated_registry_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def register_generated(self, row: dict[str, Any]) -> dict[str, Any]:
        if self.generated_registry_path is None:
            raise RuntimeError("generated scenario storage is not configured")
        scenario_id = str(row.get("scenario_id") or "").strip()
        if not scenario_id:
            raise ValueError("generated scenario requires scenario_id")
        self.generated_registry_path.parent.mkdir(parents=True, exist_ok=True)
        with self._generated_lock:
            existing = {str(item.get("scenario_id")): item for item in self._generated_rows()}
            if scenario_id not in existing:
                with self.generated_registry_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                return row
            return existing[scenario_id]

    def update_generated(self, scenario_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        if self.generated_registry_path is None:
            raise RuntimeError("generated scenario storage is not configured")
        with self._generated_lock:
            rows = self._generated_rows()
            for index, existing in enumerate(rows):
                if str(existing.get("scenario_id")) != str(scenario_id):
                    continue
                merged = {**existing, **updates, "scenario_id": existing.get("scenario_id")}
                rows[index] = merged
                self.generated_registry_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.generated_registry_path.with_suffix(
                    self.generated_registry_path.suffix + ".tmp"
                )
                temporary.write_text(
                    "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in rows),
                    encoding="utf-8",
                )
                temporary.replace(self.generated_registry_path)
                return merged
        raise ScenarioNotFoundError(scenario_id)

    def _overlay(self, language: str) -> dict[str, dict[str, Any]]:
        resolved = str(language or "zh")
        if resolved in {"zh", "mixed"}:
            return {}
        if resolved in self._overlay_cache:
            return self._overlay_cache[resolved]
        overlay_path = self.registry_path.parent.parent / "locales" / resolved / "scenarios.pilot.jsonl"
        rows: dict[str, dict[str, Any]] = {}
        if overlay_path.exists():
            with overlay_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    scenario_id = row.get("scenario_id")
                    if scenario_id:
                        rows[str(scenario_id)] = row
        self._overlay_cache[resolved] = rows
        return rows

    def _localized(self, row: dict[str, Any], language: str) -> dict[str, Any]:
        overlay = self._overlay(language).get(str(row.get("scenario_id")))
        if not overlay:
            return row
        return {**row, **{key: value for key, value in overlay.items() if key != "language"}}

    def get(self, scenario_id: str, *, language: str = "zh") -> dict[str, Any]:
        for row in self._generated_rows():
            if row.get("scenario_id") == scenario_id:
                return self._localized(row, language)
        with self.registry_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("scenario_id") == scenario_id:
                    localized = self._localized(row, language)
                    if str(language) == "en" and localized is row:
                        raise ScenarioNotFoundError(f"{scenario_id} (language=en pilot overlay missing)")
                    return localized
        raise ScenarioNotFoundError(scenario_id)

    def list(
        self,
        *,
        family: str | None = None,
        risk_band: str | None = None,
        difficulty_band: str | None = None,
        search: str | None = None,
        split_key: str | None = "formal_split",
        split_value: str | None = "test",
        limit: int = 50,
        language: str = "zh",
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        source_rows = [*self._generated_rows()]
        if self.registry_path.exists():
            with self.registry_path.open("r", encoding="utf-8") as handle:
                source_rows.extend(json.loads(line) for line in handle if line.strip())
        for row in source_rows:
            if str(language) == "en" and str(row.get("scenario_id")) not in self._overlay(language):
                if row.get("generated") is not True:
                    continue
            row = self._localized(row, language)
            if family and row.get("scenario_family") != family:
                continue
            if risk_band and row.get("risk_band") != risk_band:
                continue
            if difficulty_band and row.get("difficulty_band") != difficulty_band:
                continue
            if split_key and split_value is not None:
                approved_generated = (
                    row.get("generated") is True
                    and row.get("review_status") == "approved"
                    and split_key == "formal_split"
                    and split_value == "test"
                )
                if not approved_generated and (row.get("split_labels") or {}).get(split_key) != split_value:
                    continue
            if search:
                haystack = " ".join(
                    [
                        str(row.get("scenario_brief") or ""),
                        str(row.get("case_family") or ""),
                        *[str(item) for item in (row.get("scenario_tags") or [])],
                    ]
                ).lower()
                if search.lower() not in haystack:
                    continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows
