import csv
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import END, START, write_dataset
from pipeline_diff import demo, report, store, timeline, workspace
from pipeline_diff.buckets import compare


def ascii_only(root):
    for path in Path(root).rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert text.isascii(), f"{path} has non-ASCII text"


def test_report_writes_a_summary_and_one_csv_row_per_change(dataset, tmp_path):
    result = compare(dataset, START, END)
    summary_path, csv_path = report.write(dataset, result, tmp_path / "report")
    text = summary_path.read_text()
    assert "| Pipeline at start | | USD " in text and "| - won | 2 | USD -450 |" in text and "| + new | 2 | USD 750 |" in text
    assert "Tidewater Labs - Renewal (Ava Reyes): USD 800" in text
    assert "## Flag: past due (1)" in text
    with csv_path.open() as handle:
        assert len(list(csv.DictReader(handle))) == len(result.changes)
    ascii_only(tmp_path / "report")


def test_the_workspace_passes_the_walk_test(dataset, tmp_path):
    root = workspace.export(dataset, compare(dataset, START, END), tmp_path / "ws")
    entry = (root / "CLAUDE.md").read_text()
    assert len(entry.splitlines()) < 60
    run = workspace.run_id(END)
    assert f"runs/{run}/summary.md" in entry
    summary = (root / "runs" / run / "summary.md").read_text()
    # A cold agent reading CLAUDE.md and one summary can say which deal is past due and whose it is.
    assert "## Flag: past due (1)" in summary and "Bluefin Systems - Pilot" in summary and "Marcus Chen" in summary
    assert (len(entry) + len(summary)) / 4 < 8000
    for folder in ("01_reference", "records", "runs", f"runs/{run}"):
        assert (root / folder / "CONTEXT.md").exists(), folder
    card = next((root / "records" / "deals").glob("bluefin-systems-pilot-*/card.md")).read_text()
    assert "status: open" in card and "owner: \"Marcus Chen\"" in card
    link = summary.split("[Bluefin Systems - Pilot](")[1].split(")")[0]
    assert (root / "runs" / run / link).resolve().exists()
    ascii_only(root)


def test_the_workspace_never_overwrites_notes_or_a_past_run(dataset, tmp_path):
    result = compare(dataset, START, END)
    root = workspace.export(dataset, result, tmp_path / "ws")
    notes = next((root / "records" / "deals").glob("*/notes.md"))
    notes.write_text("Call the CFO on Monday.")
    summary = root / "runs" / workspace.run_id(END) / "summary.md"
    summary.write_text(summary.read_text() + "\nManager note: the push on Oakmont is expected.\n")
    workspace.export(dataset, result, root)
    assert notes.read_text() == "Call the CFO on Monday."
    assert "Manager note" in summary.read_text()


def test_demo_data_is_believable_and_adds_up(tmp_path):
    info = demo.generate(tmp_path / "demo", days=60)
    ds = timeline.load(tmp_path / "demo")
    end = store.parse(info["synced_at"])
    assert len(ds.owners) == 12 and info["deals"] > 60 and len(ds.snapshots) >= 8
    for days in (7, 30):
        result = compare(ds, end - timedelta(days=days), end)
        assert result.start_total + sum(v for _, v in result.totals().values()) == pytest.approx(result.end_total)
        assert result.totals()["new"][0] > 0 and result.totals()["won"][0] > 0
    assert all(o["email"].endswith("@example.com") for o in ds.owners.values())
    ascii_only(tmp_path / "demo")


def test_the_dashboard_renders_every_tab(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    demo.generate(tmp_path / "data" / "demo", days=45)
    write_dataset(tmp_path / "data" / "fixture")
    monkeypatch.setenv("PIPELINE_DIFF_DATA_DIR", str(tmp_path / "data"))
    app = Path(__file__).parent.parent / "pipeline_diff" / "dashboard" / "app.py"
    at = AppTest.from_file(str(app), default_timeout=120).run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == ["Start", "Added", "Won", "Lost", "Pushed out", "Other", "End"]
    for source in ("fixture", "demo"):
        at.sidebar.selectbox[0].set_value(source).run()
        for compare_name in ("Last 7 days", "Last 30 days", "Quarter to date"):
            at.sidebar.selectbox[1].set_value(compare_name).run()
            for scope_name in ("All open deals", "Closing next quarter"):
                at.sidebar.selectbox[2].set_value(scope_name).run()
                assert not at.exception, (source, compare_name, scope_name, at.exception)


def test_the_dashboard_explains_what_to_do_with_no_data(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PIPELINE_DIFF_DATA_DIR", str(tmp_path / "empty"))
    at = AppTest.from_file(str(Path(__file__).parent.parent / "pipeline_diff" / "dashboard" / "app.py")).run()
    assert "pipeline-diff demo" in at.info[0].value


def test_the_closed_line_matches_the_table_when_a_deal_changed_amount_and_closed(tmp_path):
    from conftest import DEALS

    changed = {**DEALS, "won": (*DEALS["won"][:3], DEALS["won"][3] + [("2026-09-03", "amount", 450)])}
    ds = timeline.load(write_dataset(tmp_path / "ds", changed))
    text = report.summary(ds, compare(ds, START, END))
    assert "| + amount up | 2 | USD 100 |" in text and "| - won | 2 | USD -500 |" in text and "2 won (USD 500)" in text  # closed at 450, plus a 50 deal created and won
    rows = report.rows(ds, compare(ds, START, END))
    assert all(not isinstance(r["amount_after"], float) or not r["amount_after"].is_integer() for r in rows)


def test_the_tile_deal_counts_add_up_like_the_money(tmp_path):
    from pipeline_diff import ranges

    info = demo.generate(tmp_path / "demo", days=200)
    ds = timeline.load(tmp_path / "demo")
    end = store.parse(info["synced_at"])
    for scope_text in ("this-quarter", "next-quarter", "all"):
        for days in (7, 30):
            scope = ranges.scope_for(scope_text, end.date())
            result = compare(ds, end - timedelta(days=days), end, scope)
            count = {key: sum(1 for d in ds.deals if scope.contains(timeline.state_at(ds, d, moment)))
                     for key, moment in (("start", end - timedelta(days=days)), ("end", end))}
            n = {b: v[0] for b, v in result.totals().items()}
            entries = n["new"] + n["moved in"] + n["pulled in"] + n["reopened"]
            exits = n["won"] + n["lost"] + n["pushed out"] + n["moved out"] + n["removed"]
            assert count["start"] + entries - exits == count["end"], (scope_text, days)


def test_a_new_demo_replaces_the_old_snapshots(tmp_path):
    first = demo.generate(tmp_path / "demo", days=30, now=store.parse("2026-09-01T12:00:00Z"))
    demo.generate(tmp_path / "demo", days=30, now=store.parse("2026-09-29T12:00:00Z"))
    stamps = [p.stem for p in (tmp_path / "demo" / "snapshots").glob("*.jsonl")]
    assert first["synced_at"].replace(":", "_") not in stamps and max(stamps) == "2026-09-29T12_00_00Z"
