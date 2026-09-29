"""Turn "last 7 days", "since the previous sync", "next quarter" and the like into exact moments and a scope."""

from datetime import date, datetime, time, timedelta

from pipeline_diff import store
from pipeline_diff.buckets import Scope


def quarter(day, offset=0):
    """(first day, last day, label) of the quarter holding day, moved by offset quarters."""
    index = (day.year * 4 + (day.month - 1) // 3) + offset
    year, q = divmod(index, 4)
    first = date(year, q * 3 + 1, 1)
    last = date(year + (q == 3), (q * 3 + 3) % 12 + 1, 1) - timedelta(days=1)
    return first, last, f"Q{q + 1} {year}"


def latest(ds):
    return store.parse(ds.sync["synced_at"]) if ds.sync.get("synced_at") else store.now()


def previous_sync(ds, end):
    earlier = [t for t in ds.snapshots if t < end - timedelta(seconds=1)]
    return earlier[-1] if earlier else None


def scope_for(text, end_day, pipelines=(), owners=()):
    text = (text or "all").strip().lower()
    extra = {"pipelines": frozenset(pipelines), "owners": frozenset(owners)}
    if text in ("all", "open", ""):
        return Scope(label="All open deals", **extra)
    if text in ("this-quarter", "next-quarter"):
        first, last, label = quarter(end_day, 0 if text == "this-quarter" else 1)
        return Scope(window=(first, last), label=f"Closing in {label}", **extra)
    first, _, last = text.partition(":")
    try:
        window = (date.fromisoformat(first), date.fromisoformat(last))
    except ValueError:
        raise SystemExit(f"scope {text!r} is not all, this-quarter, next-quarter, or YYYY-MM-DD:YYYY-MM-DD") from None
    if window[0] > window[1]:
        raise SystemExit(f"scope {text!r} ends before it starts")
    return Scope(window=window, label=f"Closing {window[0]} to {window[1]}", **extra)


def resolve(ds, since="7d", start_day=None, end_day=None, scope="all", tz=None, pipelines=(), owners=()):
    """(start, end, scope). The end is the latest sync unless an end day is given. Days are read in the time zone."""
    try:
        return _resolve(ds, since, start_day, end_day, scope, tz, pipelines, owners)
    except OverflowError:
        raise SystemExit("that date range is outside the dates pipeline-diff can handle") from None


def _resolve(ds, since, start_day, end_day, scope, tz, pipelines, owners):
    end = latest(ds)
    if end_day:
        end = min(end, datetime.combine(end_day + timedelta(days=1), time(0), tzinfo=tz or end.tzinfo))
    local_end = end.astimezone(tz) if tz else end
    anchor = min(end_day, local_end.date()) if end_day else local_end.date()
    if start_day:
        start = datetime.combine(start_day, time(0), tzinfo=tz or end.tzinfo)
    elif since == "sync":
        start = previous_sync(ds, end) or end - timedelta(days=7)
    elif since == "qtd":
        start = datetime.combine(quarter(anchor)[0], time(0), tzinfo=tz or end.tzinfo)
    else:
        try:
            start = end - timedelta(days=int(str(since).removesuffix("d") or 7))
        except ValueError:
            raise SystemExit(f"since {since!r} is not a number of days like 7d, or qtd, or sync") from None
    if start >= end:
        raise SystemExit(f"the range starts at {store.stamp(start)}, which is not before its end at {store.stamp(end)}")
    return start, end, scope_for(scope, anchor, pipelines, owners)
