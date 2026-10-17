"""CLI surface for the TheraSkillAgent platform profile."""

from __future__ import annotations

import argparse
import json

from domains.theraskill.launcher import RuntimeLaunchError

from installers.theraskill import (
    ReleaseInstallError,
    doctor,
    download_release,
    enable_profile,
    install_archive,
    install_release,
    rollback_release,
)


def register_cli(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="platform_action", required=True)

    install_parser = actions.add_parser("install", help="Install a TheraSkill runtime release")
    install_parser.add_argument("domain", choices=["theraskill"])
    install_parser.add_argument("--source", help="Candidate release directory")
    install_parser.add_argument("--archive", help="Administrator-uploaded .tar.gz or .tgz release")
    install_parser.add_argument("--archive-sha256", help="Expected SHA-256 of --archive")
    install_parser.add_argument("--manifest-url", help="Download a release manifest and its declared artifacts")
    install_parser.add_argument("--release", dest="release_id", help="Release id from manifest.json")
    install_parser.add_argument("--activate", action="store_true", help="Activate the current slot after validation")

    doctor_parser = actions.add_parser("doctor", help="Check an installed TheraSkill runtime release")
    doctor_parser.add_argument("domain", choices=["theraskill"])
    doctor_parser.add_argument("--release", dest="release_id", default="current")

    status_parser = actions.add_parser("status", help="Show the TheraSkillAgent TheraSkill platform status")
    status_parser.add_argument("domain", choices=["theraskill"])

    enable_parser = actions.add_parser("enable", help="Check that TheraSkill is available as a bundled platform")
    enable_parser.add_argument("domain", choices=["theraskill"])

    run_parser = actions.add_parser("run", help="Start the active TheraSkill runtime profile")
    run_parser.add_argument("domain", nargs="?", choices=["theraskill"])
    run_parser.add_argument("--profile", dest="profile", choices=["theraskill"])
    run_parser.add_argument("--service", action="append", dest="services")

    stop_parser = actions.add_parser("stop", help="Stop the active TheraSkill runtime profile")
    stop_parser.add_argument("domain", nargs="?", choices=["theraskill"])
    stop_parser.add_argument("--profile", dest="profile", choices=["theraskill"])
    stop_parser.add_argument("--service", action="append", dest="services")

    rollback_parser = actions.add_parser("rollback", help="Activate an installed immutable TheraSkill release")
    rollback_parser.add_argument("domain", choices=["theraskill"])
    rollback_parser.add_argument("release")


def platform_command(args: argparse.Namespace) -> None:
    action = getattr(args, "platform_action", "")
    try:
        if action == "install":
            source = getattr(args, "source", None)
            archive = getattr(args, "archive", None)
            manifest_url = getattr(args, "manifest_url", None)
            archive_sha256 = getattr(args, "archive_sha256", None)
            inputs = [source, archive, manifest_url]
            if sum(bool(value) for value in inputs) != 1:
                raise ReleaseInstallError(
                    "provide exactly one of --source, --archive or --manifest-url"
                )
            if archive_sha256 and not archive:
                raise ReleaseInstallError("--archive-sha256 requires --archive")
            if manifest_url:
                destination = download_release(manifest_url, None, activate=args.activate)
            elif archive:
                destination = install_archive(
                    archive,
                    None,
                    release_id=args.release_id,
                    activate=args.activate,
                    archive_sha256=archive_sha256,
                )
            else:
                destination = install_release(source, None, release_id=args.release_id, activate=args.activate)
            print(json.dumps({"status": "complete", "release_dir": str(destination)}, ensure_ascii=False))
            return
        if action == "doctor":
            print(json.dumps(doctor(None, args.release_id), ensure_ascii=False))
            return
        if action == "status":
            from theraskill_agent.platform import THERASKILL_PLATFORM

            print(json.dumps(THERASKILL_PLATFORM.runtime_snapshot(), ensure_ascii=False))
            return
        if action in {"run", "stop"}:
            from domains.theraskill.launcher import start, stop

            services = getattr(args, "services", None)
            payload = start(services=services) if action == "run" else stop(services=services)
            print(json.dumps({"status": "complete", "services": payload}, ensure_ascii=False))
            return
        if action == "rollback":
            print(json.dumps(rollback_release(None, args.release), ensure_ascii=False))
            return
        if action == "enable":
            print(json.dumps({"status": "complete", "plugin": "theraskill", "mode": "bundled_platform", **enable_profile(None)}, ensure_ascii=False))
            return
        raise ValueError(f"unknown platform action: {action}")
    except (ReleaseInstallError, RuntimeLaunchError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
