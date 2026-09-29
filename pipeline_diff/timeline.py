"""What any deal looked like at any moment, rebuilt from its history."""

import bisect
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_diff import store


@dataclass
class Dataset:
    name: str
    deals: dict
    history: dict
    stages: dict
    pipelines: list
    owners: dict
    sync: dict = field(default_factory=dict)
    snapshots: list = field(default_factory=list)

    @property
    def currency(self):
        return self.sync.get("currency") or "USD"

    def owner_name(self, owner_id):
        owner = self.owners.get(str(owner_id)) if owner_id else None
        return owner["name"] if owner else ("Unassigned" if not owner_id else f"Owner {owner_id}")

    def pipeline_label(self, pipeline_id):
        return next((p["label"] for p in self.pipelines if p["id"] == pipeline_id), pipeline_id or "")

    def stage_label(self, stage_id):
        return next((s["label"] for s in self.stages.values() if s["id"] == stage_id), stage_id)

    def changes_of(self, deal_id):
        """Every recorded change of one deal, oldest first: [(when, field, value)]."""
        if not hasattr(self, "_by_deal"):
            self._by_deal = {}
            for (did, name), changes in self.history.items():
                self._by_deal.setdefault(did, []).extend((when, name, value) for when, value in changes)
            for changes in self._by_deal.values():
                changes.sort(key=lambda change: change[0])
        return self._by_deal.get(deal_id, [])

    def settled(self, deal_id, moment):
        """True if the deal was already closed or deleted at the moment and never changed after it, so it looks the same at
        any later moment. Comparisons skip these, which on a CRM with years of history is almost every deal."""
        if not hasattr(self, "_settled_at"):
            self._settled_at = {}
            for did, deal in self.deals.items():
                if deal.get("deleted_periods"):
                    continue
                if deal.get("deleted_at"):
                    self._settled_at[did] = store.parse(deal["deleted_at"])
                    continue
                changes = self.changes_of(did)
                last = {name: value for _, name, value in changes}
                stage = self.stages.get((last.get("pipeline", deal.get("pipeline")), last.get("stage", deal.get("stage"))))
                if stage and stage.get("is_closed"):
                    self._settled_at[did] = max([store.parse(deal["created_at"])] + [when for when, _, _ in changes[-1:]])
        settled_at = self._settled_at.get(deal_id)
        return settled_at is not None and settled_at <= moment


def load(folder):
    folder = Path(folder)
    data = store.load(folder)
    history = {}
    for line in data["history"]:
        history.setdefault((line["deal_id"], line["field"]), []).append((store.parse(line["changed_at"]), line["value"]))
    for changes in history.values():
        changes.sort(key=lambda change: change[0])
    stages = {(p["id"], s["id"]): {**s, "pipeline": p["id"]} for p in data["pipelines"] for s in p["stages"]}
    return Dataset(
        name=folder.name,
        deals={d["id"]: d for d in data["deals"]},
        history=history,
        stages=stages,
        pipelines=data["pipelines"],
        owners={str(o["id"]): o for o in data["owners"]},
        sync=data["sync"],
        snapshots=store.snapshot_times(folder),
    )


def exists_at(deal, moment):
    deleted = deal.get("deleted_at")
    if store.parse(deal["created_at"]) > moment or (deleted and store.parse(deleted) <= moment):
        return False
    return not any(store.parse(gone) <= moment < store.parse(back) for gone, back in deal.get("deleted_periods") or [])


def value_at(ds, deal_id, name, moment):
    """The last recorded value at or before the moment. Before the first record, the first recorded value."""
    changes = ds.history.get((deal_id, name))
    if not changes:
        return ds.deals[deal_id].get(name)
    index = bisect.bisect_right([when for when, _ in changes], moment)
    return changes[max(index - 1, 0)][1]


def state_at(ds, deal_id, moment):
    """The deal as it was at the moment, or None if it did not exist then."""
    deal = ds.deals[deal_id]
    if not exists_at(deal, moment):
        return None
    state = {name: value_at(ds, deal_id, name, moment) for name in store.TRACKED}
    stage = ds.stages.get((state["pipeline"], state["stage"])) or {}
    state.update(
        id=deal_id,
        name=deal.get("name") or deal_id,
        stage_label=stage.get("label", state["stage"]),
        stage_order=stage.get("order", 0),
        stage_forecast=stage.get("forecast"),
        stage_known=bool(stage),
        is_closed=bool(stage.get("is_closed")),
        is_won=bool(stage.get("is_won")),
        amount=float(state["amount"] or 0),
    )
    return state


def last_change(ds, deal_id, name, moment):
    """When the field last changed value at or before the moment (its first record counts)."""
    changes = [c for c in ds.history.get((deal_id, name), []) if c[0] <= moment]
    for index in range(len(changes) - 1, 0, -1):
        if changes[index][1] != changes[index - 1][1]:
            return changes[index][0]
    return changes[0][0] if changes else None
