"""Fill a CRM test account with invented deals, then change them in rounds. Only for test accounts: it writes to the CRM.

Each round changes every picked deal in exactly one way and records the bucket or flag that change must produce, so
`verify` can check a sync against it. Only deals this script created are ever changed.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

from pipeline_diff import fake, store, timeline
from pipeline_diff.buckets import compare

# action: (deals per round, the bucket it must produce, the flag it must produce)
ACTIONS = {"advance": (10, None, "stage forward"), "back": (3, None, "stage back"), "amount up": (5, "amount up", None),
           "amount down": (3, "amount down", None), "push": (6, None, "close date pushed"), "pull": (3, None, "close date pulled"),
           "won": (6, "won", None), "lost": (4, "lost", None), "delete": (2, "removed", None), "owner": (3, None, "owner changed"),
           "forecast": (4, None, "forecast up"), "new": (8, "new", None)}


def _load(folder):
    path = Path(folder) / "seed.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"ids": [], "accounts": {}, "rounds": []}


def _save(folder, state):
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "seed.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def _check_account(client, state):
    """Refuse anything but a test account, and any account other than the one this seed started in."""
    account = client.account()
    if not account["test"]:
        raise SystemExit(f"{client.name} account {account['id']} is {account['kind']}, not a test account. seed only writes to HubSpot "
                         "developer test accounts or sandboxes, and Salesforce Developer Edition orgs or sandboxes.")
    if state.get("account") and state["account"] != account["id"]:
        raise SystemExit(f"seed.json belongs to account {state['account']}, but this key opens account {account['id']}. "
                         "Use a new data folder for a new account.")
    state["account"] = account["id"]


def _stages(pipelines, pipeline_id):
    stages = sorted(next(p for p in pipelines if p["id"] == pipeline_id)["stages"], key=lambda s: s["order"])
    return [s["id"] for s in stages if not s["is_closed"]], next(s["id"] for s in stages if s["is_won"]), next(
        s["id"] for s in stages if s["is_closed"] and not s["is_won"])


def _names(rng, count, taken):
    names = []
    while len(names) < count:
        name = fake.deal_name(rng)
        if name not in taken:
            taken.add(name)
            names.append(name)
    return names


def _attach_accounts(client, state, specs):
    """Salesforce opportunities get an account per invented company, created once."""
    if not hasattr(client, "create_accounts"):
        return
    missing = sorted({s["name"].split(" - ")[0] for s in specs} - set(state["accounts"]))
    if missing:
        state["accounts"].update(zip(missing, client.create_accounts([(c, "https://" + fake.domain(c)) for c in missing])))
    for spec in specs:
        spec["account_id"] = state["accounts"][spec["name"].split(" - ")[0]]


def _spec(rng, name, open_stages, owners, today, slipped=False):
    close = today - timedelta(days=rng.randint(2, 20)) if slipped else today + timedelta(days=rng.randint(12, 150))
    return {"name": name, "stage": rng.choices(open_stages, weights=[5, 4, 3, 2, 2, 1, 1, 1][:len(open_stages)])[0],
            "amount": fake.amount(rng), "close_date": close.isoformat(), "owner_id": rng.choice(owners), "forecast_category": "Pipeline"}


def _fit(info, specs):
    """New deals get a forecast category only where the CRM has one."""
    return specs if info["meta"].get("forecast") else [{k: v for k, v in s.items() if k != "forecast_category"} for s in specs]


def baseline(client, folder, count=100, seed=11, today=None):
    """Create count invented deals, about one in ten already past its close date."""
    rng, today = random.Random(seed), today or date.today()
    state = _load(folder)
    _check_account(client, state)
    info = client.fetch()
    pipeline = info["pipelines"][0]["id"]
    open_stages, _, _ = _stages(info["pipelines"], pipeline)
    owners = client.assignable_owners() if hasattr(client, "assignable_owners") else [o["id"] for o in info["owners"]]
    taken = {d["name"] for d in info["deals"]}
    specs = _fit(info, [dict(_spec(rng, name, open_stages, owners, today, slipped=i % 10 == 0), pipeline=pipeline)
                        for i, name in enumerate(_names(rng, count, taken))])
    _attach_accounts(client, state, specs)
    state["ids"] += [i for i in client.create(specs) if i]
    state["owners"] = owners
    _save(folder, state)
    return len(specs)


def change_round(client, folder, seed=None, today=None):
    """Change seeded deals in one round and record what each change must produce."""
    state = _load(folder)
    if not state["ids"]:
        raise SystemExit("No seeded deals yet. Run the baseline first.")
    _check_account(client, state)
    rng, today = random.Random(seed if seed is not None else 100 + len(state["rounds"])), today or date.today()
    info = client.fetch()
    pipeline = info["pipelines"][0]["id"]
    open_stages, won, lost = _stages(info["pipelines"], pipeline)
    owners = state.get("owners") or [o["id"] for o in info["owners"]]
    seeded = set(state["ids"])
    pool = [d for d in info["deals"] if d["id"] in seeded and not d.get("deleted_at") and d["stage"] in open_stages]
    rng.shuffle(pool)
    updates, deletes, expected = [], [], {}

    def take(action, fits):
        picked = [d for d in pool if fits(d)][:ACTIONS[action][0]]
        for deal in picked:
            pool.remove(deal)
        return picked

    def expect(deal_id, action):
        _, bucket, flag = ACTIONS[action]
        expected[deal_id] = {"action": action, "bucket": bucket, "flag": flag}

    plans = {
        "advance": (lambda d: open_stages.index(d["stage"]) < len(open_stages) - 1, lambda d: {"stage": open_stages[open_stages.index(d["stage"]) + 1]}),
        "back": (lambda d: open_stages.index(d["stage"]) > 0, lambda d: {"stage": open_stages[open_stages.index(d["stage"]) - 1]}),
        "amount up": (lambda d: d["amount"], lambda d: {"amount": max(d["amount"] + 500, float(round(d["amount"] * rng.uniform(1.2, 1.6) / 500) * 500))}),
        "amount down": (lambda d: d["amount"] and d["amount"] > 2000,
                        lambda d: {"amount": min(d["amount"] - 500, float(round(d["amount"] * rng.uniform(0.5, 0.8) / 500) * 500))}),
        "push": (lambda d: d["close_date"], lambda d: {"close_date": (date.fromisoformat(d["close_date"]) + timedelta(days=rng.randint(21, 60))).isoformat()}),
        "pull": (lambda d: d["close_date"] and date.fromisoformat(d["close_date"]) > today + timedelta(days=20),
                 lambda d: {"close_date": (date.fromisoformat(d["close_date"]) - timedelta(days=rng.randint(7, 14))).isoformat()}),
        "won": (lambda d: True, lambda d: {"stage": won}),
        "lost": (lambda d: True, lambda d: {"stage": lost}),
        "owner": (lambda d: len(owners) > 1, lambda d: {"owner_id": next(o for o in owners if o != d["owner_id"])}),
        "forecast": (lambda d: d.get("forecast_category") in ("Pipeline", "Best case"),
                     lambda d: {"forecast_category": "Best case" if d["forecast_category"] == "Pipeline" else "Commit"}),
    }
    for action, (fits, change) in plans.items():
        for deal in take(action, fits):
            updates.append((deal["id"], change(deal)))
            expect(deal["id"], action)
    for deal in take("delete", lambda d: True):
        deletes.append(deal["id"])
        expect(deal["id"], "delete")
    started_at = store.server_now(info["meta"])
    if updates:
        client.update(updates)
    if deletes:
        client.delete(deletes)
    specs = _fit(info, [dict(_spec(rng, name, open_stages, owners, today), pipeline=pipeline)
                        for name in _names(rng, ACTIONS["new"][0], {d["name"] for d in info["deals"]})])
    _attach_accounts(client, state, specs)
    for deal_id in client.create(specs):
        if deal_id:
            state["ids"].append(deal_id)
            expect(deal_id, "new")
    state["rounds"].append({"started_at": store.stamp(started_at), "expected": expected})
    _save(folder, state)
    return expected


def verify(folder, round_index=-1):
    """Check the latest sync against what a seed round recorded. Returns a list of mismatches (empty means all good)."""
    state = _load(folder)
    ds = timeline.load(folder)
    seed_round = state["rounds"][round_index]
    start = store.parse(seed_round["started_at"]) - timedelta(seconds=1)
    result = compare(ds, start, store.parse(ds.sync["synced_at"]))
    got = {c.deal_id: c for c in result.changes}
    problems = []
    for deal_id, want in seed_round["expected"].items():
        change = got.get(deal_id)
        if want["bucket"] and (not change or change.bucket != want["bucket"]):
            problems.append(f"{deal_id} ({want['action']}): expected bucket {want['bucket']}, got {change.bucket if change else None}")
        if want["flag"] and (not change or want["flag"] not in change.flags):
            problems.append(f"{deal_id} ({want['action']}): expected flag {want['flag']}, got {change.flags if change else []}")
        if not want["bucket"] and change and change.bucket:
            problems.append(f"{deal_id} ({want['action']}): expected no bucket, got {change.bucket}")
    return problems
