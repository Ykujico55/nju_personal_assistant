"""Standalone local planning commands, independent of the assistant host."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .domain import FilterSpec, Job, filter_jobs
from .planning import boss_plan, mail_plans, read_resume
from .sources import load_jobs, load_recipients
from .storage import LocalStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BOSS and email application material planner")
    parser.add_argument("--database", type=Path, default=Path(".local/career.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    for group in ("jobs", "recipients"):
        child = commands.add_parser(group)
        actions = child.add_subparsers(dest="action", required=True)
        importer = actions.add_parser("import")
        importer.add_argument("path", type=Path)
        listing = actions.add_parser("list")
        if group == "jobs":
            listing.add_argument("--include", action="append", default=[])
            listing.add_argument("--exclude", action="append", default=[])
            listing.add_argument("--city", action="append", default=[])
            listing.add_argument("--deny-company", action="append", default=[])
            listing.add_argument("--limit", type=int, default=100)
    plans = commands.add_parser("plans").add_subparsers(dest="action", required=True)
    plans.add_parser("list")
    show = plans.add_parser("show")
    show.add_argument("plan_id")
    show.add_argument("--history", action="store_true")
    export = plans.add_parser("export")
    export.add_argument("path", type=Path)
    preparing = commands.add_parser("plan").add_subparsers(dest="channel", required=True)
    boss = preparing.add_parser("boss")
    boss.add_argument("job_id")
    boss.add_argument("--action", choices=("greet", "message", "send_resume"), default="message")
    boss.add_argument("--message", default="")
    boss.add_argument("--recruiter")
    email = preparing.add_parser("email")
    email.add_argument("--subject", required=True)
    email.add_argument("--body", required=True)
    email.add_argument("--recipient")
    for child in (boss, email):
        child.add_argument("--resume", type=Path)
        child.add_argument("--expected-version", type=int)
    return parser


def _run(args: argparse.Namespace, store: LocalStore) -> dict[str, Any]:
    if args.command == "jobs":
        if args.action == "import":
            return {"imported": store.import_jobs(load_jobs(args.path))}
        spec = FilterSpec(
            include_keywords=tuple(args.include), exclude_keywords=tuple(args.exclude),
            locations=tuple(args.city), company_denylist=tuple(args.deny_company),
            daily_limit=args.limit,
        )
        selected = filter_jobs((Job.from_mapping(row) for row in store.jobs()), spec)
        return {"items": [job.as_dict() for job in selected], "execution_available": False}
    if args.command == "recipients":
        if args.action == "import":
            return {"imported": store.import_recipients(load_recipients(args.path))}
        return {"items": store.recipients()}
    if args.command == "plans":
        if args.action == "show":
            result = store.get_plan(args.plan_id)
            if args.history:
                result["history"] = store.plan_versions(args.plan_id)
            return result
        result = {"items": store.plans(), "execution_available": False}
        if args.action == "export":
            material = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            with args.path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(material)
            return {"exported": len(result["items"]), "path": str(args.path)}
        return result
    resume = read_resume(args.resume) if args.resume else None
    if args.channel == "boss":
        job = Job.from_mapping(store.get_job(args.job_id))
        if args.recruiter is not None:
            job = replace(job, recruiter=args.recruiter)
        return store.save_plan(boss_plan(job, args.action, message=args.message, resume=resume),
                               expected_version=args.expected_version)
    recipients = store.recipients()
    if args.recipient:
        recipients = [row for row in recipients if row["email"].casefold() ==
                      args.recipient.casefold()]
        if not recipients:
            raise ValueError("recipient not found in the local list")
    if not recipients:
        raise ValueError("import a local recipient list first")
    planned = mail_plans(recipients, args.subject, args.body, resume)
    return {"items": [store.save_plan(plan, expected_version=args.expected_version)
                      for plan in planned], "execution_available": False}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with LocalStore(args.database) as store:
            result = _run(args, store)
    except sqlite3.Error:
        print(json.dumps({"error": {"code": "DATABASE_ERROR",
                                    "message": "local planner database is unavailable"}}))
        return 2
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "INPUT_ERROR"),
                                    "message": str(exc)}}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
