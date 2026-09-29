"""The JSONL files a source keeps in the data folder, and merging a fresh sync into them."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FILES = ("deals", "history", "pipelines", "owners")
TRACKED = ("stage", "amount", "close_date", "owner_id", "pipeline", "forecast_category")
FORECAST = ("Omitted", "Pipeline", "Best case", "Commit", "Closed")  # the categories both CRMs use, lowest to highest


def now():
    return datetime.now(timezone.utc)


def server_now(meta):
    """The CRM's clock from a fetch, or this machine's if the CRM sent none."""
    return parse(meta["server_time"]) if meta.get("server_time") else now()


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text):
    """An aware UTC datetime from an ISO string, with or without Z, fraction, or offset."""
    if isinstance(text, str) and text.endswith("Z") and "+" not in text and "-" not in text[10:] and text == text.strip():
        return datetime.fromisoformat(_fraction(text[:-1])).replace(tzinfo=timezone.utc)  # the common case, and the fast one
    text = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", str(text).strip().replace("Z", "+00:00"))  # Salesforce writes +0000
    text = _fraction(text)
    moment = datetime.fromisoformat(text)
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _fraction(text):
    """Seconds fractions padded or cut to 6 digits: Python 3.10 reads only 3 or 6."""
    return re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1) if "." in text else text


def read_jsonl(path, repair=False):
    """The rows of a JSONL file. With repair, a damaged file is renamed to .bad and read as empty."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                if repair:
                    path.replace(path.with_suffix(".jsonl.bad"))
                    print(f"pipeline-diff: {path} was damaged; set aside as {path.name}.bad and rebuilt from the CRM", file=sys.stderr)
                    return []
                raise SystemExit(f"{path} line {number} is not valid JSON ({exc.msg}). Run the sync again to rebuild it.") from None
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    os.replace(temp, path)


def sources(data_dir):
    """Source folders that hold at least one sync, by name."""
    root = Path(data_dir)
    return sorted(p.name for p in root.iterdir() if (p / "sync.json").exists()) if root.is_dir() else []


def load(folder, repair=False):
    folder = Path(folder)
    data = {name: read_jsonl(folder / f"{name}.jsonl", repair) for name in FILES}
    sync = folder / "sync.json"
    try:
        data["sync"] = json.loads(sync.read_text(encoding="utf-8")) if sync.exists() else {}
    except json.JSONDecodeError:
        if not repair:
            raise SystemExit(f"{sync} is damaged. Run the sync again to rebuild it.") from None
        data["sync"] = {}
    return data


def snapshot_times(folder):
    return sorted(parse(p.stem.replace("_", ":")) for p in (Path(folder) / "snapshots").glob("*.jsonl"))


def save(folder, data, synced_at, meta=None):
    """Write the files, a dated snapshot of the deals, and sync.json."""
    folder = Path(folder)
    for name in FILES:
        write_jsonl(folder / f"{name}.jsonl", data[name])
    guard = folder.parent / ".gitignore"
    if not guard.exists():
        guard.write_text("# Synced CRM data: real customer data. Keep it out of git.\n*\n", encoding="utf-8")
    write_jsonl(folder / "snapshots" / f"{stamp(synced_at).replace(':', '_')}.jsonl", data["deals"])
    info = {**(meta or {}), "synced_at": stamp(synced_at), "deals": len(data["deals"]), "history": len(data["history"])}
    temp = folder / "sync.json.tmp"
    temp.write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, folder / "sync.json")
    return info


def merge(previous, fetched, synced_at):
    """Deals the CRM stopped returning are kept as deleted, with their history. Where a current value moved with no
    history line (a field the CRM does not track), a line at the sync time is added."""
    deals = {deal["id"]: deal for deal in fetched["deals"]}
    fresh = set(deals)
    known = {deal["id"]: deal for deal in previous.get("deals", [])}
    for deal_id, deal in deals.items():
        old = known.get(deal_id, {})
        for key in ("url", "currency"):
            if deal.get(key) is None and old.get(key):
                deal[key] = old[key]
        gaps = list(old.get("deleted_periods") or [])
        if old.get("deleted_at") and not deal.get("deleted_at"):  # deleted, then restored in the CRM
            gaps.append([old["deleted_at"], stamp(synced_at)])
        if gaps:
            deal["deleted_periods"] = gaps
    for deal in previous.get("deals", []):
        if deal["id"] not in deals:
            deals[deal["id"]] = {**deal, "deleted_at": deal.get("deleted_at") or stamp(synced_at)}
    earliest = {}
    for line in fetched["history"]:
        key = (line["deal_id"], line["field"])
        earliest[key] = min(earliest.get(key, line["changed_at"]), line["changed_at"])
    # Keep lines of deals the CRM stopped returning, the sync-time lines, and history older than what the CRM still returns.
    history = list(fetched["history"]) + [h for h in previous.get("history", []) if h["deal_id"] in deals and (
        h["deal_id"] not in fresh or h.get("source") == "snapshot" or h["changed_at"] < earliest.get((h["deal_id"], h["field"]), "~"))]
    latest = {}
    for line in sorted(history, key=lambda h: h["changed_at"]):
        latest[(line["deal_id"], line["field"])] = line["value"]
    for deal_id in fresh:
        deal = deals[deal_id]
        for field in TRACKED:
            if field in deal and latest.get((deal_id, field), object()) != deal[field]:
                history.append({"deal_id": deal_id, "field": field, "value": deal[field], "changed_at": stamp(synced_at), "source": "snapshot"})
    history.sort(key=lambda h: (h["deal_id"], h["changed_at"], h["field"]))
    pipelines = [dict(p, stages=list(p["stages"])) for p in fetched["pipelines"]]
    for old in previous.get("pipelines", []):  # a stage or pipeline removed in the CRM still describes past states
        match = next((p for p in pipelines if p["id"] == old["id"]), None)
        if match is None:
            pipelines.append(old)
        else:
            ids = {s["id"] for s in match["stages"]}
            match["stages"] += [s for s in old["stages"] if s["id"] not in ids]
    return {**fetched, "deals": sorted(deals.values(), key=lambda d: d["id"]), "history": history, "pipelines": pipelines}
