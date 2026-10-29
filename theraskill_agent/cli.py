"""Command-line entry point for local TheraSkillAgent development."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .skills import SkillManager
from .contracts import AgentTask
from .runtime import AgentRuntime
from .paths import project_root, data_root
from .source_adapters import inspect_source, sample_source
from .migration import migrate_data
from .text import chunk_text, extract_sections, normalize_text, redact_secrets


def _manager_from_config(path: str | None) -> SkillManager:
    if not path:
        return SkillManager()
    try:
        import yaml

        config_path = Path(path).expanduser()
        if not config_path.is_absolute():
            config_path = project_root() / config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError(f"cannot read config: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config must contain a mapping")
    root = project_root()
    skill_config = raw.get("skills") if isinstance(raw.get("skills"), dict) else {}
    configured_roots = skill_config.get("roots") or []
    roots = [Path(str(item)).expanduser() for item in configured_roots]
    roots = [item if item.is_absolute() else root / item for item in roots]
    user_root = skill_config.get("user_root")
    return SkillManager(roots or None, user_root=user_root)


def _json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _status(manager: SkillManager) -> dict[str, Any]:
    providers: dict[str, Any] = {}
    try:
        from .provider_smoke import list_providers

        providers = list_providers()
    except Exception as exc:
        providers = {"error": redact_secrets(str(exc))}
    return {
        "project": "TheraSkillAgent",
        "version": __version__,
        "project_root": str(project_root()),
        "data_root": str(data_root()),
        "skill_roots": [str(manager.user_root), *(str(root) for root in manager.roots)],
        "skill_count": len(manager.list_skills()),
        "providers": providers,
        "network_access": "disabled by default",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="theraskill-agent", description="TheraSkillAgent local skill and text toolkit")
    parser.add_argument("--project", metavar="DIRECTORY", help="use this writable project directory")
    parser.add_argument("--config", help="local YAML configuration (skill roots and user directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    from .task_cli import add_parser as add_task_parser
    add_task_parser(sub)

    status = sub.add_parser("status", help="show local project, skill and provider metadata")
    status.add_argument("--json", action="store_true", help="kept for consistent machine-readable output")

    ui = sub.add_parser("ui", help="serve the built workspaces without Node.js")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=5176)
    ui.add_argument("--start-services", action="store_true", help=argparse.SUPPRESS)

    skill = sub.add_parser("skill", help="discover, install and improve skills")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    list_cmd = skill_sub.add_parser("list", help="list available skills")
    list_cmd.add_argument("--json", action="store_true")
    inspect = skill_sub.add_parser("inspect", help="inspect one skill")
    inspect.add_argument("target")
    inspect.add_argument("--json", action="store_true")
    install = skill_sub.add_parser("install", help="install a local skill into the user skill root")
    install.add_argument("source")
    install.add_argument("--name")
    install.add_argument("--overwrite", action="store_true")
    install.add_argument("--behavior-report", help="reviewed behavior comparison report ID required for replacement")
    optimize = skill_sub.add_parser("optimize", help="score a skill and optionally add missing metadata")
    optimize.add_argument("target")
    optimize.add_argument("--write", action="store_true", help="write generated name/description frontmatter")
    skill_sub.add_parser("lock", help="show the local skill lock snapshot")
    rollback = skill_sub.add_parser("rollback", help="restore the latest backup of a user skill")
    rollback.add_argument("target")
    readiness = skill_sub.add_parser('check', help='check tools and dependencies before running a skill')
    readiness.add_argument('target')

    text = sub.add_parser("text", help="deterministic text processing")
    text_sub = text.add_subparsers(dest="text_command", required=True)
    normalize = text_sub.add_parser("normalize")
    normalize.add_argument("path", nargs="?", default="-")
    normalize.add_argument("--flat", action="store_true", help="collapse all whitespace")
    chunk = text_sub.add_parser("chunk")
    chunk.add_argument("path")
    chunk.add_argument("--max-chars", type=int, default=4000)
    chunk.add_argument("--overlap", type=int, default=200)
    sections = text_sub.add_parser("sections")
    sections.add_argument("path")

    run = sub.add_parser("run", help="execute a task and record its outcome locally")
    run.add_argument("task", choices=["text.normalize", "text.chunk", "text.sections", "skill.inspect", "skill.optimize", "source.inspect", "source.sample", "extraction.plan", "transcript.inspect", "transcript.analyze", "transcript.review", "transcript.package"])
    run.add_argument("path", nargs="?")
    run.add_argument("--text")
    run.add_argument("--max-chars", type=int, default=4000)
    run.add_argument("--overlap", type=int, default=200)
    run.add_argument("--target")
    run.add_argument("--write", action="store_true")
    run.add_argument("--goal", help="goal for extraction.plan")
    run.add_argument("--limit", type=int, default=5, help="sample size for source.sample")
    run.add_argument("--output-dir", help="output directory for transcript.package")
    run.add_argument("--exclude-raw", action="store_true", help="do not copy raw transcript text into a transcript bundle")
    run.add_argument("--mode", choices=["plan", "model"], default="plan", help="review mode for transcript.review")
    run.add_argument("--skill", action="append", dest="skills", help="skill to run for transcript.review; repeatable")
    run.add_argument("--max-turns", type=int, help="cap transcript turns sent to model review")
    run.add_argument("--output-language", choices=["zh", "en"])

    source = sub.add_parser("source", help="inspect and sample extraction inputs")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_inspect = source_sub.add_parser("inspect")
    source_inspect.add_argument("path")
    source_sample = source_sub.add_parser("sample")
    source_sample.add_argument("path")
    source_sample.add_argument("--limit", type=int, default=5)

    project = sub.add_parser("project", help="project checks and data migration")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_sub.add_parser("doctor")
    init = project_sub.add_parser("init", help="create a workspace without overwriting existing settings")
    init.add_argument("path", nargs="?", default=str(project_root()))
    migrate = project_sub.add_parser("migrate-data")
    migrate.add_argument("source", nargs="?")
    migrate.add_argument("--plan", action="store_true")
    migrate.add_argument("--include-releases", action="store_true")
    migrate.add_argument("--max-bytes", type=int)

    evaluation = sub.add_parser("eval", help="run deterministic regression cases")
    evaluation_sub = evaluation.add_subparsers(dest="eval_command", required=True)
    eval_run = evaluation_sub.add_parser("run")
    eval_run.add_argument("cases")
    eval_run.add_argument("--outputs", required=True, help="JSON mapping of case_id to observed output")
    eval_run.add_argument("--output")

    transcript = sub.add_parser("transcript", help="ingest, normalize and analyze dialogue transcripts")
    transcript_sub = transcript.add_subparsers(dest="transcript_command", required=True)
    for name in ("inspect", "analyze", "review", "package"):
        command = transcript_sub.add_parser(name)
        command.add_argument("path")
        command.add_argument("--language", default="auto", choices=["auto", "zh", "en", "mixed"])
        command.add_argument("--output", help="write JSON result to this path")
        if name == "package":
            command.add_argument("--output-dir", required=True)
            command.add_argument("--no-raw", action="store_true", help="do not copy raw transcript text into the bundle")
        if name == "review":
            command.add_argument("--mode", choices=["plan", "model"], default="plan")
            command.add_argument("--skill", action="append", dest="skills")
            command.add_argument("--max-turns", type=int)
            command.add_argument("--output-language", choices=["zh", "en"])

    service = sub.add_parser("service", help="manage project-local domain services")
    service_sub = service.add_subparsers(dest="service_command", required=True)
    for name in ("status", "start", "stop"):
        command = service_sub.add_parser(name)
        command.add_argument("--service", action="append", choices=["simulation", "support", "expert", "contribution", "extraction", "transcript"])

    provider = sub.add_parser("provider", help="inspect the configured LLM and embedding services")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_sub.add_parser("list", help="show direct provider configuration without exposing credentials")
    check = provider_sub.add_parser("check", help="check configuration, optionally making one live request")
    check.add_argument("--kind", choices=["llm", "embedding"], default="llm")
    check.add_argument("--live", action="store_true", help="make one bounded network request")
    from .resource_cli import add_commands
    add_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.project:
        import os
        os.environ["THERASKILL_AGENT_ROOT"] = str(Path(args.project).expanduser().resolve())
    manager = _manager_from_config(args.config)
    try:
        if args.command in {"knowledge", "index", "simulation"}:
            from .resource_cli import run_command
            _json(run_command(args))
            return 0
        if args.command == "task":
            from .task_cli import run as run_interactive_task
            _json(run_interactive_task(args))
            return 0
        if args.command == "ui":
            import uvicorn
            from .webapp import create_app

            app = create_app()
            uvicorn.run(app, host=args.host, port=args.port)
            return 0
        if args.command == "status":
            _json(_status(manager))
            return 0
        if args.command == "skill":
            if args.skill_command == "list":
                data = [record.to_dict() for record in manager.list_skills()]
                _json(data)
            elif args.skill_command == "inspect":
                _json(manager.inspect(args.target).to_dict())
            elif args.skill_command == "install":
                _json(manager.install_local(args.source, name=args.name, overwrite=args.overwrite, report_id=args.behavior_report).to_dict())
            elif args.skill_command == "optimize":
                _json(manager.optimize(args.target, write=args.write).to_dict())
            elif args.skill_command == "lock":
                _json(manager.lock_snapshot())
            elif args.skill_command == "rollback":
                _json(manager.rollback(args.target).to_dict())
            elif args.skill_command == 'check':
                from .skill_readiness import check_skill
                _json(check_skill(args.target, manager=manager))
            return 0
        if args.command == "text":
            value = _read_input(args.path)
            if args.text_command == "normalize":
                print(normalize_text(value, preserve_newlines=not args.flat))
            elif args.text_command == "chunk":
                _json([chunk.to_dict() for chunk in chunk_text(value, max_chars=args.max_chars, overlap=args.overlap)])
            elif args.text_command == "sections":
                _json(extract_sections(value))
            return 0
        if args.command == "run":
            task = AgentTask(kind=args.task, text=args.goal or args.text, input_path=args.path, parameters={"max_chars": args.max_chars, "overlap": args.overlap, "target": args.target, "write": args.write, "limit": args.limit, "output_dir": args.output_dir, "exclude_raw": args.exclude_raw, "mode": args.mode, "skills": args.skills, "max_turns": args.max_turns, "output_language": args.output_language})
            result = AgentRuntime(manager=manager).run(task)
            _json(result.to_dict())
            return 0 if result.status == "succeeded" else 2
        if args.command == "source":
            _json(inspect_source(args.path).to_dict() if args.source_command == "inspect" else [item.to_dict() for item in sample_source(args.path, args.limit)])
            return 0
        if args.command == "project":
            if args.project_command == "init":
                from .initialize import initialize_project
                _json(initialize_project(args.path))
                return 0
            if args.project_command == "doctor":
                current_manifest = data_root() / "current" / "manifest.json"
                checks = {
                    "root": project_root().is_dir(),
                    "config": (project_root() / "config.yaml").is_file(),
                    "data": data_root().is_dir(),
                    "skills": bool(manager.list_skills()),
                    "knowledge_release": current_manifest.is_file() or (data_root() / "active-index.json").is_file(),
                }
                _json({"project": "TheraSkillAgent", "checks": checks, "ok": all(checks.values())})
                return 0 if all(checks.values()) else 2
            _json(migrate_data(args.source, plan=args.plan, include_releases=args.include_releases, max_bytes=args.max_bytes).to_dict())
            return 0
        if args.command == "eval":
            from .eval import EvalRunner, load_cases
            cases = load_cases(args.cases)
            outputs = json.loads(Path(args.outputs).read_text(encoding="utf-8"))
            report = EvalRunner().run(cases, outputs, metadata={"cases_path": str(Path(args.cases).resolve()), "outputs_path": str(Path(args.outputs).resolve())})
            if args.output:
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _json(report)
            return 0 if report["counts"]["failed"] == 0 else 2
        if args.command == "transcript":
            from .transcript import analyze_transcript, build_bundle, load_transcript

            document, raw_text = load_transcript(args.path, language=args.language)
            if args.transcript_command == "inspect":
                payload = {
                    "document": document.to_dict(include_raw=False),
                    "sample_turns": [turn.to_dict() for turn in document.turns[:5]],
                    "next_steps": ["review_speaker_roles", "analyze", "package"],
                }
            elif args.transcript_command == "review":
                from .reviews import build_review_plan, run_skill_reviews

                analysis = analyze_transcript(document)
                if args.mode == "model":
                    review = run_skill_reviews(document, analysis, skills=args.skills, max_turns=args.max_turns, output_language=args.output_language)
                    payload = {"document": document.to_dict(include_raw=False), "analysis": analysis, "reviews": review}
                else:
                    payload = {"document": document.to_dict(include_raw=False), "analysis": analysis, "review_plan": build_review_plan(document, analysis, skills=args.skills, max_turns=args.max_turns, output_language=args.output_language)}
            else:
                analysis = analyze_transcript(document)
                if args.transcript_command == "analyze":
                    payload = {"document": document.to_dict(include_raw=False), "analysis": analysis}
                else:
                    payload = {"document": document.to_dict(include_raw=False), "analysis": analysis, "artifacts": build_bundle(document, analysis, args.output_dir, include_raw=not args.no_raw, raw_text=raw_text)}
            if args.output:
                Path(args.output).expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _json(payload)
            return 0
        if args.command == "service":
            from domains.theraskill import launcher
            result = launcher.status() if args.service_command == "status" else getattr(launcher, args.service_command)(services=args.service)
            _json(result)
            return 0
        if args.command == "provider":
            from .provider_smoke import check_provider, list_providers
            if args.provider_command == "list":
                _json(list_providers())
            else:
                result = check_provider(args.kind, live=args.live)
                _json(result)
                return 0 if result.get("status") in {"configured", "ok"} else 2
            return 0
    except (OSError, ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {redact_secrets(str(exc))}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
