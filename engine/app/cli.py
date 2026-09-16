"""Command-line tools.

    python -m app.cli check                 verify config, CV, database, keys, channels
    python -m app.cli run                   run the full pipeline once without n8n
    python -m app.cli notify-test           send a test notification
    python -m app.cli export-schemas        write JSON schemas to schemas/
    python -m app.cli reprocess ID [ID...]  send opportunities back through filtering + analysis
"""
from __future__ import annotations

import argparse
import json
import sys

from . import db
from .config import ROOT, ConfigError, get_env, load_config
from .models import ExtractedPosting, JobAnalysis, MaterialsVerification, NormalizedJob, TailoredMaterials
from .profile import ProfileError, load_profile


def _init_db() -> None:
    db.init_pool(get_env().database_url, max_size=6)
    db.run_migrations()


def cmd_check(_: argparse.Namespace) -> int:
    ok = True
    env = get_env()
    try:
        cfg = load_config()
        print(f"[ok] config ({cfg.digest}) model={cfg.get('ai.model')} budget=${cfg.get('ai.daily_budget_usd')}/day")
    except ConfigError as exc:
        print(f"[FAIL] config: {exc}")
        ok = False
    try:
        profile = load_profile()
        print(f"[ok] CV {profile.cv_path.name} ({len(profile.cv_text)} chars), experience ~{profile.experience_months()} months")
    except ProfileError as exc:
        print(f"[FAIL] profile: {exc}")
        ok = False
    try:
        _init_db()
        print("[ok] database reachable, migrations applied")
    except Exception as exc:
        print(f"[FAIL] database: {exc}")
        ok = False
    token = env.engine_api_token or ""
    print(("[ok] " if len(token) >= 24 and not token.startswith("change-me") else "[FAIL] ") + "ENGINE_API_TOKEN")
    ok = ok and len(token) >= 24 and not token.startswith("change-me")
    print(("[ok] " if env.ai_available else "[warn] ") + "ANTHROPIC_API_KEY " + ("set" if env.ai_available else "missing - AI analysis paused"))
    print(f"[info] search providers: {', '.join(env.search_providers()) or 'none'}")
    print(f"[info] notifications: telegram={env.telegram_enabled} email={env.email_enabled}")
    print(f"[info] auto-apply: config={load_config().get('applications.auto_apply_enabled') if ok else '?'} env={env.auto_apply_enabled} (phase 1: never submits)")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    from .services import fetching, processor, runs

    _init_db()
    started = runs.start_run("cli")
    run_id = started["run_id"]
    print(json.dumps(started, indent=2, default=str))
    for _ in range(args.max_batches):
        batch = fetching.fetch_next(run_id, max_tasks=15, max_seconds=120)
        for task in batch["executed"]:
            print(f"  fetch {task['status']:8} {task['label'][:60]:60} found={task.get('found', 0)} new={task.get('new', 0)} {task.get('error') or ''}")
        if batch["remaining"] == 0:
            break
    for _ in range(args.max_batches):
        batch = processor.process_next(run_id, max_items=8, max_seconds=300)
        for item in batch["results"]:
            print(f"  process {item.get('outcome', ''):22} {str(item.get('score', '')):>3} {item.get('title', '')[:70]}")
        if batch.get("ai_note"):
            print(f"  note: {batch['ai_note']}")
        if batch["remaining"] == 0:
            break
    print(json.dumps(runs.finish_run(run_id), indent=2, default=str))
    return 0


def cmd_notify_test(_: argparse.Namespace) -> int:
    from .services import notify

    _init_db()
    print(json.dumps(notify.send_test(), indent=2))
    return 0


def cmd_export_schemas(args: argparse.Namespace) -> int:
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for model in (NormalizedJob, JobAnalysis, ExtractedPosting, TailoredMaterials, MaterialsVerification):
        path = out / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
        print(f"wrote {path}")
    return 0


def cmd_reprocess(args: argparse.Namespace) -> int:
    _init_db()
    count = db.execute(
        "UPDATE opportunities SET pipeline_status = 'new', analysis_attempts = 0, last_processed_run_id = NULL,"
        " materials_status = CASE WHEN materials_status = 'failed' THEN 'none' ELSE materials_status END WHERE id = ANY(%s)",
        (args.ids,),
    )
    print(f"{count} opportunities queued for the next run")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    run = sub.add_parser("run")
    run.add_argument("--max-batches", type=int, default=100)
    run.set_defaults(fn=cmd_run)
    sub.add_parser("notify-test").set_defaults(fn=cmd_notify_test)
    export = sub.add_parser("export-schemas")
    export.add_argument("--out", default="schemas")
    export.set_defaults(fn=cmd_export_schemas)
    reprocess = sub.add_parser("reprocess")
    reprocess.add_argument("ids", type=int, nargs="+")
    reprocess.set_defaults(fn=cmd_reprocess)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
