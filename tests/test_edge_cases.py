"""Edge cases a correctness review found. Each test fails without its fix."""

import copy
from datetime import date, datetime, timezone

import pytest

from conftest import DEALS, END, START, write_dataset
from fake_crm import FakeCRM
from pipeline_diff import fake, seed, store, timeline, workspace
from pipeline_diff.buckets import Scope, compare
from pipeline_diff.cli import main, sync_client
from pipeline_diff.crm.hubspot import HubSpot
from pipeline_diff.crm.salesforce import Salesforce
from pipeline_diff.ranges import resolve
from test_crm import FakeSalesforceClient, hubspot_transport


def test_repeat_syncs_keep_a_safety_net_line_where_it_was(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([{"name": "Quiet - Deal", "stage": "discovery", "amount": 100.0, "close_date": "2026-12-01", "owner_id": "o1"}])
    crm.deals[deal_id]["amount"] = 500.0  # the CRM holds 500 but its history only ever recorded 100
    first = sync_client(crm, folder)
    sync_client(crm, folder)
    sync_client(crm, folder)
    lines = [h for h in store.read_jsonl(folder / "history.jsonl") if h["deal_id"] == deal_id and h["field"] == "amount"]
    assert [(h["value"], h["changed_at"]) for h in lines if h.get("source") == "snapshot"] == [(500.0, first["synced_at"])]


def test_hubspot_changes_in_the_same_second_keep_their_order(tmp_path):
    import test_crm

    deal = copy.deepcopy(test_crm.DEAL)
    deal["propertiesWithHistory"]["amount"] = [{"value": "25000", "timestamp": "2026-09-20T10:00:00.400Z"},
                                               {"value": "10000", "timestamp": "2026-09-20T10:00:00.100Z"}]
    original = test_crm.DEAL
    test_crm.DEAL = deal
    try:
        client = HubSpot("pat-test-token", transport=hubspot_transport([]))
        sync_client(client, tmp_path / "hs")
    finally:
        test_crm.DEAL = original
    ds = timeline.load(tmp_path / "hs")
    assert timeline.value_at(ds, "501", "amount", datetime(2026, 9, 21, tzinfo=timezone.utc)) == 25000.0


def test_a_salesforce_deal_in_a_retired_stage_keeps_its_closed_and_won_flags():
    client = FakeSalesforceClient()
    client.RECORDS = {**client.RECORDS, "FROM Opportunity": [
        {**client.RECORDS["FROM Opportunity"][0], "StageName": "Signed (old)", "IsClosed": True, "IsWon": True}]}
    data = Salesforce(None, None, None, client=client).fetch()
    stage = next(s for s in data["pipelines"][0]["stages"] if s["id"] == "Signed (old)")
    assert stage["is_closed"] and stage["is_won"]


def test_a_renamed_deal_keeps_its_notes(tmp_path):
    folder = write_dataset(tmp_path / "ds")
    ds = timeline.load(folder)
    root = workspace.export(ds, compare(ds, START, END), tmp_path / "ws")
    notes = next((root / "records" / "deals").glob("*-same/notes.md"))
    notes.write_text("Champion is the VP of Ops.")
    renamed = {**DEALS, "same": ("2026-08-01", None, "Brightpath Logistics - Renewal", DEALS["same"][3])}
    ds = timeline.load(write_dataset(tmp_path / "ds2", renamed))
    workspace.export(ds, compare(ds, START, END), root)
    folders = list((root / "records" / "deals").glob("*-same"))
    assert [f.name for f in folders] == ["brightpath-logistics-platform-same"]  # the first name stays, so old links hold
    assert (folders[0] / "notes.md").read_text() == "Champion is the VP of Ops."
    assert "# Brightpath Logistics - Renewal" in (folders[0] / "card.md").read_text()


def test_a_pipe_in_a_deal_name_does_not_break_tables(tmp_path):
    named = {**DEALS, "same": ("2026-08-01", None, "Acme | Renewal", DEALS["same"][3])}
    ds = timeline.load(write_dataset(tmp_path / "ds", named))
    root = workspace.export(ds, compare(ds, START, END), tmp_path / "ws")
    row = next(line for line in (root / "_index" / "deals.md").read_text().splitlines() if "Acme" in line)
    assert row.replace("\\|", "").count("|") == 7


def test_created_and_closed_deals_respect_the_close_date_window(tmp_path):
    late = {"late_win": ("2026-09-03", None, "Canyon Forge - Platform", [("2026-09-03", "contract", 900, "2027-06-30", "o1"), ("2026-09-05", "stage", "won")])}
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **late}))
    result = compare(ds, START, END, Scope(window=(date(2026, 10, 1), date(2026, 12, 31))))
    assert "late_win" not in {c.deal_id for c in result.changes if c.bucket}


