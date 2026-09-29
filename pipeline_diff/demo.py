"""A generated demo source: years of believable pipeline history with invented companies and reps, no CRM needed."""

import random
import shutil
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from pipeline_diff import fake, store


def _pipeline(pid, label, open_stages, won, lost):
    stages, n = [*open_stages, won, lost], len(open_stages)

    def default(i):
        """The category a stage usually means: early stages Pipeline, late ones Best case, the last open one Commit."""
        if i >= n:
            return "Closed" if i == n else "Omitted"
        return "Commit" if i == n - 1 else "Best case" if i >= n - 3 and i > 0 else "Pipeline"
    return {"id": pid, "label": label, "stages": [
        {"id": s, "label": name, "order": i, "is_closed": i >= n, "is_won": (s, name) == won, "forecast": default(i)}
        for i, (s, name) in enumerate(stages)]}


PIPELINES = [
    _pipeline("default", "New business", [
        ("appointmentscheduled", "Appointment scheduled"), ("qualifiedtobuy", "Qualified to buy"),
        ("presentationscheduled", "Presentation scheduled"), ("decisionmakerboughtin", "Decision maker bought in"),
        ("contractsent", "Contract sent")], ("closedwon", "Closed won"), ("closedlost", "Closed lost")),
    _pipeline("renewals", "Renewals", [
        ("renewalupcoming", "Renewal upcoming"), ("renewalquoted", "Quote sent"), ("renewalnegotiation", "In negotiation")],
        ("renewalsigned", "Renewed"), ("renewalchurned", "Churned")),
]
OPEN = {p["id"]: [s["id"] for s in p["stages"] if not s["is_closed"]] for p in PIPELINES}
WON = {p["id"]: next(s["id"] for s in p["stages"] if s["is_won"]) for p in PIPELINES}
LOST = {p["id"]: next(s["id"] for s in p["stages"] if s["is_closed"] and not s["is_won"]) for p in PIPELINES}
SNAPSHOT_WEEKS = 12
CATEGORIES = store.FORECAST[:4]
USUAL = {s["id"]: s["forecast"] for p in PIPELINES for s in p["stages"]}  # each stage's usual forecast category


