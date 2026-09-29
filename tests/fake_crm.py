"""An in-memory CRM with the same interface as the real clients, and its own clock, for seed and sync tests."""

import copy
from datetime import datetime, timedelta, timezone

from conftest import OWNERS, PIPELINES
from pipeline_diff import store

FIELDS = ("stage", "amount", "close_date", "owner_id", "pipeline", "forecast_category")


class FakeCRM:
    name = "fake"

    def __init__(self):
        self.clock = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        self.deals, self.history, self.counter = {}, [], 100

    account_info = {"id": "test-1", "kind": "DEVELOPER_TEST", "test": True}

    def account(self):
        return dict(self.account_info)

    def tick(self):
        self.clock += timedelta(minutes=1)
        return store.stamp(self.clock)

    def fetch(self):
        return {"deals": copy.deepcopy(list(self.deals.values())), "history": [h for h in self.history if h["deal_id"] in self.deals], "pipelines": copy.deepcopy(PIPELINES),
                "owners": copy.deepcopy(OWNERS), "meta": {"source": "fake", "currency": "USD", "server_time": self.tick(), "forecast": True}}

    def create(self, specs):
        when, ids = self.tick(), []
        for spec in specs:
            self.counter += 1
            deal_id = str(self.counter)
            self.deals[deal_id] = {"id": deal_id, "name": spec["name"], "created_at": when, "deleted_at": None, "url": None,
                                   **{f: spec.get(f, "default" if f == "pipeline" else None) for f in FIELDS}}
            self.history += [{"deal_id": deal_id, "field": f, "value": self.deals[deal_id][f], "changed_at": when} for f in FIELDS]
            ids.append(deal_id)
        return ids

    def update(self, changes):
        when = self.tick()
        for deal_id, fields in changes:
            for field, value in fields.items():
                self.deals[deal_id][field] = value
                self.history.append({"deal_id": deal_id, "field": field, "value": value, "changed_at": when})

    def delete(self, ids):
        when = self.tick()
        for deal_id in ids:
            self.deals.pop(deal_id)  # like a CRM that stops returning deleted deals; the next sync keeps them as deleted
        return when
