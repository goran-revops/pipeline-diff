"""The waterfall: how the pipeline got from its value at one moment to its value at another, deal by deal."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from pipeline_diff import store
from pipeline_diff.store import FORECAST
from pipeline_diff.timeline import last_change, state_at, value_at

# Waterfall order: what adds, then what removes.
ADDS = ("new", "moved in", "pulled in", "reopened", "amount up")
REMOVES = ("amount down", "pushed out", "moved out", "won", "lost", "removed")
BUCKETS = ADDS + REMOVES


@dataclass
class Scope:
    """Which deals count as pipeline: open, and optionally in these pipelines, for these owners, closing in a window."""

    pipelines: frozenset = frozenset()
    owners: frozenset = frozenset()
    window: tuple = None
    label: str = "All open deals"

    def filtered(self, state):
        return bool(state) and (not self.pipelines or state["pipeline"] in self.pipelines) and (
            not self.owners or str(state["owner_id"]) in self.owners
        )

    def contains(self, state):
        if not self.filtered(state) or state["is_closed"]:
            return False
        return self.window is None or (state["close_date"] is not None and self.window[0] <= _day(state["close_date"]) <= self.window[1])

    def after_window(self, state):
        return self.window is not None and state["close_date"] is not None and _day(state["close_date"]) > self.window[1]


@dataclass
class Change:
    deal_id: str
    name: str
    before: dict
    after: dict
    bucket: str = None
    value: float = 0.0
    flags: list = field(default_factory=list)
    note: str = ""
    parts: list = field(default_factory=list)

    def part(self, bucket):
        """What this deal put in one bucket. A deal can sit in two: an amount change, then the move that took it out."""
        return sum(v for b, v in self.parts if b == bucket)


@dataclass
class Result:
    start: object
    end: object
    scope: Scope
    start_total: float
    end_total: float
    changes: list

    def totals(self):
        """{bucket: (deals, value)} in waterfall order, empty buckets included."""
        out = {bucket: [0, 0.0] for bucket in BUCKETS}
        for change in self.changes:
            for bucket, value in change.parts:
                out[bucket][0] += 1
                out[bucket][1] += value
        return {bucket: tuple(v) for bucket, v in out.items()}

    def flagged(self, flag):
        return [c for c in self.changes if flag in c.flags]


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def compare(ds, start, end, scope=None, stall_days=30, tz=None):
    """Every deal that moved between start and end, plus flags.

    Each deal gets one main bucket: how it entered or left the pipeline, or its amount change if it stayed. A deal that
    left at a different amount than it started with also puts the difference in amount up or down, so won and lost show
    what deals closed at, and a deal created and closed in the range shows in new and in won or lost. The parts always
    add up: start plus every bucket equals end.
    """
    scope = scope or Scope()
    end_day = end.astimezone(tz).date() if tz else end.date()
    changes, start_total, end_total = [], 0.0, 0.0
    for deal_id in ds.deals:
        if ds.settled(deal_id, start):
            continue
        before, after = state_at(ds, deal_id, start), state_at(ds, deal_id, end)
        if before is None and after is None:
            continue
        was, now = scope.contains(before), scope.contains(after)
        start_total += before["amount"] if was else 0
        end_total += after["amount"] if now else 0
        change = Change(deal_id, (after or before)["name"], before, after)
        if was and now:
            _amount_change(change, after["amount"] - before["amount"])
        elif was:
            if after is None:
                change.parts = [("removed", -before["amount"])]
            else:
                _amount_change(change, after["amount"] - before["amount"])
                exit_bucket = ("won" if after["is_won"] else "lost") if after["is_closed"] else (
                    "pushed out" if scope.after_window(after) else "moved out")
                change.parts.append((exit_bucket, -after["amount"]))
        elif now:
            if before is None:
                entry = "new"
            elif before["is_closed"]:
                entry = "reopened"
            else:
                entry = "pulled in" if scope.after_window(before) else "moved in"
            change.parts = [(entry, after["amount"])]
        elif after and after["is_closed"] and (closed_at := _last_close(ds, deal_id, start, end)) and scope.filtered(
                state_at(ds, deal_id, closed_at)) and (scope.window is None or _closed_inside(ds, deal_id, after, closed_at, scope.window)):
            if before is None:
                entry, change.note = "new", "created and closed in range"
            elif before["is_closed"]:
                entry, change.note = "reopened", "reopened and closed again in range"
            else:
                entry = "pulled in" if scope.after_window(before) else "moved in"
                change.note = f"{entry} and closed in range"
            change.parts = [(entry, after["amount"]), ("won" if after["is_won"] else "lost", -after["amount"])]
        if change.parts:
            change.bucket, change.value = change.parts[-1][0], sum(v for _, v in change.parts)
        change.flags = _flags(ds, deal_id, before, after, scope, end, end_day, stall_days, bool(change.parts))
        if change.bucket or change.flags:
            changes.append(change)
    return Result(start, end, scope, start_total, end_total, changes)


def _is_closed(ds, deal_id, moment):
    stage = ds.stages.get((value_at(ds, deal_id, "pipeline", moment), value_at(ds, deal_id, "stage", moment))) or {}
    return bool(stage.get("is_closed"))


def _last_close(ds, deal_id, start, end):
    """When the deal last went from open to closed inside the range, or None. A deal created closed counts as closing then.
    A win later corrected to lost has no open time in between, so its last close is still the win."""
    created = ds.deals[deal_id].get("created_at")
    created = store.parse(created) if created else None
    closes = [when for when, _ in ds.history.get((deal_id, "stage"), []) if start < when <= end and _is_closed(ds, deal_id, when)
              and (not _is_closed(ds, deal_id, when - timedelta(microseconds=1)) or (created and created >= when))]
    if closes:
        return closes[-1]
    if created and start < created <= end and _is_closed(ds, deal_id, created):
        return created  # created already closed, with no stage line of its own
    return None


def _closed_inside(ds, deal_id, after, closed_at, window):
    """Whether a deal that closed in the range belonged to the window. It is judged by the close date it had just before it
    closed, because HubSpot, and many reps, set the close date to the day a deal closes: a deal planned for next quarter
    and won today was never this quarter's pipeline."""
    planned = value_at(ds, deal_id, "close_date", closed_at - timedelta(microseconds=1)) or after["close_date"]
    return bool(planned) and window[0] <= _day(planned) <= window[1]


