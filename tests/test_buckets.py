from datetime import date

import pytest

from conftest import END, START
from pipeline_diff.buckets import Scope, compare
from pipeline_diff.timeline import state_at

Q4 = Scope(window=(date(2026, 10, 1), date(2026, 12, 31)), label="Q4 2026")


def buckets(result):
    return {c.deal_id: c.bucket for c in result.changes if c.bucket}


def check_invariant(result):
    assert result.start_total + sum(v for _, v in result.totals().values()) == pytest.approx(result.end_total)


def test_state_at_rebuilds_the_past(dataset):
    assert state_at(dataset, "up", START)["amount"] == 200
    assert state_at(dataset, "up", END)["amount"] == 250
    assert state_at(dataset, "new", START) is None
    assert state_at(dataset, "removed", END) is None
    assert state_at(dataset, "reopened", START)["is_closed"] is True


def test_every_bucket_on_all_open_deals(dataset):
    result = compare(dataset, START, END)
    assert buckets(result) == {
        "up": "amount up", "down": "amount down", "won": "won", "lost": "lost", "removed": "removed",
        "new": "new", "reopened": "reopened", "quick_win": "won",
    }
    values = {c.deal_id: c.value for c in result.changes if c.bucket}
    assert values["up"] == 50 and values["down"] == -100 and values["won"] == -400 and values["new"] == 700
    assert values["quick_win"] == 0
    quick = next(c for c in result.changes if c.deal_id == "quick_win")
    assert quick.parts == [("new", 50), ("won", -50)] and result.totals()["new"] == (2, 750)
    check_invariant(result)


def test_a_deal_that_changed_amount_and_closed_shows_both(tmp_path):
    from conftest import DEALS, write_dataset
    from pipeline_diff import timeline

    changed = {**DEALS, "won": (*DEALS["won"][:3], DEALS["won"][3] + [("2026-09-03", "amount", 450)])}
    ds = timeline.load(write_dataset(tmp_path / "ds", changed))
    won = next(c for c in compare(ds, START, END).changes if c.deal_id == "won")
    assert won.bucket == "won" and won.parts == [("amount up", 50), ("won", -450)] and won.value == -400


def test_a_close_date_window_adds_pushed_and_pulled(dataset):
    result = compare(dataset, START, END, Q4)
    got = buckets(result)
    assert got["pushed"] == "pushed out" and got["pulled"] == "pulled in"
    assert "won" not in got  # it closed on 2026-09-30, before the window
    check_invariant(result)


def test_an_owner_filter_moves_reassigned_deals(dataset):
    result = compare(dataset, START, END, Scope(owners=frozenset({"o1"})))
    assert buckets(result)["owner"] == "moved out"
    check_invariant(compare(dataset, START, END, Scope(owners=frozenset({"o2"}))))


def test_flags(dataset):
    result = compare(dataset, START, END)
    flags = {c.deal_id: c.flags for c in result.changes}
    assert "past due" in flags["slipped"]
    assert "stalled" in flags["same"]
    assert "stage forward" in flags["forward"]
    assert "owner changed" in flags["owner"]
    assert "close date pushed" in flags["pushed"] and "close date pulled" in flags["pulled"]
    assert "stalled" not in flags.get("new", [])


def test_the_invariant_holds_for_every_range(dataset):
    from datetime import timedelta

    for days in range(0, 60, 3):
        for scope in (Scope(), Q4, Scope(owners=frozenset({"o2"}))):
            check_invariant(compare(dataset, START - timedelta(days=days), END, scope))
