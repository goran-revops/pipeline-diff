from datetime import date

from fake_crm import FakeCRM
from pipeline_diff import seed, store, timeline
from pipeline_diff.cli import main, sync_client


def test_a_seed_round_lands_every_change_in_its_bucket(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    seed.baseline(crm, folder, count=80, today=date(2026, 9, 1))
    sync_client(crm, folder)
    for _ in range(2):
        expected = seed.change_round(crm, folder, today=date(2026, 9, 1))
        sync_client(crm, folder)
        assert seed.verify(folder) == []
    actions = {v["action"] for v in expected.values()}
    assert {"advance", "won", "lost", "delete", "new", "owner", "push", "pull", "amount up", "forecast"} <= actions


def test_seed_only_changes_deals_it_created(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    theirs = crm.create([{"name": "Existing - Deal", "stage": "discovery", "amount": 100.0, "close_date": "2026-12-01", "owner_id": "o1"}])
    seed.baseline(crm, folder, count=60, today=date(2026, 9, 1))
    seed.change_round(crm, folder, today=date(2026, 9, 1))
    assert len([h for h in crm.history if h["deal_id"] == theirs[0]]) == 6  # only its creation lines, never a change


def test_a_deleted_deal_is_kept_with_its_history(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([{"name": "Gone - Deal", "stage": "discovery", "amount": 100.0, "close_date": "2026-12-01", "owner_id": "o1"}])
    sync_client(crm, folder)
    crm.delete([deal_id])
    info = sync_client(crm, folder)
    ds = timeline.load(folder)
    assert ds.deals[deal_id]["deleted_at"] == info["synced_at"]
    assert timeline.state_at(ds, deal_id, store.parse(info["synced_at"])) is None
    assert ds.history[(deal_id, "amount")]


def test_a_change_without_a_history_line_gets_one_at_sync_time(tmp_path):
    crm, folder = FakeCRM(), tmp_path / "fake"
    [deal_id] = crm.create([{"name": "Quiet - Deal", "stage": "discovery", "amount": 100.0, "close_date": "2026-12-01", "owner_id": "o1"}])
    sync_client(crm, folder)
    crm.deals[deal_id]["owner_id"] = "o2"  # a field the CRM does not track history for
    info = sync_client(crm, folder)
    lines = [h for h in store.read_jsonl(folder / "history.jsonl") if h["deal_id"] == deal_id and h["field"] == "owner_id"]
    assert lines[-1] == {"deal_id": deal_id, "field": "owner_id", "value": "o2", "changed_at": info["synced_at"], "source": "snapshot"}


def test_seed_refuses_without_the_test_account_flag(capsys):
    assert main(["seed", "hubspot"]) == 2
    assert "test account" in capsys.readouterr().err
