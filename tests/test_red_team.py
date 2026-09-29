"""Cases the final red team found: forecast categories from both CRMs, a hidden Salesforce field, and closing edge cases."""

from datetime import date, datetime, timezone

from conftest import DEALS, END, START, write_dataset
from pipeline_diff import store, timeline
from pipeline_diff.buckets import Scope, compare
from pipeline_diff.crm import CRMError
from pipeline_diff.crm.salesforce import Salesforce
from test_crm import FakeSalesforceClient

Q3 = Scope(window=(date(2026, 7, 1), date(2026, 9, 30)))


class ForecastSalesforce(FakeSalesforceClient):
    RECORDS = {
        **FakeSalesforceClient.RECORDS,
        "FROM Opportunity": [{**FakeSalesforceClient.RECORDS["FROM Opportunity"][0], "ForecastCategoryName": "Commit"}],
        "FROM OpportunityHistory": [
            {**row, "ForecastCategory": value} for row, value in zip(FakeSalesforceClient.RECORDS["FROM OpportunityHistory"],
                                                                     ("Pipeline", "BestCase", "Forecast"))],
    }


class HiddenForecast(FakeSalesforceClient):
    """An org where the integration user cannot see the forecast category."""

    def query_all(self, soql, include_deleted=False):
        if "ForecastCategory" in soql:
            error = Exception("https://example.my.salesforce.com/services/data/v59.0/queryAll/?q=SELECT+Id%2C+Name%2C+...")
            error.content = [{"errorCode": "INVALID_FIELD", "message": "No such column 'ForecastCategoryName' on entity 'Opportunity'"}]
            raise error
        return super().query_all(soql, include_deleted)


def test_salesforce_history_spells_categories_by_api_name():
    data = Salesforce(None, None, None, client=ForecastSalesforce()).fetch()
    lines = [(h["value"], h["changed_at"]) for h in data["history"] if h["field"] == "forecast_category"]
    assert lines == [("Pipeline", "2026-09-01T10:00:00Z"), ("Best case", "2026-09-10T10:00:00Z"), ("Commit", "2026-09-12T10:00:00Z")]
    assert data["deals"][0]["forecast_category"] == "Commit" and data["meta"]["forecast"] is True


def test_a_hidden_forecast_field_does_not_stop_the_sync():
    data = Salesforce(None, None, None, client=HiddenForecast()).fetch()
    assert len(data["deals"]) == 1 and "forecast_category" not in data["deals"][0] and data["meta"]["forecast"] is False
    assert not [h for h in data["history"] if h["field"] == "forecast_category"]


def test_a_salesforce_timeout_stops_the_sync_instead_of_syncing_less():
    class Slow(FakeSalesforceClient):
        def query_all(self, soql, include_deleted=False):
            if "ForecastCategory" in soql:
                error = Exception("https://example.my.salesforce.com/services/data/v59.0/queryAll/")
                error.content = [{"errorCode": "QUERY_TIMEOUT", "message": "Your query request was running for too long."}]
                raise error
            return super().query_all(soql, include_deleted)

    try:
        Salesforce(None, None, None, client=Slow()).fetch()
    except CRMError as problem:
        assert "QUERY_TIMEOUT" in str(problem)
    else:
        raise AssertionError("a timeout must not quietly drop the forecast category")


def test_a_salesforce_error_shows_its_code_not_the_url():
    class Broken(FakeSalesforceClient):
        def query_all(self, soql, include_deleted=False):
            error = Exception("https://example.my.salesforce.com/services/data/v59.0/queryAll/?q=" + "SELECT+" * 80)
            error.content = [{"errorCode": "INVALID_SESSION_ID", "message": "Session expired or invalid"}]
            raise error

    try:
        Salesforce(None, None, None, client=Broken()).fetch()
    except CRMError as problem:
        assert "INVALID_SESSION_ID: Session expired or invalid" in str(problem)
    else:
        raise AssertionError("expected a CRMError")


def test_a_stage_move_that_sets_the_default_category_is_not_a_forecast_call(tmp_path):
    folder = write_dataset(tmp_path / "ds")
    pipelines = store.read_jsonl(folder / "pipelines.jsonl")
    for stage in pipelines[0]["stages"]:
        stage["forecast"] = {"contract": "Commit"}.get(stage["id"], "Pipeline")
    store.write_jsonl(folder / "pipelines.jsonl", pipelines)
    history = store.read_jsonl(folder / "history.jsonl")
    history += [{"deal_id": "forward", "field": "forecast_category", "value": "Pipeline", "changed_at": "2026-08-01T00:00:00Z"},
                {"deal_id": "forward", "field": "forecast_category", "value": "Commit", "changed_at": "2026-09-03T00:00:00Z"},
                {"deal_id": "same", "field": "forecast_category", "value": "Pipeline", "changed_at": "2026-08-01T00:00:00Z"},
                {"deal_id": "same", "field": "forecast_category", "value": "Best case", "changed_at": "2026-09-03T00:00:00Z"}]
    store.write_jsonl(folder / "history.jsonl", history)
    flags = {c.deal_id: c.flags for c in compare(timeline.load(folder), START, END).changes}
    assert "stage forward" in flags["forward"] and "forecast up" not in flags["forward"]  # the CRM set Commit with the stage
    assert "forecast up" in flags["same"]  # a rep's own call, no stage move


