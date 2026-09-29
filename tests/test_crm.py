"""The CRM clients against recorded response shapes: no network, no keys."""

import httpx
import pytest

from pipeline_diff.crm import CRMError, hubspot
from pipeline_diff.crm.hubspot import HubSpot
from pipeline_diff.crm.salesforce import Salesforce

DATE = "Sun, 27 Sep 2026 09:48:29 GMT"
DEAL = {
    "id": "501", "createdAt": "2026-09-20T10:00:00.000Z",
    "properties": {"dealname": "Brightpath Logistics - Platform", "pipeline": "default", "dealstage": "contractsent", "amount": "25000",
                   "closedate": "2026-11-15T12:00:00Z", "hubspot_owner_id": "77", "createdate": "2026-09-20T10:00:00.000Z", "deal_currency_code": None},
    "propertiesWithHistory": {
        "dealstage": [{"value": "contractsent", "timestamp": "2026-09-25T10:00:00.000Z", "sourceType": "CRM_UI"},
                      {"value": "qualifiedtobuy", "timestamp": "2026-09-20T10:00:00.000Z", "sourceType": "CRM_UI"}],
        "amount": [{"value": "25000", "timestamp": "2026-09-20T10:00:00.000Z"}],
        "closedate": [{"value": "2026-11-15T12:00:00Z", "timestamp": "2026-09-20T10:00:00.000Z"}],
        "hubspot_owner_id": [{"value": "77", "timestamp": "2026-09-20T10:00:00.000Z"}],
        "pipeline": [{"value": "default", "timestamp": "2026-09-20T10:00:00.000Z"}],
    },
}
ARCHIVED = {**DEAL, "id": "502", "archived": True, "archivedAt": "2026-09-26T08:00:00.000Z"}
PIPELINES = {"results": [{"id": "default", "label": "Sales Pipeline", "stages": [
    {"id": "qualifiedtobuy", "label": "Qualified to buy", "displayOrder": 1, "metadata": {"isClosed": "false", "probability": "0.4"}},
    {"id": "contractsent", "label": "Contract sent", "displayOrder": 4, "metadata": {"isClosed": "false", "probability": "0.9"}},
    {"id": "closedwon", "label": "Closed won", "displayOrder": 5, "metadata": {"isClosed": "true", "probability": "1.0"}},
    {"id": "closedlost", "label": "Closed lost", "displayOrder": 6, "metadata": {"isClosed": "true", "probability": "0.0"}},
]}]}


def hubspot_transport(calls):
    def handler(request):
        calls.append(request)
        path, params = request.url.path, request.url.params
        if path == "/account-info/v3/details":
            body = {"portalId": 42, "uiDomain": "app-eu1.hubspot.com", "companyCurrency": "EUR"}
        elif path == "/crm/v3/objects/deals":
            if params.get("archived") == "true":
                body = {"results": [ARCHIVED]}
            elif params.get("after"):
                body = {"results": []}
            else:
                body = {"results": [DEAL], "paging": {"next": {"after": "2"}}}
        elif path == "/crm/v3/pipelines/deals":
            body = PIPELINES
        elif path == "/crm/v3/owners":
            body = {"results": [{"id": "77", "firstName": "Ava", "lastName": "Reyes", "email": "ava.reyes@example.com"}]}
        else:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=body, headers={"Date": DATE})
    return httpx.MockTransport(handler)


def test_hubspot_fetch_turns_responses_into_deals_and_history():
    calls = []
    data = HubSpot("pat-test-token", transport=hubspot_transport(calls)).fetch()
    deals = {d["id"]: d for d in data["deals"]}
    assert deals["501"]["stage"] == "contractsent" and deals["501"]["amount"] == 25000.0 and deals["501"]["close_date"] == "2026-11-15"
    assert deals["501"]["url"] == "https://app-eu1.hubspot.com/contacts/42/record/0-3/501"
    assert deals["502"]["deleted_at"] == "2026-09-26T08:00:00Z"
    stages = [(h["value"], h["changed_at"]) for h in data["history"] if h["deal_id"] == "501" and h["field"] == "stage"]
    assert sorted(stages) == [("contractsent", "2026-09-25T10:00:00Z"), ("qualifiedtobuy", "2026-09-20T10:00:00Z")]
    won = [s for s in data["pipelines"][0]["stages"] if s["is_won"]]
    assert [s["id"] for s in won] == ["closedwon"]
    assert data["owners"] == [{"id": "77", "name": "Ava Reyes", "email": "ava.reyes@example.com"}]
    assert data["meta"]["server_time"] == "2026-09-27T09:48:29Z" and data["meta"]["currency"] == "EUR"
    assert all(c.headers["Authorization"] == "Bearer pat-test-token" for c in calls)


