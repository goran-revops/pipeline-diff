"""HubSpot: deals with their property history, pipelines, and owners. Write calls exist only for the seed script."""

import time
from email.utils import parsedate_to_datetime

import httpx

from pipeline_diff import store
from pipeline_diff.config import redact
from pipeline_diff.crm import CRMError

API = "https://api.hubapi.com"
PROPERTIES = ["dealname", "pipeline", "dealstage", "amount", "closedate", "hubspot_owner_id", "createdate", "deal_currency_code",
              "hs_manual_forecast_category"]
HISTORY = {"dealstage": "stage", "amount": "amount", "closedate": "close_date", "hubspot_owner_id": "owner_id", "pipeline": "pipeline",
           "hs_manual_forecast_category": "forecast_category"}
TO_HUBSPOT = {"name": "dealname", "stage": "dealstage", "amount": "amount", "close_date": "closedate", "owner_id": "hubspot_owner_id",
              "pipeline": "pipeline", "forecast_category": "hs_manual_forecast_category"}
FORECAST = {"OMIT": "Omitted", "PIPELINE": "Pipeline", "BEST_CASE": "Best case", "COMMIT": "Commit", "CLOSED": "Closed"}


def _convert(field, value):
    if value in (None, ""):
        return None
    if field == "amount":
        try:
            return float(value)
        except ValueError:
            return None
    if field == "forecast_category":
        return FORECAST.get(str(value), str(value))
    return str(value)[:10] if field == "close_date" else str(value)


class HubSpot:
    name = "hubspot"

    server_time = None

    def __init__(self, token, transport=None):
        if not token:
            raise CRMError("Set PIPELINE_DIFF_HUBSPOT_TOKEN to a HubSpot Service Key or private app token")
        if not (token.isascii() and token.isprintable()):
            raise CRMError("PIPELINE_DIFF_HUBSPOT_TOKEN has a character that is not plain text, often an invisible one pasted with it. Paste it again.")
        self.http = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport)

    def request(self, method, path, **kwargs):
        for attempt in range(6):
            try:
                response = self.http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise CRMError(f"HubSpot {method} {path} failed: {type(exc).__name__}") from None
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 5:
                time.sleep(min(float(response.headers.get("Retry-After") or 2 ** attempt), 30))
                continue
            if response.headers.get("Date"):
                self.server_time = parsedate_to_datetime(response.headers["Date"])  # the CRM's clock, not this machine's
            if response.status_code >= 400:
                raise CRMError(f"HubSpot {method} {path} returned {response.status_code}: {redact(response.text)[:300]}")
            return response.json() if response.content else {}

    def pages(self, path, params):
        params = dict(params)
        while True:
            body = self.request("GET", path, params=params)
            yield from body.get("results", [])
            after = body.get("paging", {}).get("next", {}).get("after")
            if not after:
                return
            params["after"] = after

    def fetch(self):
        try:
            account = self.request("GET", "/account-info/v3/details")
        except CRMError:
            account = {}  # without this call there are no deal links; the sync still works
        started = self.server_time  # the first answer's clock: anything changed after it belongs to the next sync
        link = f"https://{account.get('uiDomain') or 'app.hubspot.com'}/contacts/{account['portalId']}/record/0-3/" if account.get("portalId") else None
        currency = account.get("companyCurrency")
        deals, history, forecast = [], [], False
        params = {"limit": 50, "properties": ",".join(PROPERTIES), "propertiesWithHistory": ",".join(HISTORY)}
        for archived in ("false", "true"):
            for row in self.pages("/crm/v3/objects/deals", {**params, "archived": archived}):
                props = row.get("properties", {})
                forecast = forecast or "hs_manual_forecast_category" in props  # HubSpot leaves out properties a portal does not have
                deals.append({
                    "id": row["id"], "name": props.get("dealname") or f"Deal {row['id']}",
                    **{field: _convert(field, props.get(hs)) for hs, field in HISTORY.items()},
                    "created_at": store.stamp(store.parse(props.get("createdate") or row["createdAt"])),
                    "deleted_at": store.stamp(store.parse(row["archivedAt"])) if row.get("archived") and row.get("archivedAt") else None,
                    "currency": props.get("deal_currency_code") or currency, "url": link + row["id"] if link else None,
                })
                for hs, field in HISTORY.items():
                    for change in reversed(row.get("propertiesWithHistory", {}).get(hs, [])):  # HubSpot lists newest first
                        history.append({"deal_id": row["id"], "field": field, "value": _convert(field, change.get("value")),
                                        "changed_at": store.stamp(store.parse(change["timestamp"])), "source": change.get("sourceType", "")})
        if not forecast:
            for deal in deals:
                deal.pop("forecast_category", None)  # no property, no field: nothing to compare or show
        pipelines = [{
            "id": p["id"], "label": p["label"],
            "stages": [{"id": s["id"], "label": s["label"], "order": s.get("displayOrder", 0),
                        "is_closed": str(s["metadata"].get("isClosed")).lower() == "true",
                        "is_won": str(s["metadata"].get("isClosed")).lower() == "true" and float(s["metadata"].get("probability") or 0) >= 1}
                       for s in p["stages"]],
        } for p in self.request("GET", "/crm/v3/pipelines/deals").get("results", [])]
        owners = [{"id": str(o["id"]), "name": f"{o.get('firstName') or ''} {o.get('lastName') or ''}".strip() or o.get("email", ""),
                   "email": o.get("email", "")} for o in self.pages("/crm/v3/owners", {"limit": 100})]
        return {"deals": deals, "history": history, "pipelines": pipelines, "owners": owners,
                "meta": {"source": "hubspot", "currency": currency, "portal": account.get("portalId"),
                         "server_time": store.stamp(started) if started else None, "forecast": forecast}}

    # Seed script only: these write to the CRM.

    def account(self):
        details = self.request("GET", "/account-info/v3/details")
        kind = details.get("accountType", "unknown")
        return {"id": str(details.get("portalId")), "kind": kind, "test": kind in ("DEVELOPER_TEST", "SANDBOX")}

    @staticmethod
    def _props(fields):
        props = {TO_HUBSPOT[k]: v for k, v in fields.items() if k in TO_HUBSPOT}
        if "closedate" in props:
            props["closedate"] = f"{props['closedate']}T12:00:00Z"  # noon UTC keeps the same calendar day in every time zone
        if "amount" in props:
            props["amount"] = str(props["amount"])
        if "hs_manual_forecast_category" in props:
            value = props["hs_manual_forecast_category"]
            props["hs_manual_forecast_category"] = {v: k for k, v in FORECAST.items()}.get(value, value)
        return props

    def create(self, deals):
        ids = []
        for i in range(0, len(deals), 100):
            body = self.request("POST", "/crm/v3/objects/deals/batch/create", json={"inputs": [{"properties": self._props(d)} for d in deals[i:i + 100]]})
            by_name = {r["properties"]["dealname"]: r["id"] for r in body.get("results", [])}
            ids += [by_name.get(d["name"]) for d in deals[i:i + 100]]
        return ids

    def update(self, changes):
        for i in range(0, len(changes), 100):
            self.request("POST", "/crm/v3/objects/deals/batch/update",
                         json={"inputs": [{"id": deal_id, "properties": self._props(fields)} for deal_id, fields in changes[i:i + 100]]})

    def delete(self, ids):
        for i in range(0, len(ids), 100):
            self.request("POST", "/crm/v3/objects/deals/batch/archive", json={"inputs": [{"id": deal_id} for deal_id in ids[i:i + 100]]})