def _amount_change(change, delta):
    if delta:
        change.parts.append(("amount up" if delta > 0 else "amount down", delta))


def _flags(ds, deal_id, before, after, scope, end, end_day, stall_days, moved):
    """Flags for deals in the scope at either end, or that moved the pipeline. Close date flags only on deals still open."""
    flags = []
    if scope.contains(after):
        if after["close_date"] and _day(after["close_date"]) < end_day:
            flags.append("past due")
        stage_since = last_change(ds, deal_id, "stage", end)
        if stage_since and stage_since <= end - timedelta(days=stall_days):
            flags.append("stalled")
    if before and after and (moved or scope.contains(before) or scope.contains(after)):
        if (after["stage"] != before["stage"] and after["pipeline"] == before["pipeline"] and not after["is_closed"] and not before["is_closed"]
                and before.get("stage_known", True) and after.get("stage_known", True)):
            flags.append("stage forward" if after["stage_order"] > before["stage_order"] else "stage back")
        was_in, now_in = before.get("forecast_category"), after.get("forecast_category")
        by_stage = after["stage"] != before["stage"] and now_in == after.get("stage_forecast")  # the CRM set it with the stage
        if not after["is_closed"] and was_in in FORECAST[:4] and now_in in FORECAST[:4] and was_in != now_in and not by_stage:
            flags.append("forecast up" if FORECAST.index(now_in) > FORECAST.index(was_in) else "forecast down")
        if str(after["owner_id"]) != str(before["owner_id"]):
            flags.append("owner changed")
        if not after["is_closed"] and after["close_date"] and before["close_date"] and after["close_date"] != before["close_date"]:
            flags.append("close date pushed" if _day(after["close_date"]) > _day(before["close_date"]) else "close date pulled")
    return flags
