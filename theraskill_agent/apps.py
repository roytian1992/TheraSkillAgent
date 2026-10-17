"""Safe deep links from TheraSkillAgent chat to registered dashboard applications.

Applications register an identifier and a relative TheraSkillAgent Dashboard path. The
tool never accepts an arbitrary URL, so a model cannot turn it into a generic
browser or localhost navigation primitive. The target application's backend
must still enforce authentication and role checks when the link is opened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlencode



_APP_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_VIEW_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTEXT_VALUE = 256


@dataclass(frozen=True)
class DashboardApp:
    """A dashboard application that can be opened from chat."""

    app_id: str
    label: str
    path: str
    description: str = ""
    allowed_views: tuple[str, ...] = ()
    context_keys: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    icon: str = "AppWindow"

    def __post_init__(self) -> None:
        app_id = self.app_id.strip()
        path = self.path.strip()
        if not _APP_ID_RE.fullmatch(app_id):
            raise ValueError(f"invalid dashboard app id: {self.app_id!r}")
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError(f"dashboard app path must be a relative route: {self.path!r}")
        if any(not _VIEW_RE.fullmatch(value) for value in self.allowed_views):
            raise ValueError(f"invalid dashboard app view for {app_id}")
        if any(not _VIEW_RE.fullmatch(value) for value in self.context_keys):
            raise ValueError(f"invalid dashboard app context key for {app_id}")


_APPS: dict[str, DashboardApp] = {}
_LOCK = RLock()


def register_app(app: DashboardApp) -> None:
    """Register or replace one namespaced dashboard application."""
    with _LOCK:
        existing = _APPS.get(app.app_id)
        if existing is not None and existing != app:
            raise ValueError(f"dashboard app already registered: {app.app_id}")
        _APPS[app.app_id] = app


def list_apps() -> list[DashboardApp]:
    with _LOCK:
        return sorted(_APPS.values(), key=lambda item: item.app_id)


def _clean_context(app: DashboardApp, raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("context must be an object")
    unknown = sorted(set(raw) - set(app.context_keys))
    if unknown:
        raise ValueError(f"unsupported context key(s) for {app.app_id}: {', '.join(unknown)}")
    context: dict[str, str] = {}
    for key in app.context_keys:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"context value must be scalar: {key}")
        text = str(value).strip()
        if not text or len(text) > _MAX_CONTEXT_VALUE:
            raise ValueError(f"context value is empty or too long: {key}")
        context[key] = text
    return context


def open_app(args: dict[str, Any], **_kwargs: Any) -> str:
    """Resolve a registered app to a safe TheraSkillAgent Dashboard deep link."""
    app_id = str(args.get("app_id") or "").strip()
    with _LOCK:
        app = _APPS.get(app_id)
    if app is None:
        return json.dumps(
            {
                "ok": False,
                "error": "unknown_app",
                "message": f"No TheraSkillAgent Dashboard app is registered for {app_id!r}.",
                "available_apps": [item.app_id for item in list_apps()],
            },
            ensure_ascii=False,
        )

    view = str(args.get("view") or "").strip()
    if view and view not in app.allowed_views:
        return json.dumps(
            {
                "ok": False,
                "error": "unsupported_view",
                "message": f"View {view!r} is not available in {app_id}.",
                "allowed_views": list(app.allowed_views),
            },
            ensure_ascii=False,
        )
    try:
        context = _clean_context(app, args.get("context"))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": "invalid_context", "message": str(exc)}, ensure_ascii=False)

    query: dict[str, str] = {}
    if view:
        query["view"] = view
    query.update(context)
    path = app.path
    if query:
        path = f"{path}?{urlencode(query)}"
    return json.dumps(
        {
            "ok": True,
            "type": "theraskill_app_link",
            "app_id": app.app_id,
            "label": app.label,
            "description": app.description,
            "icon": app.icon,
            "path": path,
            "url": path,
            "markdown": f"[{app.label}]({path})",
            "required_roles": list(app.required_roles),
            "context": context,
            "authorization": "Target application must re-check the TheraSkillAgent user session and role.",
        },
        ensure_ascii=False,
    )


APP_LAUNCHER_SCHEMA = {
    "type": "object",
    "properties": {
        "app_id": {
            "type": "string",
            "description": "Registered TheraSkillAgent Dashboard app id, never an arbitrary URL.",
        },
        "view": {
            "type": "string",
            "description": "Optional registered view inside the app.",
        },
        "context": {
            "type": "object",
            "description": "Optional whitelisted context such as session_id or case_id.",
            "additionalProperties": {"type": "string"},
        },
    },
    "required": ["app_id"],
}