def test_a_win_corrected_to_lost_is_judged_by_the_date_before_the_first_close(tmp_path):
    deals = {"corrected": ("2026-08-01", None, "Canyon Forge - Platform", [
        ("2026-08-01", "contract", 900, "2026-12-15", "o1"), ("2026-09-04", "stage", "won"), ("2026-09-04", "close_date", "2026-09-04"),
        ("2026-09-05", "stage", "lost")])}
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **deals}))
    assert not [c for c in compare(ds, START, END, Q3).changes if c.deal_id == "corrected" and c.parts]


def test_a_deal_reopened_and_closed_again_in_range_is_counted(tmp_path):
    deals = {"again": ("2026-07-01", None, "Juniper Freight - Pilot", [
        ("2026-07-01", "contract", 100, "2026-08-15", "o1"), ("2026-08-15", "stage", "won"), ("2026-09-02", "stage", "contract"),
        ("2026-09-03", "amount", 150), ("2026-09-05", "stage", "won")])}
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **deals}))
    change = next(c for c in compare(ds, START, END).changes if c.deal_id == "again")
    assert change.parts == [("reopened", 150), ("won", -150)]


def test_timestamps_parse_the_same_on_every_python():
    for text in ("2026-09-20T10:00:00.1Z", "2026-09-20T10:00:00.12345Z", "2026-09-20T10:00:00.123456789Z", "2026-09-20T10:00:00.000+0000"):
        assert store.parse(text).replace(microsecond=0) == datetime(2026, 9, 20, 10, tzinfo=timezone.utc), text
    assert store.parse("2026-09-20T10:00:00-05:00") == datetime(2026, 9, 20, 15, tzinfo=timezone.utc)


def test_a_deal_closed_reopened_and_closed_again_is_judged_by_its_last_close(tmp_path):
    deals = {"k": ("2026-08-01", None, "Canyon Forge - Platform", [
        ("2026-08-01", "contract", 100, "2027-02-10", "o1"), ("2026-09-03", "stage", "lost"), ("2026-09-04", "stage", "contract"),
        ("2026-09-04", "close_date", "2026-09-10"), ("2026-09-05", "stage", "won")])}
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **deals}))
    change = next(c for c in compare(ds, START, END, Q3).changes if c.deal_id == "k")
    assert change.parts == [("pulled in", 100), ("won", -100)]  # it was planned for Q3 when it was won


def test_a_win_reassigned_afterwards_counts_for_the_rep_who_won_it(tmp_path):
    deals = {"r": ("2026-08-01", None, "Juniper Freight - Pilot", [
        ("2026-08-01", "contract", 100, "2026-10-15", "o1"), ("2026-09-04", "stage", "won"), ("2026-09-06", "owner_id", "o2")])}
    ds = timeline.load(write_dataset(tmp_path / "ds", {**DEALS, **deals}))
    won_by = {o: [c.parts for c in compare(ds, START, END, Scope(owners=frozenset({o}))).changes if c.deal_id == "r"] for o in ("o1", "o2")}
    assert won_by["o1"] == [[("won", -100)]] and won_by["o2"] in ([], [[]])


def test_refreshing_twice_keeps_every_hand_written_summary(tmp_path):
    from pipeline_diff import workspace

    ds = timeline.load(write_dataset(tmp_path / "ds"))
    result = compare(ds, START, END)
    root = workspace.export(ds, result, tmp_path / "ws")
    run = root / "runs" / workspace.run_id(END)
    (run / "summary.md").write_text("MY NOTES")
    workspace.export(ds, result, root, refresh=True)
    workspace.export(ds, result, root, refresh=True)
    assert "MY NOTES" in [p.read_text() for p in run.glob("summary.previous*.md")]


def test_a_range_ending_at_midnight_belongs_to_the_week_it_covers():
    from pipeline_diff import workspace

    assert workspace.run_id(datetime(2026, 9, 28, tzinfo=timezone.utc)) == "2026-W39"  # Monday 00:00 ends the week before
    assert workspace.run_id(datetime(2026, 9, 28, 0, 1, tzinfo=timezone.utc)) == "2026-W40"
