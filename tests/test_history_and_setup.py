"""History that must survive odd CRM behavior, and setup mistakes that must fail safely."""

import os
import subprocess
from datetime import date

import pytest

from conftest import write_dataset
from fake_crm import FakeCRM
from pipeline_diff import config, store, timeline
from pipeline_diff.buckets import compare
from pipeline_diff.cli import main, sync_client
from pipeline_diff.crm import CRMError
from pipeline_diff.crm.salesforce import Salesforce
from pipeline_diff.ranges import resolve
from test_crm import FakeSalesforceClient

DEAL = {"name": "Keystone Fabrication - Pilot", "stage": "discovery", "amount": 100.0, "close_date": "2026-12-01", "owner_id": "o1"}


def at(info):
    return store.parse(info["synced_at"])


def test_a_change_made_while_a_sync_runs_shows_up_in_the_next_sync(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([DEAL])
    first = sync_client(crm, folder)
    fetch = crm.fetch

    def racy_fetch():
        data = fetch()  # the fetch starts at this clock tick...
        crm.update([(deal_id, {"amount": 900.0})])  # ...and the CRM changes before it ends
        return data

    crm.fetch = racy_fetch
    second = sync_client(crm, folder)
    crm.fetch = fetch
    third = sync_client(crm, folder)
    ds = timeline.load(folder)
    assert not [c for c in compare(ds, at(first), at(second)).changes if c.bucket]
    assert [c.bucket for c in compare(ds, at(second), at(third)).changes if c.bucket] == ["amount up"]


def test_a_restored_deal_keeps_its_deleted_period(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([DEAL])
    first = sync_client(crm, folder)
    kept = crm.deals[deal_id]
    crm.delete([deal_id])
    second = sync_client(crm, folder)
    crm.deals[deal_id] = kept  # restored from the recycle bin
    third = sync_client(crm, folder)
    ds = timeline.load(folder)
    assert [c.bucket for c in compare(ds, at(first), at(second)).changes if c.bucket] == ["removed"]
    assert timeline.state_at(ds, deal_id, at(second)) is None and timeline.state_at(ds, deal_id, at(third)) is not None


def test_history_the_crm_stops_returning_is_kept(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([DEAL])
    first = sync_client(crm, folder)
    crm.update([(deal_id, {"amount": 300.0})])
    second = sync_client(crm, folder)
    crm.history = [h for h in crm.history if not (h["deal_id"] == deal_id and h["field"] == "amount" and h["value"] == 100.0)]
    sync_client(crm, folder)
    ds = timeline.load(folder)
    assert timeline.value_at(ds, deal_id, "amount", at(first)) == 100.0
    assert [c.bucket for c in compare(ds, at(first), at(second)).changes if c.bucket] == ["amount up"]


def test_a_stage_removed_from_the_crm_still_describes_the_past(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([DEAL])
    crm.update([(deal_id, {"stage": "lost"})])
    first = sync_client(crm, folder)
    crm.update([(deal_id, {"stage": "proposal"})])
    second = sync_client(crm, folder)
    import fake_crm

    original = fake_crm.PIPELINES
    fake_crm.PIPELINES = [{**original[0], "stages": [s for s in original[0]["stages"] if s["id"] != "lost"]}]
    try:
        sync_client(crm, folder)
    finally:
        fake_crm.PIPELINES = original
    ds = timeline.load(folder)
    assert [c.bucket for c in compare(ds, at(first), at(second)).changes if c.bucket] == ["reopened"]


def test_salesforce_owner_changes_without_field_history_come_from_comparing_syncs(tmp_path):
    client = FakeSalesforceClient()
    client.RECORDS = {**client.RECORDS, "FROM OpportunityFieldHistory": []}
    crm = Salesforce(None, None, None, client=client)
    folder = tmp_path / "sf"
    first = sync_client(crm, folder)
    client.RECORDS = {**client.RECORDS, "FROM Opportunity": [{**client.RECORDS["FROM Opportunity"][0], "OwnerId": "005A"}]}
    client.session.get = staticmethod(lambda *a, **k: type("R", (), {"headers": {"Date": "Mon, 28 Sep 2026 09:00:00 GMT"}})())
    second = sync_client(crm, folder)
    ds = timeline.load(folder)
    assert timeline.value_at(ds, "006A", "owner_id", at(first)) == "005B"
    assert "owner changed" in next(c for c in compare(ds, at(first), at(second)).changes if c.deal_id == "006A").flags


def test_a_damaged_data_file_is_set_aside_and_rebuilt_by_sync(tmp_path, capsys):
    crm, folder = FakeCRM(), tmp_path / "fake"
    crm.create([DEAL])
    sync_client(crm, folder)
    (folder / "deals.jsonl").write_text("not json {{{\n")
    (folder / "sync.json").write_text("{trunc")
    info = sync_client(crm, folder)
    assert info["deals"] == 1 and (folder / "deals.jsonl.bad").exists()
    assert "set aside" in capsys.readouterr().err


def test_links_and_currency_survive_a_sync_that_cannot_read_them(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([DEAL])
    crm.deals[deal_id]["url"] = "https://crm.example.com/deal/1"
    sync_client(crm, folder)
    crm.deals[deal_id]["url"] = None
    info = sync_client(crm, folder)
    assert timeline.load(folder).deals[deal_id]["url"] == "https://crm.example.com/deal/1" and info["currency"] == "USD"


def test_an_end_day_on_a_quarters_last_day_keeps_that_quarter(dataset):
    start, end, scope = resolve(dataset, start_day=date(2026, 9, 1), end_day=date(2026, 9, 30), scope="this-quarter")
    assert scope.label == "Closing in Q3 2026"


@pytest.mark.parametrize("args", [["--since", "99999999d"], ["--end", "0001-01-01"], ["--scope", "2026-12-31:2026-10-01"]])
def test_extreme_and_backwards_ranges_are_one_line_errors(tmp_path, args):
    write_dataset(tmp_path / "fixture")
    with pytest.raises(SystemExit):
        main(["--data-dir", str(tmp_path), "report", "fixture", "--out", str(tmp_path / "out"), *args])


def test_a_dot_env_can_only_set_pipeline_diff_settings(tmp_path, monkeypatch):
    for name in ("HTTPS_PROXY", "SSL_CERT_FILE", "PIPELINE_DIFF_TEST_VALUE"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("HTTPS_PROXY=http://evil.example:8080\nSSL_CERT_FILE=./evil.pem\nexport PIPELINE_DIFF_TEST_VALUE=ok\n")
    config.load_env(env)
    assert "HTTPS_PROXY" not in os.environ and "SSL_CERT_FILE" not in os.environ
    assert os.environ["PIPELINE_DIFF_TEST_VALUE"] == "ok"
    monkeypatch.delenv("PIPELINE_DIFF_TEST_VALUE")


@pytest.mark.parametrize("domain", ["evil.example/x?", "evil.example#", "user@evil.example/", "https://yourorg.my"])
def test_the_salesforce_domain_must_be_a_plain_host_name(domain):
    with pytest.raises(CRMError, match="My Domain name"):
        Salesforce(domain, "key", "secret")


def test_report_and_export_refuse_a_folder_they_did_not_write(tmp_path):
    write_dataset(tmp_path / "fixture")
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("my own instructions")
    with pytest.raises(SystemExit, match="did not write"):
        main(["--data-dir", str(tmp_path), "export", "fixture", "--out", str(project)])
    assert (project / "CLAUDE.md").read_text() == "my own instructions"
    assert main(["--data-dir", str(tmp_path), "report", "fixture", "--out", str(tmp_path / "fresh")]) == 0
    assert main(["--data-dir", str(tmp_path), "report", "fixture", "--out", str(tmp_path / "fresh")]) == 0  # its own folder is fine


def test_the_dashboard_starts_safely(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(subprocess, "call", lambda command, env=None, cwd=None: calls.update(command=command, env=env, cwd=cwd) or 0)
    main(["--data-dir", str(tmp_path), "dashboard", "--headless"])
    command = calls["command"]
    assert command[command.index("--server.address") + 1] == "localhost"
    assert "--server.allowedHosts" in command
    assert calls["env"]["PYTHONSAFEPATH"] == "1" and calls["cwd"].name == "dashboard"
