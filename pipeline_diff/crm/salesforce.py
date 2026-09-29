"""Salesforce: opportunities, their stage and owner history, stages, and users. Write calls exist only for the seed script."""

import re
import warnings
from email.utils import parsedate_to_datetime

from pipeline_diff import store
from pipeline_diff.config import redact
from pipeline_diff.crm import CRMError

PIPELINE = {"id": "opportunities", "label": "Opportunities"}
TO_SALESFORCE = {"name": "Name", "stage": "StageName", "amount": "Amount", "close_date": "CloseDate", "owner_id": "OwnerId",
                 "forecast_category": "ForecastCategoryName"}


def _amount(value):
    return float(value) if value is not None else None


# Opportunities return the labels; OpportunityHistory returns the API names (BestCase, Forecast for Commit).
FORECAST = {"Omitted": "Omitted", "Pipeline": "Pipeline", "Best Case": "Best case", "BestCase": "Best case", "Commit": "Commit",
            "Forecast": "Commit", "Closed": "Closed"}
TO_FORECAST = {"Best case": "Best Case"}


def _forecast(value):
    return FORECAST.get(value, value) if value else None


def _error(exc):
    """Salesforce's own error code and message, not the long request URL that simple-salesforce puts first."""
    content = getattr(exc, "content", None)

    def one(item):
        if isinstance(item, dict):
            return f"{item.get('errorCode') or item.get('error') or ''}: {item.get('message') or item.get('error_description') or ''}"
        return item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
    text = "; ".join(one(c) for c in content) if isinstance(content, list) and content else one(content) if content else str(exc)
    return redact(text)[:300]