def test_a_range_that_ends_before_it_starts_is_refused(dataset):
    with pytest.raises(SystemExit, match="not before its end"):
        resolve(dataset, start_day=date(2026, 9, 20), end_day=date(2026, 9, 10))


def test_an_unknown_source_is_an_error_not_an_empty_report(tmp_path):
    with pytest.raises(SystemExit, match="no synced data"):
        main(["--data-dir", str(tmp_path), "report", "nosuch", "--out", str(tmp_path / "out")])


def test_a_seed_amount_change_always_changes_the_amount(tmp_path, monkeypatch):
    monkeypatch.setattr(fake, "amount", lambda rng: rng.choice([1000.0, 2500.0]))
    for round_seed in range(12):
        crm, folder = FakeCRM(), tmp_path / f"fake{round_seed}"
        seed.baseline(crm, folder, count=70, today=date(2026, 9, 1))
        sync_client(crm, folder)
        seed.change_round(crm, folder, seed=round_seed, today=date(2026, 9, 1))
        sync_client(crm, folder)
        assert seed.verify(folder) == [], round_seed


@pytest.mark.parametrize("args, message", [(["--since", "abc"], "not a number of days"), (["--scope", "garbage"], "is not all, this-quarter"),
                                           (["--scope", "2026-13-40:2026-12-31"], "is not all, this-quarter")])
def test_bad_options_are_one_line_errors(tmp_path, args, message):
    write_dataset(tmp_path / "fixture")
    with pytest.raises(SystemExit, match=message):
        main(["--data-dir", str(tmp_path), "report", "fixture", "--out", str(tmp_path / "out"), *args])


def test_a_damaged_data_file_names_itself(tmp_path):
    folder = write_dataset(tmp_path / "fixture")
    with (folder / "deals.jsonl").open("a") as handle:
        handle.write("not json {{{\n")
    with pytest.raises(SystemExit, match="deals.jsonl line 15 is not valid JSON"):
        main(["--data-dir", str(tmp_path), "report", "fixture", "--out", str(tmp_path / "out")])


def test_the_dashboard_handles_a_range_with_no_changes_and_a_bad_time_zone(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    write_dataset(tmp_path / "data" / "fixture")
    monkeypatch.setenv("PIPELINE_DIFF_DATA_DIR", str(tmp_path / "data"))
    app = str(Path(__file__).parent.parent / "pipeline_diff" / "dashboard" / "app.py")
    at = AppTest.from_file(app, default_timeout=120).run()
    at.sidebar.selectbox[1].set_value("Custom dates").run()
    at.sidebar.date_input[0].set_value((date(2020, 1, 1), date(2020, 1, 2))).run()
    assert not at.exception
    monkeypatch.setenv("PIPELINE_DIFF_TIMEZONE", "Mars/Olympus")
    at = AppTest.from_file(app, default_timeout=120).run()
    assert not at.exception and "not a time zone" in at.error[0].value


def test_a_close_date_set_when_a_deal_closes_is_not_a_pull_in(tmp_path):
    q3 = Scope(window=(date(2026, 7, 1), date(2026, 9, 30)))
    deals = {
        "early": ("2026-08-01", None, "Canyon Forge - Platform", [("2026-08-01", "contract", 900, "2026-12-15", "o1"),
                                                                 ("2026-09-05", "stage", "won"), ("2026-09-05", "close_date", "2026-09-05")]),
        "pulled": ("2026-08-01", None, "Juniper Freight - Pilot", [("2026-08-01", "contract", 400, "2026-12-15", "o1"),
                                                                   ("2026-09-03", "close_date", "2026-09-20"), ("2026-09-05", "stage", "won")]),
    }
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **deals}))
    got = {c.deal_id: c.parts for c in compare(ds, START, END, q3).changes}
    assert not got.get("early")  # planned for Q4 and won today: never this quarter's pipeline
    assert got["pulled"] == [("pulled in", 400), ("won", -400)]
