import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from pipeline_diff import __version__, config, store
from pipeline_diff.crm import CRMError

CRMS = ("hubspot", "salesforce")


def client_for(name):
    if name == "hubspot":
        from pipeline_diff.crm.hubspot import HubSpot

        return HubSpot(config.setting("HUBSPOT_TOKEN"))
    from pipeline_diff.crm.salesforce import Salesforce

    return Salesforce(config.setting("SF_DOMAIN"), config.setting("SF_CONSUMER_KEY"), config.setting("SF_CONSUMER_SECRET"))


def sync_client(client, folder):
    """Pull deals and their history and merge them into the folder. Times come from the CRM's clock when it sends one."""
    fetched = client.fetch()
    meta = fetched.pop("meta")
    synced_at = store.server_now(meta)
    previous = store.load(folder, repair=True)
    meta = {**meta, "currency": meta.get("currency") or previous["sync"].get("currency") or "USD"}
    return store.save(folder, store.merge(previous, fetched, synced_at), synced_at, meta)


def sync(name, data_dir):
    return sync_client(client_for(name), Path(data_dir) / name)


def cmd_sync(args):
    for name in args.crm:
        info = sync(name, config.data_dir(args.data_dir))
        print(f"{name}: {info['deals']} deals, {info['history']} history lines, synced {info['synced_at']}")
    return 0


def cmd_demo(args):
    from pipeline_diff import demo

    if args.days < 1:
        raise SystemExit("--days must be at least 1.")
    info = demo.generate(config.data_dir(args.data_dir) / "demo", days=args.days, seed=args.seed, reps=args.reps)
    print(f"demo: {info['deals']} invented deals over {args.days} days for {args.reps} reps, {info['history']} history lines")
    return 0


def cmd_seed(args):
    from pipeline_diff import seed

    if not args.test_account:
        print("seed writes invented deals to the CRM. Run it only against a test account, and pass --test-account to confirm.", file=sys.stderr)
        return 2
    folder = config.data_dir(args.data_dir) / args.crm
    client = client_for(args.crm)
    if args.baseline:
        print(f"{args.crm}: created {seed.baseline(client, folder, count=args.baseline)} deals")
    else:
        expected = seed.change_round(client, folder)
        print(f"{args.crm}: changed {len(expected)} deals. Run sync, then: pipeline-diff seed {args.crm} --verify")
    return 0


def cmd_verify(args):
    from pipeline_diff import seed

    problems = seed.verify(config.data_dir(args.data_dir) / args.crm)
    for problem in problems:
        print(problem)
    print(f"{args.crm}: {'every change landed where the seed round said' if not problems else f'{len(problems)} mismatches'}")
    return 1 if problems else 0


def _source(args):
    from pipeline_diff import timeline

    folder = config.data_dir(args.data_dir) / args.source
    if not (folder / "sync.json").exists():
        found = ", ".join(store.sources(folder.parent)) or "none yet"
        raise SystemExit(f"no synced data for {args.source!r} in {folder.parent} (synced sources: {found}). "
                         "Run pipeline-diff sync hubspot, pipeline-diff sync salesforce, or pipeline-diff demo.")
    return timeline.load(folder)


def cmd_output(args):
    """report and export: compare two moments, then write the summary or the agent workspace."""
    from pipeline_diff import ranges, report, workspace
    from pipeline_diff.buckets import compare

    ds, tz = _source(args), config.timezone()
    start, end, scope = ranges.resolve(ds, args.since, args.start, args.end, args.scope, tz)
    result = compare(ds, start, end, scope, tz=tz)
    out = Path(args.out) if args.out else config.data_dir(args.data_dir) / args.source / args.command.replace("export", "workspace")
    if out.exists() and not out.is_dir():
        raise SystemExit(f"{out} is a file. --out takes a folder.")
    if args.command == "export" and getattr(args, "refresh", False) and workspace.run_id(result.end, tz) != workspace.run_id(ranges.latest(ds), tz):
        raise SystemExit("--refresh rewrites only the current week. Past weeks keep what you wrote in them.")
    marker = out / ".pipeline-diff"
    if out.is_dir() and any(out.iterdir()) and not marker.exists() and not args.force:
        raise SystemExit(f"{out} already has files that pipeline-diff did not write. Pick an empty folder, or pass --force to write into it.")
    try:
        out.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError):
        raise SystemExit(f"{out} cannot be a folder: part of that path is a file.") from None
    marker.write_text(f"Written by pipeline-diff {args.command}. Delete this file to protect the folder again.\n", encoding="utf-8")
    if args.command == "report":
        print("\n".join(str(p) for p in report.write(ds, result, out, tz)))
    else:
        run = out / "runs" / workspace.run_id(result.end, tz)
        kept = (run / "summary.md").exists() and not args.refresh
        print(f"workspace written to {workspace.export(ds, result, out, tz, refresh=args.refresh)}")
        if kept:
            print(f"{run.name} was already exported and was left as it was, with its own range and scope. Pass --refresh to rewrite it.")
    return 0


