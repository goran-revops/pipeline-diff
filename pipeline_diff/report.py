"""summary.md (the waterfall in words, for Slack or email) and changes.csv (one row per changed deal)."""

import csv
import re
from pathlib import Path

from pipeline_diff.buckets import ADDS, BUCKETS

FLAG_ORDER = ("past due", "stalled", "close date pushed", "close date pulled", "forecast down", "forecast up", "stage forward", "stage back",
              "owner changed")


def plain(value):
    """A CRM name made safe for Markdown: one line, and no link, image, HTML, or code syntax."""
    text = " ".join(str("" if value is None else value).split())
    for ch in "\\`[]<>$&*_~":
        text = text.replace(ch, "\\" + ch)
    text = text.replace("://", "\\://").replace("www.", "www\\.")  # no automatic links
    return re.sub(r"^(#|>|[-+] |\d+[.)] )", r"\\\1", text)  # no heading, quote, or list


def cell(value):
    return plain(value).replace("|", "\\|")


def csv_cell(value):
    """Spreadsheets run a cell that starts with = + - @ as a formula. Prefix those with a quote."""
    return "'" + value if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def closed_amount(change):
    """The amount a won or lost deal closed at."""
    return -change.part(change.bucket)


def whole(value):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def money(value, currency):
    return f"{currency} {value:,.0f}"


def when(moment, tz):
    return (moment.astimezone(tz) if tz else moment).strftime("%Y-%m-%d %H:%M")


COLUMNS = ["deal", "bucket", "value", "parts", "flags", "note", "owner", "stage_before", "stage_after", "amount_before", "amount_after",
           "close_before", "close_after", "forecast_before", "forecast_after", "pipeline", "url", "id"]


def rows(ds, result):
    """One dict per changed deal, the same columns the dashboard and the CSV use."""
    out = []
    for c in sorted(result.changes, key=lambda c: (BUCKETS.index(c.bucket) if c.bucket else len(BUCKETS), -abs(c.value))):
        before, after = c.before or {}, c.after or {}
        deal = ds.deals[c.deal_id]
        out.append({
            "deal": c.name, "bucket": c.bucket or "", "value": whole(round(c.value, 2)),
            "parts": "; ".join(f"{b} {whole(round(v, 2))}" for b, v in c.parts), "flags": ", ".join(c.flags), "note": c.note,
            "owner": ds.owner_name((after or before).get("owner_id")),
            "stage_before": before.get("stage_label", ""), "stage_after": after.get("stage_label", "deleted" if not after else ""),
            "amount_before": whole(before.get("amount")), "amount_after": whole(after.get("amount")),
            "close_before": before.get("close_date") or "", "close_after": after.get("close_date") or "",
            "forecast_before": before.get("forecast_category") or "", "forecast_after": after.get("forecast_category") or "",
            "pipeline": ds.pipeline_label((after or before).get("pipeline")), "url": deal.get("url") or "", "id": c.deal_id,
        })
    return out


def summary(ds, result, tz=None, link=None):
    """The waterfall in words. link(deal_id) can turn deal names into links (the agent workspace uses it)."""
    cur, totals = ds.currency, result.totals()

    def name(c):
        return f"[{plain(c.name)}]({link(c.deal_id)})" if link else plain(c.name)

    def owner_of(state):
        return plain(ds.owner_name(state.get("owner_id")))
    lines = [
        f"# Pipeline changes: {when(result.start, tz)} to {when(result.end, tz)}", "",
        f"Source: {ds.name}. Scope: {result.scope.label}.", "",
        "| | Deals | Value |", "| --- | ---: | ---: |",
        f"| Pipeline at start | | {money(result.start_total, cur)} |",
    ]
    for bucket in BUCKETS:
        count, value = totals[bucket]
        if count:
            lines.append(f"| {'+' if bucket in ADDS else '-'} {bucket} | {count} | {money(value, cur)} |")
    net = result.end_total - result.start_total
    lines += [f"| Pipeline at end | | {money(result.end_total, cur)} |", f"| Net change | | {money(net, cur)} |", ""]
    if totals["won"][0] or totals["lost"][0]:
        lines += [f"Closed in the period: {totals['won'][0]} won ({money(-totals['won'][1], cur)}), "
                  f"{totals['lost'][0]} lost ({money(-totals['lost'][1], cur)}), at the amounts they closed at.", ""]
    for bucket in BUCKETS:
        moved = sorted((c for c in result.changes if any(b == bucket for b, _ in c.parts)), key=lambda c: -abs(c.part(bucket)))
        if moved:
            lines += [f"## {bucket.capitalize()} ({len(moved)})", ""]
            for c in moved[:10]:
                owner = owner_of(c.after or c.before)
                note = f", {c.note}" if c.note else ""
                lines.append(f"- {name(c)} ({owner}): {money(c.part(bucket), cur)}{note}")
            if len(moved) > 10:
                lines.append(f"- and {len(moved) - 10} more in changes.csv")
            lines.append("")
    for flag in FLAG_ORDER:
        flagged = result.flagged(flag)
        if flagged:
            lines += [f"## Flag: {flag} ({len(flagged)})", ""]
            for c in flagged[:10]:
                state = c.after or c.before
                lines.append(f"- {name(c)} ({owner_of(state)}), {plain(state.get('stage_label'))}, closes {state.get('close_date')}")
            if len(flagged) > 10:
                lines.append(f"- and {len(flagged) - 10} more in changes.csv")
            lines.append("")
    by_owner = {}
    for c in result.changes:
        if c.bucket:
            name = ds.owner_name((c.after or c.before).get("owner_id"))
            by_owner[name] = by_owner.get(name, 0) + c.value
    if by_owner:
        lines += ["## Net change by owner", "", "| Owner | Net change |", "| --- | ---: |"]
        lines += [f"| {cell(name)} | {money(value, cur)} |" for name, value in sorted(by_owner.items(), key=lambda kv: kv[1])]
        lines.append("")
    return "\n".join(lines)


def write(ds, result, out, tz=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary(ds, result, tz), encoding="utf-8")
    table = rows(ds, result)
    with (out / "changes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({k: csv_cell(v) for k, v in row.items()} for row in table)
    return [out / "summary.md", out / "changes.csv"]
