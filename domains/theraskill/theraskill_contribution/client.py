"""Internal client contract used by TheraSkill product APIs."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class ContributionClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ContributionClient(Protocol):
    def create_consent(
        self,
        *,
        subject_id: str,
        role: str,
        capture_tier: str,
        purposes: dict[str, bool],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    def capture(
        self,
        *,
        subject_id: str,
        role: str,
        endpoint: str,
        idempotency_key: str,
        payload: dict[str, Any],
        expert_profile_id: str | None = None,
    ) -> dict[str, Any]: ...

    def list_contributions(
        self, *, subject_id: str, role: str, limit: int = 100
    ) -> dict[str, Any]: ...

    def withdraw(
        self, *, contribution_id: str, subject_id: str, role: str
    ) -> dict[str, Any]: ...

    def memory_context(
        self, *, subject_id: str, role: str, include_sensitive: bool, max_chars: int
    ) -> dict[str, Any]: ...

    def propose_memory(self, *, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_memories(self, *, subject_id: str, role: str) -> list[dict[str, Any]]: ...
    def approve_memory(self, *, memory_id: str, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def update_memory(self, *, memory_id: str, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_memory(self, *, memory_id: str, subject_id: str, role: str) -> dict[str, Any]: ...


class HTTPContributionClient:
    def __init__(self, base_url: str, *, api_token: str = "", timeout_seconds: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        path: str,
        *,
        method: str,
        subject_id: str,
        role: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        expert_profile_id: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        headers = {
            "Content-Type": "application/json",
            "X-Contribution-Subject": subject_id,
            "X-Contribution-Role": role,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if expert_profile_id:
            headers["X-Expert-Profile"] = expert_profile_id
        request = urllib.request.Request(
            self.base_url + path,
            data=(json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            raise ContributionClientError(str(detail), status_code=exc.code) from exc
        except Exception as exc:
            raise ContributionClientError(str(exc)) from exc

    def create_consent(self, *, subject_id: str, role: str, capture_tier: str, purposes: dict[str, bool], idempotency_key: str | None = None) -> dict[str, Any]:
        return self._request(
            "/v1/consents",
            method="POST",
            subject_id=subject_id,
            role=role,
            payload={"capture_tier": capture_tier, "purposes": purposes},
            idempotency_key=idempotency_key,
        )

    def capture(self, *, subject_id: str, role: str, endpoint: str, idempotency_key: str, payload: dict[str, Any], expert_profile_id: str | None = None) -> dict[str, Any]:
        return self._request(
            endpoint,
            method="POST",
            subject_id=subject_id,
            role=role,
            payload=payload,
            idempotency_key=idempotency_key,
            expert_profile_id=expert_profile_id,
        )

    def list_contributions(self, *, subject_id: str, role: str, limit: int = 100) -> dict[str, Any]:
        return self._request(
            f"/v1/contributions?limit={limit}",
            method="GET",
            subject_id=subject_id,
            role=role,
        )

    def withdraw(self, *, contribution_id: str, subject_id: str, role: str) -> dict[str, Any]:
        return self._request(
            f"/v1/contributions/{contribution_id}",
            method="DELETE",
            subject_id=subject_id,
            role=role,
        )

    def request(
        self,
        path: str,
        *,
        method: str,
        subject_id: str,
        role: str,
        payload: dict[str, Any] | None = None,
        expert_profile_id: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        return self._request(
            path,
            method=method,
            subject_id=subject_id,
            role=role,
            payload=payload,
            expert_profile_id=expert_profile_id,
        )

    def memory_context(
        self,
        *,
        subject_id: str,
        role: str,
        include_sensitive: bool = False,
        max_chars: int = 3000,
    ) -> dict[str, Any]:
        result = self._request(
            "/v1/memory/context"
            f"?include_sensitive={'true' if include_sensitive else 'false'}&max_chars={max_chars}",
            method="GET",
            subject_id=subject_id,
            role=role,
        )
        return result if isinstance(result, dict) else {}

    def propose_memory(self, *, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "/v1/memory/proposals", method="POST", subject_id=subject_id, role=role,
            payload=payload,
        )
        return result if isinstance(result, dict) else {}

    def list_memories(self, *, subject_id: str, role: str) -> list[dict[str, Any]]:
        result = self._request(
            "/v1/memory", method="GET", subject_id=subject_id, role=role,
        )
        return result if isinstance(result, list) else []

    def approve_memory(self, *, memory_id: str, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            f"/v1/memory/{memory_id}/approve", method="POST", subject_id=subject_id,
            role=role, payload=payload,
        )
        return result if isinstance(result, dict) else {}

    def update_memory(self, *, memory_id: str, subject_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            f"/v1/memory/{memory_id}", method="PATCH", subject_id=subject_id,
            role=role, payload=payload,
        )
        return result if isinstance(result, dict) else {}

    def delete_memory(self, *, memory_id: str, subject_id: str, role: str) -> dict[str, Any]:
        result = self._request(
            f"/v1/memory/{memory_id}", method="DELETE", subject_id=subject_id, role=role,
        )
        return result if isinstance(result, dict) else {}