def cmd_dashboard(args):
    app = Path(__file__).parent / "dashboard" / "app.py"
    env = {**os.environ, "PIPELINE_DIFF_DATA_DIR": str(config.data_dir(args.data_dir).resolve())}
    command = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port), "--browser.gatherUsageStats", "false",
               "--theme.base", "light", "--theme.primaryColor", "#2a78d6",
               "--theme.backgroundColor", "#fcfcfb", "--theme.secondaryBackgroundColor", "#f3f2ee", "--theme.textColor", "#0b0b0b",
               "--client.toolbarMode", "minimal", "--server.address", args.host]
    if args.headless:
        command += ["--server.headless", "true"]
    if args.host in ("localhost", "127.0.0.1"):
        command += ["--server.allowedHosts", "localhost", "--server.allowedHosts", "127.0.0.1"]  # blocks DNS rebinding
    # Run from the package folder with a safe path, so a streamlit/ folder in the current directory is never imported.
    return subprocess.call(command, env={**env, "PYTHONSAFEPATH": "1"}, cwd=app.parent)


def main(argv=None):
    config.load_env()
    parser = argparse.ArgumentParser(prog="pipeline-diff", description="See what changed in your HubSpot or Salesforce pipeline.")
    parser.add_argument("--version", action="version", version=f"pipeline-diff {__version__}")
    parser.add_argument("--data-dir", help="where synced data lives (default: PIPELINE_DIFF_DATA_DIR or ./data)")
    sub = parser.add_subparsers(dest="command", required=True)

    item = sub.add_parser("sync", help="pull deals and their history from a CRM (read-only)")
    item.add_argument("crm", nargs="+", choices=CRMS, help="hubspot, salesforce, or both")
    item.set_defaults(func=cmd_sync)

    item = sub.add_parser("demo", help="generate an invented demo source, no CRM needed")
    item.add_argument("--days", type=int, default=730, help="how many days of history to invent (default 730, two years)")
    item.add_argument("--reps", type=int, default=12, help="how many invented reps, 1 to 24 (default 12)")
    item.add_argument("--seed", type=int, default=7, help="random seed; the same seed gives the same data (default 7)")
    item.set_defaults(func=cmd_demo)

    item = sub.add_parser("dashboard", help="open the dashboard")
    item.add_argument("--port", type=int, default=8501, help="port to serve on (default 8501)")
    item.add_argument("--headless", action="store_true", help="do not open a browser")
    item.add_argument("--host", default="localhost", help="address to listen on (default: localhost, this machine only; it has no login)")
    item.set_defaults(func=cmd_dashboard)

    for name, help_text in (("report", "write summary.md and changes.csv"), ("export", "write the agent workspace")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("source", help="a synced source: hubspot, salesforce, or demo")
        item.add_argument("--since", default="7d", help="7d, 30d, qtd (quarter to date), or sync (since the previous sync). Default 7d.")
        item.add_argument("--start", type=date.fromisoformat, help="start date, instead of --since")
        item.add_argument("--end", type=date.fromisoformat, help="end date (default: the latest sync)")
        item.add_argument("--scope", default="all", help="all (all open deals), this-quarter, next-quarter, or 2026-10-01:2026-12-31")
        item.add_argument("--out", help=f"output folder (default: <data dir>/<source>/{'report' if name == 'report' else 'workspace'})")
        item.add_argument("--force", action="store_true", help="write into a folder that already holds other files")
        if name == "export":
            item.add_argument("--refresh", action="store_true",
                              help="rewrite the current week's run; the old summary is kept as summary.previous.md")
        item.set_defaults(func=cmd_output)

    item = sub.add_parser("seed", help="fill a CRM test account with invented deals (writes to the CRM)")
    item.add_argument("crm", choices=CRMS, help="hubspot or salesforce")
    item.add_argument("--test-account", action="store_true", help="confirm this is a test account")
    group = item.add_mutually_exclusive_group()
    group.add_argument("--baseline", type=int, metavar="N", help="create N deals")
    group.add_argument("--verify", action="store_true", help="check the last sync against the last seed round")
    item.set_defaults(func=lambda a: cmd_verify(a) if a.verify else cmd_seed(a))

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CRMError as exc:
        print(f"pipeline-diff: {config.redact(exc)}", file=sys.stderr)
        return 1
