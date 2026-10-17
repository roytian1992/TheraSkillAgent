#!/usr/bin/env python3
"""Shared utilities for TheraSkill model-backed extraction runners."""

from __future__ import annotations

import json
import math
import signal
import ssl
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml
from json_repair import repair_json

from .provider_config import (
    ProviderConfigError,
    resolve_embedding_config,
    resolve_model_config,
)


DEFAULT_MAX_PROMPT_TOKENS = 24000
DEFAULT_PARSE_RETRY_ATTEMPTS = 2
DEFAULT_QUALITY_RETRY_ATTEMPTS = 2
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_JSON_REPAIR_TIMEOUT_SECONDS = 10
DEFAULT_JSON_REPAIR_MAX_CHARS = 100000


class JsonRepairTimeoutError(TimeoutError):
    """Raised when local json_repair gets stuck on pathological model output."""


@contextmanager
def local_timeout(seconds: Optional[int], reason: str) -> Any:
    if (
        seconds is None
        or int(seconds) <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
    ):
        yield
        return

    seconds = int(seconds)
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.setitimer(signal.ITIMER_REAL, seconds)

    def _raise_timeout(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise JsonRepairTimeoutError(reason)

    signal.signal(signal.SIGALRM, _raise_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep OpenAI-compatible POSTs on the configured endpoint.

    Some local/proxy providers intermittently return 301 from HTTP to a broken
    HTTPS route under load. Letting urllib follow that redirect changes POST to
    GET and turns a retryable transport issue into a misleading 404.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def build_no_redirect_opener(ssl_context: Optional[ssl.SSLContext]) -> urllib.request.OpenerDirector:
    handlers: List[Any] = [NoRedirectHandler()]
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    return urllib.request.build_opener(*handlers)


class PromptBudgetExceededError(ValueError):
    """Raised before a model call that would exceed the configured prompt budget."""

    def __init__(self, report: Dict[str, Any]) -> None:
        self.report = report
        super().__init__(
            "prompt_token_budget_exceeded: "
            f"{report.get('estimated_prompt_tokens')} > {report.get('max_prompt_tokens')}"
        )


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def write_jsonl(path: str | Path, records: Iterable[Dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def select_records(
    records: List[Dict[str, Any]],
    limit: Optional[int] = None,
    source_ids: Optional[List[str]] = None,
    dataset_source_keys: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    selected = records
    if dataset_source_keys:
        dataset_source_set = set(dataset_source_keys)
        selected = [
            record
            for record in selected
            if f"{record.get('dataset')}::{record.get('source_id')}" in dataset_source_set
        ]
    if source_ids:
        source_id_set = set(source_ids)
        selected = [record for record in selected if record.get("source_id") in source_id_set]
    if limit is not None:
        selected = selected[:limit]
    return selected


def merged_model_config(extraction_config: Dict[str, Any], local_model_config: Dict[str, Any]) -> Dict[str, Any]:
    local_model = dict(local_model_config.get("model") or {})
    extraction_model = dict((extraction_config.get("extraction") or {}).get("model") or {})
    # Connection details always come from the direct providers.yaml block.
    # Domain YAML may still tune request-time controls without selecting a
    # second hidden model catalog.
    try:
        resolved = resolve_model_config()
    except ProviderConfigError as exc:
        raise ValueError(str(exc)) from exc
    for source in (extraction_model, local_model):
        resolved.update({
            key: value for key, value in source.items()
            if key in {"temperature", "max_output_tokens", "timeout_seconds", "retry_attempts", "response_format_json", "thinking", "reasoning_effort"}
            and value is not None
        })
    return resolved


def apply_model_runtime_overrides(
    model_config: Dict[str, Any],
    *,
    model_name: Optional[str] = None,
    thinking_mode: str = "config",
    thinking_budget_tokens: int = 512,
) -> Dict[str, Any]:
    updated = dict(model_config)
    if model_name:
        updated["name"] = model_name
    if thinking_mode == "enabled":
        updated["thinking"] = {"type": "enabled", "budget_tokens": int(thinking_budget_tokens)}
    elif thinking_mode == "disabled":
        updated["thinking"] = {"type": "disabled"}
    elif thinking_mode != "config":
        raise ValueError(f"unsupported thinking_mode: {thinking_mode}")
    return updated


def merged_embedding_config(extraction_config: Dict[str, Any], local_model_config: Dict[str, Any]) -> Dict[str, Any]:
    retrieval_config = extraction_config.get("retrieval") or {}
    base_embedding = dict(retrieval_config.get("embedding") or {})
    local_embedding = dict(local_model_config.get("embedding") or {})
    base_embedding.update({key: value for key, value in local_embedding.items() if value is not None})
    enabled = bool(base_embedding.get("enabled", False))
    if not enabled:
        return {}
    try:
        resolved = resolve_embedding_config()
    except ProviderConfigError as exc:
        raise ValueError(str(exc)) from exc
    resolved.update({
        key: value for key, value in base_embedding.items()
        if key in {"enabled", "batch_size", "timeout_seconds", "retry_attempts", "dimensions"}
        and value is not None
    })
    return resolved


def parse_retry_attempts(extraction_config: Dict[str, Any], local_model_config: Dict[str, Any]) -> int:
    local_value = (local_model_config.get("model") or {}).get("parse_retry_attempts")
    if local_value is not None:
        return int(local_value)
    value = ((extraction_config.get("extraction") or {}).get("model") or {}).get("parse_retry_attempts")
    if value is None:
        return DEFAULT_PARSE_RETRY_ATTEMPTS
    return int(value)


def quality_retry_attempts(extraction_config: Dict[str, Any], local_model_config: Dict[str, Any]) -> int:
    local_value = (local_model_config.get("model") or {}).get("quality_retry_attempts")
    if local_value is not None:
        return int(local_value)
    value = ((extraction_config.get("extraction") or {}).get("model") or {}).get("quality_retry_attempts")
    if value is None:
        return DEFAULT_QUALITY_RETRY_ATTEMPTS
    return int(value)


def context_max_input_chars(local_model_config: Dict[str, Any]) -> Optional[int]:
    value = (local_model_config.get("context_policy") or {}).get("max_input_chars")
    if value is None:
        return None
    return int(value)


def context_max_prompt_tokens(
    extraction_config: Dict[str, Any],
    local_model_config: Dict[str, Any],
) -> int:
    local_context = local_model_config.get("context_policy") or {}
    extraction_context = (extraction_config.get("extraction") or {}).get("context_policy") or {}
    value = local_context.get("max_prompt_tokens")
    if value is None:
        value = extraction_context.get("max_prompt_tokens")
    if value is None:
        value = DEFAULT_MAX_PROMPT_TOKENS
    return int(value)


def estimate_prompt_tokens(text: str) -> int:
    """Conservative tokenizer-free estimate for Chinese-heavy Qwen prompts."""
    ascii_chars = 0
    non_ascii_chars = 0
    whitespace_chars = 0
    for char in text:
        if char.isspace():
            whitespace_chars += 1
        elif ord(char) < 128:
            ascii_chars += 1
        else:
            non_ascii_chars += 1
    ascii_tokens = (ascii_chars + 3) // 4
    # Chinese and mixed CJK text commonly approaches one token per character.
    return int(ascii_tokens + non_ascii_chars + max(1, whitespace_chars // 8))


def format_user_content(user_payload: Dict[str, Any], extra_user_instruction: Optional[str] = None) -> str:
    user_content = json.dumps(user_payload, ensure_ascii=False)
    if extra_user_instruction:
        user_content = f"{user_content}\n\n{extra_user_instruction}"
    return user_content


def prompt_budget_report(
    system_prompt: str,
    payload: Dict[str, Any],
    max_prompt_tokens: int,
    *,
    extra_user_instruction: Optional[str] = None,
) -> Dict[str, Any]:
    user_payload_text = format_user_content(payload, extra_user_instruction)
    prompt_text = system_prompt + "\n" + user_payload_text
    estimated_tokens = estimate_prompt_tokens(prompt_text)
    return {
        "estimated_prompt_tokens": estimated_tokens,
        "max_prompt_tokens": max_prompt_tokens,
        "over_budget": estimated_tokens > max_prompt_tokens,
        "estimator": "qwen_conservative_v0",
        "system_prompt_chars": len(system_prompt),
        "user_payload_chars": len(user_payload_text),
        "extra_user_instruction_chars": len(extra_user_instruction or ""),
    }


def ensure_prompt_budget(
    system_prompt: str,
    payload: Dict[str, Any],
    max_prompt_tokens: Optional[int],
    *,
    extra_user_instruction: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if max_prompt_tokens is None:
        return None
    report = prompt_budget_report(
        system_prompt,
        payload,
        max_prompt_tokens,
        extra_user_instruction=extra_user_instruction,
    )
    if report["over_budget"]:
        raise PromptBudgetExceededError(report)
    return report


def maybe_too_long(
    payload: Dict[str, Any],
    max_input_chars: Optional[int],
    system_prompt: Optional[str] = None,
    max_prompt_tokens: Optional[int] = None,
) -> bool:
    if max_input_chars is not None and len(json.dumps(payload, ensure_ascii=False)) > max_input_chars:
        return True
    if system_prompt is not None and max_prompt_tokens is not None:
        return bool(prompt_budget_report(system_prompt, payload, max_prompt_tokens)["over_budget"])
    return False


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def parse_json_object(
    text: str,
    *,
    allow_repair: bool = True,
    repair_timeout_seconds: int = DEFAULT_JSON_REPAIR_TIMEOUT_SECONDS,
    repair_max_chars: int = DEFAULT_JSON_REPAIR_MAX_CHARS,
) -> Dict[str, Any]:
    stripped = strip_markdown_fence(text)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
        raw_decode_error: Optional[Exception] = None
        start = stripped.find("{")
        if start >= 0:
            try:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(stripped[start:])
            except Exception as exc:  # noqa: BLE001
                raw_decode_error = exc
        if data is None and allow_repair:
            if repair_max_chars and len(stripped) > int(repair_max_chars):
                if raw_decode_error is not None:
                    raise raw_decode_error
                raise ValueError(f"model output too long for local JSON repair: {len(stripped)} chars")
            try:
                with local_timeout(
                    repair_timeout_seconds,
                    f"json_repair_timeout_after_{repair_timeout_seconds}s",
                ):
                    repaired = repair_json(stripped, return_objects=True)
                data = repaired
            except Exception:
                if raw_decode_error is not None:
                    raise raw_decode_error
                raise
        if data is None:
            raise
    if not isinstance(data, dict):
        raise ValueError("model output JSON is not an object")
    return data


_NATIVE_PROVIDERS = {"agent", "hermes", "hermes-native", "runtime", "host"}


class AgentNativeLLMClient:
    """Resolve the active project provider without importing a host runtime."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        config = config or {}
        resolved = resolve_model_config()
        if str(resolved.get("provider") or "").strip().lower() in _NATIVE_PROVIDERS:
            raise ValueError("active LLM config must specify a concrete provider and base_url, not an agent alias")
        for key in ("temperature", "max_output_tokens", "timeout_seconds", "retry_attempts",
                    "response_format_json", "thinking", "reasoning_effort"):
            if config.get(key) is not None:
                resolved[key] = config[key]
        self._client = OpenAICompatibleClient(resolved)
        self.model = self._client.model
        self.provider = "agent"
        self.temperature = self._client.temperature
        self.max_output_tokens = self._client.max_output_tokens
        self.timeout_seconds = self._client.timeout_seconds

    def chat(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        *,
        force_json_response: Optional[bool] = None,
        extra_user_instruction: Optional[str] = None,
    ) -> str:
        return self._client.chat(
            system_prompt, user_payload,
            force_json_response=force_json_response,
            extra_user_instruction=extra_user_instruction,
        )


class OpenAICompatibleClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        if str(config.get("provider") or "").strip().lower() in _NATIVE_PROVIDERS:
            self._hosted_client = AgentNativeLLMClient(config)
            self.model = self._hosted_client.model
            self.base_url = "agent://active-provider"
            self.api_key = ""
            self.temperature = self._hosted_client.temperature
            self.max_output_tokens = self._hosted_client.max_output_tokens
            self.timeout_seconds = self._hosted_client.timeout_seconds
            self.retry_attempts = 0
            self.response_format_json = True
            self.thinking = None
            self.reasoning_effort = None
            return
        self._hosted_client = None
        self.model = config["name"]
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = str(config.get("api_key") or "")
        self.temperature = float(config.get("temperature", 0.0))
        self.max_output_tokens = config.get("max_output_tokens")
        self.timeout_seconds = int(config.get("timeout_seconds", 120))
        self.retry_attempts = int(config.get("retry_attempts", 2))
        self.response_format_json = bool(config.get("response_format_json", False))
        self.thinking = config.get("thinking")
        self.reasoning_effort = config.get("reasoning_effort")
        self.ssl_context = None if config.get("ssl_verify", True) else ssl._create_unverified_context()
        self.opener = build_no_redirect_opener(self.ssl_context)

    def chat(
        self,
        system_prompt: str,
        user_payload: Dict[str, Any],
        *,
        force_json_response: Optional[bool] = None,
        extra_user_instruction: Optional[str] = None,
    ) -> str:
        if self._hosted_client is not None:
            return self._hosted_client.chat(
                system_prompt,
                user_payload,
                force_json_response=force_json_response,
                extra_user_instruction=extra_user_instruction,
            )
        user_content = format_user_content(user_payload, extra_user_instruction)
        request_payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
        }
        if self.max_output_tokens is not None:
            request_payload["max_tokens"] = int(self.max_output_tokens)
        if self.thinking is not None:
            request_payload["thinking"] = self.thinking
        if self.reasoning_effort:
            request_payload["reasoning_effort"] = self.reasoning_effort
        use_json_response = self.response_format_json if force_json_response is None else force_json_response
        if use_json_response:
            request_payload["response_format"] = {"type": "json_object"}

        data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Optional[str] = None
        for attempt in range(self.retry_attempts + 1):
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                return response_data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {body[:1000]}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.retry_attempts:
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(last_error or "unknown chat completion failure")


class OpenAICompatibleEmbeddingClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.model = config["name"]
        self.dimensions = int(config["dimensions"]) if config.get("dimensions") else None
        endpoint = config.get("endpoint")
        if endpoint:
            self.url = str(endpoint).rstrip("/")
        else:
            self.url = f"{str(config['base_url']).rstrip('/')}/embeddings"
        self.api_key = str(config.get("api_key") or "")
        self.timeout_seconds = int(config.get("timeout_seconds", 120))
        self.retry_attempts = int(config.get("retry_attempts", 2))
        self.batch_size = int(config.get("batch_size") or DEFAULT_EMBEDDING_BATCH_SIZE)
        self.ssl_context = None if config.get("ssl_verify", True) else ssl._create_unverified_context()
        self.opener = build_no_redirect_opener(self.ssl_context)

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        all_embeddings: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            request_payload: Dict[str, Any] = {
                "model": self.model,
                "input": batch,
            }
            if self.dimensions is not None:
                request_payload["dimensions"] = self.dimensions
            data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            response_data = self._post(request)
            items = response_data.get("data")
            if not isinstance(items, list):
                raise RuntimeError("embedding response missing data list")
            items_sorted = sorted(items, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
            batch_embeddings: List[List[float]] = []
            for item in items_sorted:
                if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                    raise RuntimeError("embedding response item missing embedding")
                vector = [float(value) for value in item["embedding"]]
                if not vector:
                    raise RuntimeError("embedding response contains empty vector")
                batch_embeddings.append(vector)
            if len(batch_embeddings) != len(batch):
                raise RuntimeError(f"embedding response count mismatch: {len(batch_embeddings)} != {len(batch)}")
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    def _post(self, request: urllib.request.Request) -> Dict[str, Any]:
        last_error: Optional[str] = None
        for attempt in range(self.retry_attempts + 1):
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                if not isinstance(response_data, dict):
                    raise RuntimeError("embedding response JSON is not an object")
                return response_data
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {body[:1000]}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.retry_attempts:
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(last_error or "unknown embedding failure")


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def chat_json_with_repair(
    client: OpenAICompatibleClient,
    system_prompt: str,
    user_payload: Dict[str, Any],
    *,
    parse_retries: int = DEFAULT_PARSE_RETRY_ATTEMPTS,
    max_prompt_tokens: Optional[int] = None,
    extra_user_instruction: Optional[str] = None,
    force_json_first: bool = False,
) -> Dict[str, Any]:
    last_error: Optional[str] = None
    for attempt in range(parse_retries + 1):
        # Keep the client's configured response_format_json behavior on the
        # first attempt. Explicitly force JSON only when requested by the
        # caller or after a parse failure.
        force_json = True if (force_json_first or attempt > 0) else None
        # Parse recovery is local-first: json_repair is attempted before this
        # retry loop advances. If the model must be called again, repeat the
        # same prompt content instead of adding prior-output/error context,
        # which can push Qwen-class deployments over the 24k prompt budget.
        combined_instruction = extra_user_instruction
        ensure_prompt_budget(
            system_prompt,
            user_payload,
            max_prompt_tokens,
            extra_user_instruction=combined_instruction,
        )
        text = client.chat(
            system_prompt,
            user_payload,
            force_json_response=force_json,
            extra_user_instruction=combined_instruction,
        )
        try:
            return parse_json_object(text, allow_repair=True)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < parse_retries:
                time.sleep(1.0 * (attempt + 1))
    raise ValueError(last_error or "JSON parse failed after repair and retry")


def failure_record(
    record: Dict[str, Any],
    prompt_version: str,
    reason: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    failure = {
        "reason": reason,
        "context_policy": "explicit_skip_too_long" if reason in {"input_too_long", "prompt_token_budget_exceeded"} else "runner_failure",
    }
    if details:
        failure["details"] = details
    return {
        "dataset": record.get("dataset"),
        "source_id": record.get("source_id"),
        "prompt_version": prompt_version,
        "failure": failure,
    }