def generate(folder, days=730, seed=7, now=None, reps=12):
    """Write a demo source to folder and return its sync info. A new demo replaces the old one, snapshots included."""
    shutil.rmtree(Path(folder) / "snapshots", ignore_errors=True)
    rng = random.Random(seed)
    now = (now or store.now()).replace(microsecond=0)
    owners = fake.reps(reps)
    deals, history, open_deals, later, used = {}, [], {}, {}, {}

    def record(deal, field, value, when):
        deal[field] = value
        history.append({"deal_id": deal["id"], "field": field, "value": value, "changed_at": store.stamp(when), "source": "demo"})

    def moment(day):
        return datetime.combine(day, time(rng.randint(8, 17), rng.randint(0, 59)), tzinfo=timezone.utc)

    def schedule(day, event):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        later.setdefault(day, []).append(event)

    def fresh_name(day):
        """A deal name not used in the past year, so names rarely repeat within a view."""
        for _ in range(60):
            name = fake.deal_name(rng)
            if name not in used or (day - used[name]).days > 365:
                break
        used[name] = day
        return name

    def create(day, owner_id, pipeline, name, amount, close_in):
        when = moment(day)
        if when >= now:
            return None
        deal_id = f"D-{len(deals) + 1001}"
        deal = {"id": deal_id, "name": name, "created_at": store.stamp(when), "deleted_at": None,
                "url": f"https://crm.example.com/deals/{deal_id}"}
        deals[deal_id] = open_deals[deal_id] = deal
        stages = OPEN[pipeline]
        for field, value in (("pipeline", pipeline), ("stage", stages[0] if pipeline == "renewals" else rng.choice(stages[:2])),
                             ("amount", amount), ("close_date", (day + timedelta(days=close_in)).isoformat()), ("owner_id", owner_id)):
            record(deal, field, value, when)
        record(deal, "forecast_category", USUAL[deal["stage"]], when)
        return deal_id

    def close(deal, day, when, won):
        record(deal, "stage", (WON if won else LOST)[deal["pipeline"]], when)
        record(deal, "forecast_category", "Closed" if won else "Omitted", when)
        if deal["close_date"] != day.isoformat():
            record(deal, "close_date", day.isoformat(), when)
        open_deals.pop(deal["id"], None)
        if won and rng.random() < (0.35 if deal["pipeline"] == "default" else 0.6):
            renewal_day = day + timedelta(days=rng.randint(300, 335))
            base, base_name = deal.get("base_amount", deal["amount"]), deal.get("base_name", deal["name"])
            schedule(renewal_day, ("renewal", deal["owner_id"], f"{base_name} renewal",
                                   float(round(base * rng.uniform(0.9, 1.1) / 500) * 500), base, base_name))
        elif not won and deal["pipeline"] == "default" and rng.random() < 0.03:
            schedule(day + timedelta(days=rng.randint(30, 180)), ("reopen", deal["id"]))

    first = (now - timedelta(days=days)).date()
    snapshots_from = now - timedelta(weeks=SNAPSHOT_WEEKS)
    for offset in range(days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() == 0:
            snapshot = datetime.combine(day, time(6, 0), tzinfo=timezone.utc)
            if snapshots_from <= snapshot < now:
                # Only the file's time is read back; open deals keep it small.
                store.write_jsonl(folder / "snapshots" / f"{store.stamp(snapshot).replace(':', '_')}.jsonl",
                                  [{k: v for k, v in d.items() if not k.startswith("base_")} for d in open_deals.values()])
        if day.weekday() >= 5:
            continue
        created_today = set()
        for owner in owners:
            if rng.random() < 0.42:
                created_today.add(create(day, owner["id"], "default", fresh_name(day), fake.amount(rng), rng.randint(30, 120)))
        for event in later.pop(day, []):
            if event[0] == "renewal":
                _, owner_id, name, amount, base, base_name = event
                renewal_id = create(day, owner_id, "renewals", name, amount, rng.randint(30, 60))
                if renewal_id:
                    deals[renewal_id].update(base_amount=base, base_name=base_name)
                created_today.add(renewal_id)
            else:
                deal = deals[event[1]]
                when = moment(day)
                if deal["deleted_at"] or deal["stage"] != LOST["default"] or when >= now:
                    continue
                record(deal, "stage", OPEN["default"][1], when)
                record(deal, "forecast_category", "Pipeline", when)
                record(deal, "close_date", (day + timedelta(days=rng.randint(30, 60))).isoformat(), when)
                open_deals[deal["id"]] = deal
                created_today.add(deal["id"])
        for deal in list(open_deals.values()):
            if deal["id"] in created_today:
                continue
            when = moment(day)
            if when < now:
                _step(rng, deal, day, when, record, close, open_deals, owners)

    for deal in deals.values():
        deal.pop("base_amount", None)
        deal.pop("base_name", None)
    info = store.save(folder, {"deals": list(deals.values()), "history": history, "pipelines": PIPELINES, "owners": owners}, now,
                      {"source": "demo", "currency": "USD", "note": "Generated demo data. Every company, rep, and deal is invented."})
    return info


def _step(rng, deal, day, when, record, close, open_deals, owners):
    """One working day in the life of an open deal."""
    stages = OPEN[deal["pipeline"]]
    index, last = stages.index(deal["stage"]), len(stages) - 1
    renewal = deal["pipeline"] == "renewals"
    roll = rng.random()
    if roll < 0.0008:
        deal["deleted_at"] = store.stamp(when)
        open_deals.pop(deal["id"])
        return
    if renewal:
        win, lose = (0.06 if index == last else 0), 0.003
    else:
        win, lose = (0.03 + (index - 3) * 0.08 if index >= last - 1 else 0), 0.010 + index * 0.003
    if roll < win:
        close(deal, day, when, True)
        return
    if roll < win + lose:
        close(deal, day, when, False)
        return
    if roll < win + lose + 0.07 and index < last:
        record(deal, "stage", stages[index + 1], when)
        if rng.random() < 0.8:  # reps update the category most of the time, not always
            record(deal, "forecast_category", USUAL[deal["stage"]], when)
    elif roll < win + lose + 0.085 and index > 0:
        record(deal, "stage", stages[index - 1], when)
        if rng.random() < 0.6:
            record(deal, "forecast_category", USUAL[deal["stage"]], when)
    elif rng.random() < 0.012:  # a manual call: one step up or down, or out of the forecast
        current = CATEGORIES.index(deal.get("forecast_category") or "Pipeline")
        target = 0 if rng.random() < 0.1 else max(1, min(3, current + rng.choice([-1, 1])))
        if target != current:
            record(deal, "forecast_category", CATEGORIES[target], when)
    if rng.random() < (0.01 if renewal else 0.03):
        record(deal, "amount", float(round(deal["amount"] * rng.choice([0.7, 0.8, 0.9, 1.15, 1.25, 1.5]) / 500) * 500), when)
    close_day = datetime.fromisoformat(deal["close_date"]).date()
    if (close_day < day and rng.random() < 0.25) or rng.random() < 0.008:
        record(deal, "close_date", (max(close_day, day) + timedelta(days=rng.randint(14, 45))).isoformat(), when)
    elif close_day > day + timedelta(days=21) and rng.random() < 0.004:
        record(deal, "close_date", (close_day - timedelta(days=rng.randint(7, 21))).isoformat(), when)
    if rng.random() < 0.002:
        record(deal, "owner_id", rng.choice(owners)["id"], when)
