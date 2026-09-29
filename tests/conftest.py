import json
from datetime import datetime, timezone

import pytest

from pipeline_diff import store, timeline

START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 8, tzinfo=timezone.utc)

PIPELINES = [{"id": "default", "label": "Sales Pipeline", "stages": [
    {"id": "discovery", "label": "Discovery", "order": 0, "is_closed": False, "is_won": False},
    {"id": "proposal", "label": "Proposal", "order": 1, "is_closed": False, "is_won": False},
    {"id": "contract", "label": "Contract sent", "order": 2, "is_closed": False, "is_won": False},
    {"id": "won", "label": "Closed won", "order": 3, "is_closed": True, "is_won": True},
    {"id": "lost", "label": "Closed lost", "order": 4, "is_closed": True, "is_won": False},
]}]
OWNERS = [{"id": "o1", "name": "Ava Reyes", "email": "ava.reyes@example.com"},
          {"id": "o2", "name": "Marcus Chen", "email": "marcus.chen@example.com"}]

# id: (created, deleted, name, [(when, field, value), ...]); every deal starts with its full state at creation.
DEALS = {
    "same": ("2026-08-01", None, "Brightpath Logistics - Platform", [("2026-08-01", "discovery", 100, "2026-10-15", "o1")]),
    "up": ("2026-08-01", None, "Cobalt Ridge Health - Analytics", [("2026-08-01", "proposal", 200, "2026-10-20", "o1"), ("2026-09-03", "amount", 250)]),
    "down": ("2026-08-01", None, "Juniper Freight - Support", [("2026-08-01", "proposal", 300, "2026-11-01", "o2"), ("2026-09-03", "amount", 200)]),
    "won": ("2026-08-01", None, "Ironvale Energy - Expansion", [("2026-08-01", "contract", 400, "2026-09-30", "o1"), ("2026-09-04", "stage", "won")]),
    "lost": ("2026-08-01", None, "Lumen Harbor - Pilot", [("2026-08-01", "proposal", 500, "2026-10-01", "o2"), ("2026-09-04", "stage", "lost")]),
    "removed": ("2026-08-01", "2026-09-05", "Quarry Lane Foods - Platform", [("2026-08-01", "discovery", 600, "2026-12-01", "o1")]),
    "new": ("2026-09-02", None, "Silverline Dental - Onboarding", [("2026-09-02", "discovery", 700, "2026-11-15", "o2")]),
    "reopened": ("2026-07-01", None, "Tidewater Labs - Renewal", [("2026-07-01", "proposal", 800, "2026-10-30", "o1"), ("2026-08-20", "stage", "lost"), ("2026-09-06", "stage", "discovery")]),
    "pushed": ("2026-08-01", None, "Oakmont Retail - Platform", [("2026-08-01", "proposal", 900, "2026-10-10", "o2"), ("2026-09-03", "close_date", "2027-01-15")]),
    "pulled": ("2026-08-01", None, "Pinecrest Insurance - Data sync", [("2026-08-01", "proposal", 1000, "2027-02-01", "o1"), ("2026-09-03", "close_date", "2026-11-01")]),
    "owner": ("2026-08-15", None, "Redstone Media - Expansion", [("2026-08-15", "proposal", 1100, "2026-11-20", "o1"), ("2026-09-02", "owner_id", "o2")]),
    "slipped": ("2026-08-15", None, "Bluefin Systems - Pilot", [("2026-08-15", "contract", 1200, "2026-09-05", "o2")]),
    "quick_win": ("2026-09-03", None, "Summit Arc Software - Add-on", [("2026-09-03", "contract", 50, "2026-09-10", "o1"), ("2026-09-05", "stage", "won")]),
    "forward": ("2026-08-20", None, "Harborview Clinics - Platform", [("2026-08-20", "discovery", 1300, "2026-12-15", "o2"), ("2026-09-04", "stage", "contract")]),
}


def _history(deal_id, created, events):
    first, rest = events[0], events[1:]
    when = f"{first[0]}T10:00:00Z"
    lines = [{"deal_id": deal_id, "field": f, "value": v, "changed_at": when}
             for f, v in zip(("stage", "amount", "close_date", "owner_id", "pipeline"), (*first[1:], "default"))]
    lines += [{"deal_id": deal_id, "field": f, "value": v, "changed_at": f"{w}T10:00:00Z"} for w, f, v in rest]
    return lines


def write_dataset(folder, deals=DEALS):
    rows, history = [], []
    for deal_id, (created, deleted, name, events) in deals.items():
        lines = _history(deal_id, created, events)
        history += lines
        current = {line["field"]: line["value"] for line in lines}
        rows.append({"id": deal_id, "name": name, "created_at": f"{created}T10:00:00Z",
                     "deleted_at": f"{deleted}T10:00:00Z" if deleted else None, "url": f"https://crm.example.com/deal/{deal_id}", **current})
    store.write_jsonl(folder / "deals.jsonl", rows)
    store.write_jsonl(folder / "history.jsonl", history)
    store.write_jsonl(folder / "pipelines.jsonl", PIPELINES)
    store.write_jsonl(folder / "owners.jsonl", OWNERS)
    (folder / "sync.json").write_text(json.dumps({"synced_at": "2026-09-08T00:00:00Z", "currency": "USD"}))
    return folder


@pytest.fixture
def dataset(tmp_path):
    return timeline.load(write_dataset(tmp_path / "fixture"))