class Salesforce:
    name = "salesforce"

    def __init__(self, domain, consumer_key, consumer_secret, client=None):
        if client is None:
            if not (domain and consumer_key and consumer_secret):
                raise CRMError("Set PIPELINE_DIFF_SF_DOMAIN, PIPELINE_DIFF_SF_CONSUMER_KEY, and PIPELINE_DIFF_SF_CONSUMER_SECRET")
            if not all(v.isascii() and v.isprintable() for v in (domain, consumer_key, consumer_secret)):
                raise CRMError("A Salesforce setting has a character that is not plain text, often an invisible one pasted with it. Paste it again.")
            if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*", domain):
                raise CRMError("PIPELINE_DIFF_SF_DOMAIN must be your My Domain name without https:// or .salesforce.com, for example yourorg.my")
            from simple_salesforce import Salesforce as Client

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")  # its "not approved" warning prints the consumer key
                    client = Client(consumer_key=consumer_key, consumer_secret=consumer_secret, domain=domain)
            except Exception as exc:
                raise CRMError(f"Salesforce login failed: {redact(exc)[:300]}") from None
        self.sf = client

    def query(self, soql):
        try:
            return self.sf.query_all(soql, include_deleted=True)["records"]
        except Exception as exc:
            raise CRMError(f"Salesforce query failed: {_error(exc)}") from None

    def first(self, *queries):
        """The first query whose fields the org has. Orgs can hide a field from the integration user, or not have it at all.
        Any other error, like a timeout or a used-up API limit, stops the sync instead of quietly syncing less."""
        for index, soql in enumerate(queries):
            try:
                return index, self.query(soql)
            except CRMError as problem:
                if index == len(queries) - 1 or "INVALID_FIELD" not in str(problem):
                    raise

    def fetch(self):
        started = self.server_time()  # before any query: anything changed after it belongs to the next sync
        fields = "Id, Name, StageName, Amount, CloseDate, OwnerId, CreatedDate, SystemModstamp, IsDeleted, IsClosed, IsWon"
        # CurrencyIsoCode exists only in multi-currency orgs; the forecast category can be hidden or missing.
        extras = [", CurrencyIsoCode, ForecastCategoryName", ", ForecastCategoryName", ", CurrencyIsoCode", ""]
        used, opportunities = self.first(*(f"SELECT {fields}{extra} FROM Opportunity" for extra in extras))
        forecast = "ForecastCategoryName" in extras[used]
        currency = self.currency()
        history = "SELECT OpportunityId, CreatedDate, StageName, Amount, CloseDate{} FROM OpportunityHistory ORDER BY OpportunityId, CreatedDate, Id"
        used, rows = self.first(*([history.format(", ForecastCategory")] if forecast else []), history.format(""))
        forecast_history = forecast and used == 0
        owner_rows = self.query("SELECT OpportunityId, CreatedDate, OldValue, NewValue FROM OpportunityFieldHistory "
                                "WHERE Field = 'Owner' ORDER BY OpportunityId, CreatedDate, Id")
        _, stages = self.first("SELECT ApiName, MasterLabel, SortOrder, IsClosed, IsWon, ForecastCategoryName FROM OpportunityStage ORDER BY SortOrder",
                               "SELECT ApiName, MasterLabel, SortOrder, IsClosed, IsWon FROM OpportunityStage ORDER BY SortOrder")
        by_opportunity, owners_by = {}, {}
        for row in rows:
            by_opportunity.setdefault(row["OpportunityId"], []).append(row)
        for row in owner_rows:
            # An owner change writes two rows, one with names and one with ids. Keep the id rows (users start with 005).
            if str(row.get("NewValue") or "").startswith("005"):
                owners_by.setdefault(row["OpportunityId"], []).append(row)
        link = f"https://{self.sf.sf_instance}/lightning/r/Opportunity/"
        deals, history = [], []
        for opp in opportunities:
            created = store.stamp(store.parse(opp["CreatedDate"]))
            deals.append({
                "id": opp["Id"], "name": opp["Name"], "pipeline": PIPELINE["id"], "stage": opp["StageName"], "amount": _amount(opp["Amount"]),
                "close_date": opp["CloseDate"], "owner_id": opp["OwnerId"], "created_at": created,
                **({"forecast_category": _forecast(opp.get("ForecastCategoryName"))} if forecast else {}),
                "deleted_at": store.stamp(store.parse(opp["SystemModstamp"])) if opp.get("IsDeleted") else None, "url": link + opp["Id"] + "/view",
                "currency": opp.get("CurrencyIsoCode") or currency,
            })

            def add(field, value, when):
                history.append({"deal_id": opp["Id"], "field": field, "value": value, "changed_at": when, "source": "salesforce"})

            add("pipeline", PIPELINE["id"], created)
            previous = None
            for row in by_opportunity.get(opp["Id"]) or [{"CreatedDate": opp["CreatedDate"], "StageName": opp["StageName"], "Amount": opp["Amount"],
                                                           "CloseDate": opp["CloseDate"], "ForecastCategory": opp.get("ForecastCategoryName")}]:
                values = {"stage": row["StageName"], "amount": _amount(row["Amount"]), "close_date": row["CloseDate"]}
                if forecast_history:
                    values["forecast_category"] = _forecast(row.get("ForecastCategory"))
                when = store.stamp(store.parse(row["CreatedDate"]))
                for field, value in values.items():
                    if previous is None or previous[field] != value:
                        add(field, value, created if previous is None else when)
                previous = values
            changes = owners_by.get(opp["Id"], [])
            if changes and changes[0].get("OldValue"):  # with no field history, the sync-to-sync comparison records owner changes instead
                add("owner_id", changes[0]["OldValue"], created)
            for row in changes:
                add("owner_id", row["NewValue"], store.stamp(store.parse(row["CreatedDate"])))
        owner_ids = sorted({d["owner_id"] for d in deals if d["owner_id"]} | {h["value"] for h in history if h["field"] == "owner_id" and h["value"]})
        owners = []
        for i in range(0, len(owner_ids), 100):
            quoted = ",".join(f"'{owner_id}'" for owner_id in owner_ids[i:i + 100] if owner_id.isalnum())
            owners += [{"id": u["Id"], "name": u["Name"], "email": u.get("Email") or ""} for u in self.query(f"SELECT Id, Name, Email FROM User WHERE Id IN ({quoted})")]
        listed = [{"id": s["ApiName"], "label": s["MasterLabel"], "order": s["SortOrder"], "is_closed": s["IsClosed"], "is_won": s["IsWon"],
                   "forecast": _forecast(s.get("ForecastCategoryName"))} for s in stages]
        known = {s["id"] for s in listed}
        for opp in opportunities:  # a stage renamed or removed since: trust the opportunity's own flags
            if opp["StageName"] not in known:
                known.add(opp["StageName"])
                listed.append({"id": opp["StageName"], "label": opp["StageName"], "order": 999, "is_closed": bool(opp.get("IsClosed")),
                               "is_won": bool(opp.get("IsWon"))})
        pipelines = [{**PIPELINE, "stages": listed}]
        return {"deals": deals, "history": history, "pipelines": pipelines, "owners": owners,
                "meta": {"source": "salesforce", "currency": currency, "server_time": started, "forecast": forecast}}

    def server_time(self):
        """The CRM's clock from a response Date header. This machine's clock can be off by more than a minute."""
        try:
            response = self.sf.session.get(f"https://{self.sf.sf_instance}/services/data/", headers=self.sf.headers, timeout=30)
            return store.stamp(parsedate_to_datetime(response.headers["Date"]))
        except Exception:
            return None

    def currency(self):
        try:
            return self.sf.query("SELECT DefaultCurrencyIsoCode FROM Organization")["records"][0]["DefaultCurrencyIsoCode"] or "USD"
        except Exception:
            return "USD"

    # Seed script only: these write to the CRM.

    def account(self):
        org = self.sf.query("SELECT Id, OrganizationType, IsSandbox FROM Organization")["records"][0]
        return {"id": org["Id"], "kind": "a sandbox" if org["IsSandbox"] else org["OrganizationType"],
                "test": bool(org["IsSandbox"]) or org["OrganizationType"] == "Developer Edition"}

    def assignable_owners(self):
        """Active users with a full Salesforce license, the only ones who can own opportunities."""
        return [u["Id"] for u in self.query("SELECT Id FROM User WHERE IsActive = true AND UserType = 'Standard' "
                                            "AND Profile.UserLicense.Name = 'Salesforce' ORDER BY CreatedDate")]

    def _collection(self, method, records=None, path="composite/sobjects"):
        try:
            results = self.sf.restful(path, method=method, json={"allOrNone": True, "records": records} if records is not None else None)
        except Exception as exc:
            raise CRMError(f"Salesforce {method} failed: {redact(exc)[:300]}") from None
        failed = [r for r in results or [] if not r.get("success")]
        if failed:
            raise CRMError(f"Salesforce {method} failed: {redact(failed[0].get('errors'))[:300]}")
        return [r.get("id") for r in results or []]

    @staticmethod
    def _fields(fields):
        return {TO_SALESFORCE[k]: (TO_FORECAST.get(v, v) if k == "forecast_category" else v) for k, v in fields.items() if k in TO_SALESFORCE}

    def create_accounts(self, names_and_sites):
        ids = []
        for i in range(0, len(names_and_sites), 200):
            ids += self._collection("POST", [{"attributes": {"type": "Account"}, "Name": n, "Website": site} for n, site in names_and_sites[i:i + 200]])
        return ids

    def create(self, deals):
        ids = []
        for i in range(0, len(deals), 200):
            records = [{"attributes": {"type": "Opportunity"}, **self._fields(d),
                        **({"AccountId": d["account_id"]} if d.get("account_id") else {})} for d in deals[i:i + 200]]
            ids += self._collection("POST", records)
        return ids

    def update(self, changes):
        for i in range(0, len(changes), 200):
            self._collection("PATCH", [{"attributes": {"type": "Opportunity"}, "id": deal_id, **self._fields(fields)}
                                       for deal_id, fields in changes[i:i + 200]])

    def delete(self, ids):
        for i in range(0, len(ids), 200):
            self._collection("DELETE", path=f"composite/sobjects?ids={','.join(ids[i:i + 200])}&allOrNone=true")