def test_hubspot_waits_out_a_rate_limit(monkeypatch):
    monkeypatch.setattr(hubspot.time, "sleep", lambda seconds: None)
    responses = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"ok": True})])
    client = HubSpot("pat-test-token", transport=httpx.MockTransport(lambda request: next(responses)))
    assert client.request("GET", "/anything") == {"ok": True}


def test_a_hubspot_error_never_shows_the_token(monkeypatch):
    monkeypatch.setenv("PIPELINE_DIFF_HUBSPOT_TOKEN", "pat-secret-123")
    client = HubSpot("pat-secret-123", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad token pat-secret-123")))
    with pytest.raises(CRMError) as error:
        client.request("GET", "/crm/v3/owners")
    assert "pat-secret-123" not in str(error.value) and "401" in str(error.value)


class FakeSalesforceClient:
    sf_instance = "example-dev-ed.develop.my.salesforce.com"
    headers = {}

    class session:
        @staticmethod
        def get(url, headers=None, timeout=None):
            return type("R", (), {"headers": {"Date": DATE}})()

    RECORDS = {
        "FROM Opportunity": [{"Id": "006A", "Name": "Juniper Freight - Support", "StageName": "Negotiation/Review", "Amount": 40000.0,
                              "CloseDate": "2026-11-30", "OwnerId": "005B", "CreatedDate": "2026-09-01T10:00:00.000+0000",
                              "SystemModstamp": "2026-09-25T10:00:00.000+0000", "IsDeleted": False}],
        "FROM OpportunityHistory": [
            {"OpportunityId": "006A", "CreatedDate": "2026-09-01T10:00:00.000+0000", "StageName": "Prospecting", "Amount": 30000.0, "CloseDate": "2026-11-30"},
            {"OpportunityId": "006A", "CreatedDate": "2026-09-10T10:00:00.000+0000", "StageName": "Negotiation/Review", "Amount": 30000.0, "CloseDate": "2026-11-30"},
            {"OpportunityId": "006A", "CreatedDate": "2026-09-12T10:00:00.000+0000", "StageName": "Negotiation/Review", "Amount": 40000.0, "CloseDate": "2026-11-30"},
        ],
        "FROM OpportunityFieldHistory": [
            {"OpportunityId": "006A", "CreatedDate": "2026-09-15T10:00:00.000+0000", "OldValue": "Ava Reyes", "NewValue": "Marcus Chen"},
            {"OpportunityId": "006A", "CreatedDate": "2026-09-15T10:00:00.000+0000", "OldValue": "005A", "NewValue": "005B"},
        ],
        "FROM OpportunityStage": [
            {"ApiName": "Prospecting", "MasterLabel": "Prospecting", "SortOrder": 1, "IsClosed": False, "IsWon": False},
            {"ApiName": "Negotiation/Review", "MasterLabel": "Negotiation/Review", "SortOrder": 8, "IsClosed": False, "IsWon": False},
            {"ApiName": "Closed Won", "MasterLabel": "Closed Won", "SortOrder": 9, "IsClosed": True, "IsWon": True},
        ],
        "FROM User": [{"Id": "005A", "Name": "Ava Reyes", "Email": "ava.reyes@example.com"}, {"Id": "005B", "Name": "Marcus Chen", "Email": "marcus.chen@example.com"}],
    }

    def query_all(self, soql, include_deleted=False):
        key = next(k for k in sorted(self.RECORDS, key=len, reverse=True) if k + " " in soql + " ")
        return {"records": self.RECORDS[key]}

    def query(self, soql):
        return {"records": [{"DefaultCurrencyIsoCode": "USD"}]}


def test_salesforce_fetch_turns_full_state_rows_into_one_line_per_change():
    data = Salesforce(None, None, None, client=FakeSalesforceClient()).fetch()
    lines = sorted((h["field"], h["value"], h["changed_at"]) for h in data["history"])
    assert ("stage", "Prospecting", "2026-09-01T10:00:00Z") in lines
    assert ("stage", "Negotiation/Review", "2026-09-10T10:00:00Z") in lines
    assert ("amount", 40000.0, "2026-09-12T10:00:00Z") in lines
    assert [l for l in lines if l[0] == "amount"] == [("amount", 30000.0, "2026-09-01T10:00:00Z"), ("amount", 40000.0, "2026-09-12T10:00:00Z")]
    assert [l for l in lines if l[0] == "owner_id"] == [("owner_id", "005A", "2026-09-01T10:00:00Z"), ("owner_id", "005B", "2026-09-15T10:00:00Z")]
    assert data["deals"][0]["url"] == "https://example-dev-ed.develop.my.salesforce.com/lightning/r/Opportunity/006A/view"
    assert {o["id"] for o in data["owners"]} == {"005A", "005B"}
    assert data["meta"]["server_time"] == "2026-09-27T09:48:29Z"
