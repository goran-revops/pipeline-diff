"""The pipeline-diff dashboard. Start it with `pipeline-diff dashboard`."""

import html
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pipeline_diff import config, ranges, report, store, timeline
from pipeline_diff.buckets import BUCKETS, Scope, compare

BLUE, LIGHT_BLUE, GREEN, RED, PURPLE, ORANGE = "#2a78d6", "#79aee9", "#1f9d55", "#e34948", "#7b4fa6", "#eb6834"
INK, SECONDARY, MUTED, GRID, BASELINE, SURFACE, PALE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb", "#cfcdc4"
RANGES = {"Last 7 days": "7d", "Since previous sync": "sync", "Last 30 days": "30d", "Quarter to date": "qtd", "Custom dates": "custom"}
SCOPES = {"Closing this quarter": "this-quarter", "Closing next quarter": "next-quarter", "All open deals": "all"}
# bucket: (label, verb in the summary, colour, the colour's name, what it means)
BUCKET = {
    "new": ("New", "created", BLUE, "blue", "Created during the period."),
    "moved in": ("Moved in", "moved in", LIGHT_BLUE, "light blue",
                 "Already open, and came into this view, for example from another owner or pipeline."),
    "pulled in": ("Pulled in", "pulled in", LIGHT_BLUE, "light blue", "Its close date moved into the window before it closed or while it is still open."),
    "reopened": ("Reopened", "reopened", LIGHT_BLUE, "light blue", "Was closed, and is open again."),
    "amount up": ("Amount up", "raised in amount", "#9db4cc", "grey blue",
                  "Its amount went up. A deal that changed amount and then closed shows here and in won or lost."),
    "amount down": ("Amount down", "cut in amount", "#d9a9a9", "grey red", "Its amount went down."),
    "pushed out": ("Pushed out", "pushed past the window", ORANGE, "orange", "Still open, but its close date moved past the window."),
    "moved out": ("Moved out", "moved out", "#f4a47c", "light orange",
                  "Still open, but left this view: another owner or pipeline, or a close date before the window."),
    "won": ("Won", "won", GREEN, "green", "Closed won during the period, at the amount it closed at."),
    "lost": ("Lost", "lost", RED, "red", "Closed lost during the period, at the amount it closed at."),
    "removed": ("Deleted", "deleted", PURPLE, "purple", "Deleted or merged in the CRM."),
}
LABEL, VERB, COLOR, COLOR_WORD, MEANING = ({b: row[i] for b, row in BUCKET.items()} for i in range(5))
ENTRIES, OTHER = ("new", "moved in", "pulled in", "reopened"), ("amount up", "amount down", "moved out", "removed")
GROUPS = [("Added", ENTRIES), ("Won", ("won",)), ("Lost", ("lost",)), ("Pushed out", ("pushed out",)), ("Other", OTHER)]
GROUP_OF = {b: name for name, members in GROUPS for b in members}
NO_TOOLBAR = {"displayModeBar": False}
FLAGS = [("past due", "Past due", "Open, but the close date has already passed."),
         ("close date pushed", "Close date pushed", "The close date moved later during the period. This includes pushes that stayed "
          "inside the window; the Pushed out bar counts only deals pushed past it."),
         ("forecast down", "Forecast category down", "Moved down between Commit, Best case, Pipeline, and Omitted. This includes deals "
          "created during the range whose category then dropped, which the Forecast grid shows under new or pulled in."),
         ("stage back", "Moved back a stage", "The deal went back to an earlier stage."),
         ("stalled", "Stalled", "No stage change in 30 days."),
         ("owner changed", "Reassigned", "The deal got a new owner during the period.")]
CATEGORIES = list(store.FORECAST[:4]) + ["Not set"]
BIGGEST = "Biggest moves"
WIDTHS = {"Move": 200, "Deal": 200, "What happened": 170, "Owner": 130, "Stage": 220, "Close date": 230, "Flags": 200, "Forecast": 160,
          "Close date moves": 120, "Days in stage": 100}
WIDTHS_WIDE = {"Deal": 190, "What happened": 230, "Owner": 130}


def short(value, currency="", sign=False, digits=1):
    size = abs(value)
    text = f"{size / 1e6:.{digits}f}M" if size >= 1e6 else f"{size / 1e3:.0f}k" if size >= 1e3 else f"{size:.0f}"
    lead = "-" if value < 0 else "+" if sign and value > 0 else ""
    return f"{lead}{currency + ' ' if currency else ''}{text}"


def safe_url(url):
    """Only web links: a javascript: or data: URL in the data must never become clickable."""
    return url if isinstance(url, str) and url.lower().startswith(("https://", "http://")) else None


def plotly_text(text):
    return html.escape(str(text), quote=False)  # plotly reads labels as HTML; quotes stay quotes


def count_deals(count):
    return f"{count} deal" if count == 1 else f"{count} deals"


def number(value):
    return "" if value is None or value == "" else f"{float(value):,.0f}"


def arrow(before, after, fmt=str):
    """'before -> after', or just the value when it did not change."""
    b, a = (fmt(before) if before not in (None, "") else ""), (fmt(after) if after not in (None, "") else "")
    if not b or b == a:
        return a
    return f"{b} -> {a or 'deleted'}"


def style(fig, height=380):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=16, b=8), plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
                      font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=SECONDARY, size=13),
                      hoverlabel=dict(bgcolor="white", font_color=INK, bordercolor=BASELINE), showlegend=False)
    fig.update_xaxes(showgrid=False, linecolor=BASELINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=BASELINE, tickfont=dict(color=MUTED))
    return fig


def wrap(label, width=16):
    """Break a long axis label at the space nearest its middle."""
    if len(label) <= width or " " not in label:
        return label
    middle = len(label) // 2
    cut = min((i for i, ch in enumerate(label) if ch == " "), key=lambda i: abs(i - middle))
    return label[:cut] + "<br>" + label[cut + 1:]


def table_height(count, most=12):
    return 38 + 35 * max(1, min(count, most))


def start_end_bars(names, start_values, end_values, height=380):
    shown = ["<br>".join(wrap(part) for part in plotly_text(n).split("\n")) for n in names]
    fig = go.Figure([go.Bar(name="Start", x=shown, y=start_values, marker=dict(color=PALE, cornerradius=3),
                            text=[short(v) if v or e else "" for v, e in zip(start_values, end_values)], textposition="outside",
                            cliponaxis=False),
                     go.Bar(name="End", x=shown, y=end_values, marker=dict(color=SECONDARY, cornerradius=3),
                            text=[short(v) if v or s else "" for v, s in zip(end_values, start_values)], textposition="outside",
                            cliponaxis=False)])
    fig.update_traces(hovertemplate="%{x}<br>%{y:,.0f} " + cur + "<extra>%{fullData.name}</extra>")
    return style(fig, height).update_layout(barmode="group", bargap=0.3, bargroupgap=0.06, showlegend=True,
                                            legend=dict(orientation="h", y=1.08, x=0)).update_yaxes(tickformat="~s").update_xaxes(
        tickangle=0, tickfont=dict(size=11))


def grid(moves, order):
    """A from/to grid of (from, to, amount) moves: each cell is the deal count and their amount, blank when empty."""
    cells = {}
    for f, t_, amount in moves:
        n, total = cells.get((f, t_), (0, 0.0))
        cells[(f, t_)] = (n + 1, total + amount)
    rows = sorted({f for f, _, _ in moves}, key=lambda s: order.get(s, 99))
    columns = sorted({t_ for _, t_, _ in moves}, key=lambda s: order.get(s, 99))
    return pd.DataFrame([[f"{cells[(r, c)][0]}  ({short(cells[(r, c)][1])})" if (r, c) in cells else "" for c in columns] for r in rows],
                        index=pd.Index(rows, name="From"), columns=columns)


def show_grid(frame):
    st.dataframe(frame, width="stretch", column_config={c: st.column_config.TextColumn(width=85 if len(frame.columns) > 8 else 105) for c in frame.columns})


@st.cache_resource(show_spinner=False)
def load(folder, stamp):
    return timeline.load(folder)


def dataset(folder):
    sync = Path(folder) / "sync.json"
    try:
        return load(str(folder), sync.stat().st_mtime if sync.exists() else 0)
    except SystemExit as problem:
        st.error(str(problem))
        st.stop()


@st.cache_resource(show_spinner="Comparing...", max_entries=24)
def analyse(folder, stamp, start, end, pipelines, owners, window, label, tz_name):
    """The comparison, plus the open pipeline by stage and by forecast category at both ends, cached per view."""
    ds = load(folder, stamp)
    tz = config.timezone()
    scope = Scope(pipelines, owners, window, label)
    result = compare(ds, start, end, scope, tz=tz)
    by_stage, by_category = {"start": {}, "end": {}}, {"start": {}, "end": {}}
    open_at_end, counts = [], {"start": 0, "end": 0}
    for deal_id in ds.deals:
        if ds.settled(deal_id, start):
            continue
        for key, moment in (("start", start), ("end", end)):
            state = timeline.state_at(ds, deal_id, moment)
            if scope.contains(state):
                slot = (state["pipeline"], state["stage"])
                by_stage[key][slot] = by_stage[key].get(slot, 0) + state["amount"]
                category = state.get("forecast_category") or "Not set"
                by_category[key][category] = by_category[key].get(category, 0) + state["amount"]
                counts[key] += 1
                if key == "end":
                    open_at_end.append(deal_id)
    return result, by_stage, by_category, open_at_end, counts


def pushes(ds, deal_id, start, end):
    return sum(1 for when, _ in ds.history.get((deal_id, "close_date"), []) if start < when <= end)


def deal_rows(ds, changes, cur, start, end, bucket=None):
    """The human table: one row per deal, or per deal in one bucket."""
    out = []
    for c in changes:
        before, after = c.before or {}, c.after or {}
        state = after or before
        effect = c.part(bucket) if bucket else c.value
        happened = (", ".join(f"{LABEL[b]} {short(v, sign=True)}" for b, v in c.parts) if len(c.parts) > 1
                    else LABEL[c.parts[0][0]] if c.parts else "No value change")
        close_b, close_a = before.get("close_date"), after.get("close_date")
        days = (date.fromisoformat(str(close_a)[:10]) - date.fromisoformat(str(close_b)[:10])).days if close_a and close_b else 0
        stage_since = timeline.last_change(ds, c.deal_id, "stage", end) if after and not after["is_closed"] else None
        out.append({
            "Deal": c.name, "What happened": happened, f"Pipeline effect ({cur})": effect,
            "Owner": arrow(owner_name(before.get("owner_id")) if before and after else None, owner_name(state.get("owner_id"))),
            "Stage": arrow(before.get("stage_label"), after.get("stage_label") if after else "deleted"),
            f"Amount ({cur})": arrow(before.get("amount"), after.get("amount"), number),
            "Close date": arrow(close_b, close_a) + (f" ({days:+d} day{'s' if abs(days) != 1 else ''})" if days else ""),
            "Forecast": arrow(before.get("forecast_category"), after.get("forecast_category")),
            "Close date moves": pushes(ds, c.deal_id, start, end),
            "Days in stage": str((end - stage_since).days) if stage_since else "",
            "Flags": ", ".join(c.flags), "Pipeline": ds.pipeline_label(state.get("pipeline")),
            "CRM": safe_url(ds.deals[c.deal_id].get("url")), "id": c.deal_id,
            "Size": max([abs(v) for _, v in c.parts] or [0]), "Amount now": float((after or {}).get("amount") or 0),
        })
    out.sort(key=lambda row: -row["Size"])
    return pd.DataFrame(out, columns=["Deal", "What happened", f"Pipeline effect ({cur})", "Owner", "Stage", f"Amount ({cur})",
                                      "Close date", "Forecast", "Close date moves", "Days in stage", "Flags", "Pipeline", "CRM", "id",
                                      "Size", "Amount now"])


def show_table(frame, cur, key, columns=None, most=12, wide=False):
    """A deal table. Selecting a row opens that deal below it."""
    if frame.empty:
        st.caption("No deals.")
        return
    columns = [c for c in (columns or frame.columns) if c in frame.columns and c not in ("id", "Size", "Amount now")]
    if "CRM" in columns and frame["CRM"].isna().all():
        columns.remove("CRM")
    if "Forecast" in columns and not frame["Forecast"].astype(bool).any():
        columns.remove("Forecast")
    config_ = {name: st.column_config.TextColumn(width=width) for name, width in {**WIDTHS, **(WIDTHS_WIDE if wide else {})}.items()}
    config_.update({"CRM": st.column_config.LinkColumn("CRM", display_text="Open", width=60),
                    f"Pipeline effect ({cur})": st.column_config.NumberColumn(format="%+,.0f", width=120),
                    f"Amount ({cur})": st.column_config.TextColumn(width=125),
                    "Close date moves": st.column_config.NumberColumn(width=120)})
    event = st.dataframe(frame[columns], hide_index=True, width="stretch", height=table_height(len(frame), most),
                         on_select="rerun", selection_mode="single-row", key=key, column_config=config_)
    picked = event.selection.rows if event and event.selection else []
    more = f"{len(frame)} deals, {most} shown at a time: scroll the table for the rest. " if len(frame) > most else ""
    if picked:
        with st.container(border=True):
            deal_detail(frame.iloc[picked[0]]["id"], key)
    else:
        st.caption(more + "Select a row to open that deal's full history here.")


def owner_name(owner_id):
    """The owner's name, with their email when another owner has the same name."""
    if not hasattr(ds, "shared_names"):
        names_ = [o["name"] for o in ds.owners.values()]
        ds.shared_names = {n for n in names_ if names_.count(n) > 1} if len(names_) < 500 else {
            n for n, k in pd.Series(names_).value_counts().items() if k > 1}
    name = ds.owner_name(owner_id)
    return f"{name} ({ds.owners.get(str(owner_id), {}).get('email') or owner_id})" if name in ds.shared_names else name


def deal_detail(deal_id, where):
    """One deal's facts, amount chart, and history. where keeps two copies on one page apart."""
    deal = ds.deals[deal_id]
    now_state = timeline.state_at(ds, deal_id, end)
    left, right = st.columns([1, 2])
    with left:
        st.markdown(f"**{report.plain(deal.get('name') or deal_id)}**")
        if now_state:
            status = "" if now_state["is_closed"] else " (open)"
            lines = [f"Owner: {report.plain(owner_name(now_state['owner_id']))}",
                     f"Stage: {report.plain(now_state['stage_label'])}{status}",
                     f"Amount: {report.money(now_state['amount'], cur)}", f"Close date: {now_state['close_date'] or 'not set'}"]
            if now_state.get("forecast_category"):
                lines.append(f"Forecast category: {report.plain(now_state['forecast_category'])}")
            born = store.parse(deal["created_at"]) + timedelta(seconds=120)
            moves = [(w, v) for w, v in ds.history.get((deal_id, "close_date"), []) if w <= end]
            settled = [(w, v) for w, v in moves if w <= born][-1:] + [(w, v) for w, v in moves if w > born]
            changed = [(w, v) for i, (w, v) in enumerate(settled) if i and v != settled[i - 1][1]]
            if changed and all(v for _, v in settled):
                shift = (date.fromisoformat(str(settled[-1][1])[:10]) - date.fromisoformat(str(settled[0][1])[:10])).days
                lines.append(f"Close date moved {len(changed)} time{'s' if len(changed) != 1 else ''} since created ({shift:+d} days)")
            lines += [f"Pipeline: {report.plain(ds.pipeline_label(now_state['pipeline']))}",
                      f"Created: {report.when(store.parse(deal['created_at']), tz)}"]
            st.markdown("  \n".join(lines))
        else:
            st.write("Deleted, or not created yet, at the end of the range.")
        amounts = [(w, float(v or 0)) for w, v in ds.history.get((deal_id, "amount"), []) if w <= end]
        if amounts and len({v for _, v in amounts}) == 1:
            st.caption("The amount has not changed since the deal was created.")
        if safe_url(deal.get("url")):
            st.link_button("Open in CRM", safe_url(deal["url"]))
    with right:
        if len({v for _, v in amounts}) > 1:
            points = [(w.astimezone(tz), v) for w, v in amounts] + [(end.astimezone(tz), amounts[-1][1])]
            fig = go.Figure(go.Scatter(x=[p[0] for p in points], y=[p[1] for p in points], mode="lines+markers",
                                       line=dict(color=BLUE, width=2, shape="hv"),
                                       marker=dict(size=7, color=BLUE, line=dict(color=SURFACE, width=2)),
                                       hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} " + cur + "<extra></extra>"))
            top = max(v for _, v in points) or 1
            st.caption("Amount over time")
            st.plotly_chart(style(fig, 240).update_yaxes(tickformat="~s", range=[0, top * 1.15]).update_xaxes(
                range=[points[0][0], points[-1][0]]), width="stretch", key=f"amount-{where}-{deal_id}", config=NO_TOOLBAR)
    history = history_rows(deal_id)
    st.dataframe(history, hide_index=True, width="stretch", height=table_height(len(history), 40),
                 column_config={"To": st.column_config.TextColumn(width="large")})


def shown(field, value):
    if value in (None, ""):
        return ""
    if field == "stage":
        return ds.stage_label(value)
    if field == "owner_id":
        return owner_name(value)
    if field == "pipeline":
        return ds.pipeline_label(value)
    if field == "amount":
        return report.money(float(value), cur)
    return str(value)


def history_rows(deal_id):
    """When, what, from, to. The values set when the deal was created are one row."""
    rows, last, created = [], {}, store.parse(ds.deals[deal_id]["created_at"])
    names = {"owner_id": "Owner", "close_date": "Close date", "forecast_category": "Forecast category"}
    for when, field, value in ds.changes_of(deal_id):
        if when > end:
            break
        if abs((when - created).total_seconds()) < 120:
            last[field] = value  # set or fixed as the deal was created: part of the Created row, not a change
            continue
        if last.get(field) == value:
            continue
        rows.append({"When": report.when(when, tz), "Change": names.get(field, field.capitalize()), "From": shown(field, last.get(field)),
                     "To": shown(field, value)})
        last[field] = value
    deal = ds.deals[deal_id]
    created_with = {field: deal.get(field) for field in store.TRACKED}  # a deal with no history: what the CRM has now
    created_with.update({field: value for when, field, value in ds.changes_of(deal_id) if abs((when - created).total_seconds()) < 120})
    start_values = ", ".join(shown(f, created_with[f]) for f in ("pipeline", "stage", "amount", "close_date", "owner_id", "forecast_category")
                             if created_with.get(f) not in (None, ""))
    rows.insert(0, {"When": report.when(created, tz), "Change": "Created", "From": "", "To": start_values})
    return pd.DataFrame(rows)


st.set_page_config(page_title="pipeline-diff", layout="wide")
data_dir = config.data_dir()
names = store.sources(data_dir)
if not names:
    st.title("pipeline-diff")
    st.info(f"No synced data in `{data_dir}`. Run `pipeline-diff demo` for invented demo data, or `pipeline-diff sync hubspot`.")
    st.stop()

with st.sidebar:
    st.header("pipeline-diff")
    source = st.selectbox("Source", names)
    ds = dataset(data_dir / source)
    range_name = st.selectbox("Compare", list(RANGES))
    try:
        data_day = ranges.latest(ds).astimezone(config.timezone()).date()
    except SystemExit:
        data_day = ranges.latest(ds).date()
    custom = st.date_input("Dates", value=(data_day - timedelta(days=7), data_day)) if RANGES[range_name] == "custom" else None
    quarter_end = ranges.quarter(data_day)[1]
    near_end = (quarter_end - data_day).days < 14
    scope_name = st.selectbox("Scope", list(SCOPES), index=1 if near_end else 0, help="Which deals count as pipeline. With a quarter, a deal whose close "
                              "date moves out of the quarter counts as pushed out, the way a forecast call talks about it. In the last two weeks of a quarter "
                              "the page opens on next quarter.")
    if near_end and scope_name == "Closing next quarter":
        left = (quarter_end - data_day).days + 1
        st.caption(f"{ranges.quarter(data_day)[2]} ends {'today' if left == 1 else f'in {left} days'} for this data, so the page opened "
                   "on next quarter.")
    labels_seen = [p["label"] for p in ds.pipelines]
    pipeline_labels = {(p["label"] if labels_seen.count(p["label"]) == 1 else f"{p['label']} ({p['id']})"): p["id"] for p in ds.pipelines}
    picked_pipelines = st.multiselect("Pipelines", list(pipeline_labels), placeholder="All pipelines")
    known_owners = set(ds.owners) | {str(d["owner_id"]) for d in ds.deals.values() if d.get("owner_id")}
    picked_owners = st.multiselect("Owners", sorted(known_owners, key=owner_name), placeholder="All owners", format_func=owner_name)
    st.caption("The page rereads the data every 5 minutes. New CRM data arrives each time `pipeline-diff sync` runs.")


@st.fragment(run_every="300s")
def body():
    global ds, end, cur, tz
    ds = dataset(data_dir / source)
    try:
        tz = config.timezone()
    except SystemExit as problem:
        st.error(str(problem))
        return
    since = RANGES[range_name]
    start_day = end_day = None
    if since == "custom" and isinstance(custom, (tuple, list)) and len(custom) == 2:
        start_day, end_day = custom
    try:
        start, end, scope = ranges.resolve(ds, since if since != "custom" else "7d", start_day, end_day, SCOPES[scope_name], tz,
                                           [pipeline_labels[p] for p in picked_pipelines], picked_owners)
    except SystemExit as problem:
        st.error(f"Pick another range: {problem}")
        return
    sync = data_dir / source / "sync.json"
    result, by_stage, by_category, open_at_end, counts = analyse(
        str(data_dir / source), sync.stat().st_mtime if sync.exists() else 0, start, end, scope.pipelines, scope.owners, scope.window,
        scope.label, str(tz))
    cur, totals = ds.currency, result.totals()
    net = result.end_total - result.start_total

    st.title("Pipeline changes")
    st.caption(f"{ds.name}  |  {scope.label}  |  {report.when(start, tz)} to {report.when(end, tz)}  |  "
               f"data as of {report.when(ranges.latest(ds), tz)}  |  times in {tz}")
    if ds.sync.get("note"):
        st.caption(ds.sync["note"])

    def total(group):
        return sum(totals[b][1] for b in group)

    pieces = []
    for name, members in GROUPS:
        if any(totals[b][0] for b in members):
            value = total(members) if name in ("Added", "Other") else -total(members)
            detail = (", ".join(f"{totals[b][0]} {VERB[b]}" for b in members if totals[b][0]) if len(members) > 1
                      else count_deals(totals[members[0]][0]))
            pieces.append(f"{name} {short(value, sign=name in ('Added', 'Other'))} ({detail})")
    st.markdown(f"Pipeline went from **{short(result.start_total, cur, digits=2)}** to **{short(result.end_total, cur, digits=2)}** "
                f"({'up' if net >= 0 else 'down'} {short(abs(net), cur)})." + (f" {'. '.join(pieces)}." if pieces else ""))
    late = [c for c in result.changes if "past due" in c.flags]
    if late:
        st.markdown(f"**{count_deals(len(late))} worth {short(sum(c.after['amount'] for c in late), cur)} are past their close date,** "
                    f"out of {count_deals(counts['end'])} and {short(result.end_total, cur)} still open. See Flags.")

    other_deals = totals["moved out"][0] + totals["removed"][0]
    pushed = [c for c in result.changes if "close date pushed" in c.flags]
    pushed_worth = sum(c.after["amount"] for c in pushed)
    tiles = [("Start", short(result.start_total, digits=2), count_deals(counts["start"])),
             ("Added", short(total(ENTRIES), digits=2), count_deals(sum(totals[b][0] for b in ENTRIES))),
             ("Won", short(-total(["won"]), digits=2), count_deals(totals["won"][0])),
             ("Lost", short(-total(["lost"]), digits=2), count_deals(totals["lost"][0])),
             ("Pushed out", short(-total(["pushed out"]), digits=2), count_deals(totals["pushed out"][0])),
             ("Other", short(total(OTHER), sign=True, digits=2), f"{count_deals(other_deals)} out" if other_deals else "amounts only"),
             ("End", short(result.end_total, digits=2), count_deals(counts["end"]))]
    tiles = [tile for tile in tiles if scope.window or tile[0] != "Pushed out"]  # with no window nothing can be pushed out
    formula = "start + added - won - lost - pushed out + other = end" if scope.window else "start + added - won - lost + other = end"
    st.caption(f"In {cur}: {formula}, and the deal counts add up the same way. Other is amount changes, deals moved out of this view, "
               "and deleted deals. The waterfall below groups its bars the same way.")
    for col, (label, value, delta) in zip(st.columns(len(tiles)), tiles):
        col.metric(label, value, delta, delta_color="off", delta_arrow="off")
    if not scope.window and pushed:
        st.caption(f"Also: {count_deals(len(pushed))} worth {short(pushed_worth, cur)} had their close date pushed. With all open deals as "
                   "the scope a pushed deal stays in the pipeline, so it is not in the sum; pick a quarter scope to see pushes as pushed out.")

    tabs = st.tabs(["Overview", "All changes", "By owner", "By stage", "Forecast", "Flags", "Deal lookup"])
    all_rows = deal_rows(ds, result.changes, cur, start, end)

    with tabs[0]:
        moved = [b for _, members in GROUPS for b in members if totals[b][0]]
        bases, heights, level = [], [], result.start_total
        for b in moved:
            value = totals[b][1]
            bases.append(level if value >= 0 else level + value)
            heights.append(abs(value))
            level += value
        groups = ["Start"] + [GROUP_OF[b] for b in moved] + ["End"]
        labels = [" "] + [(f"{LABEL[b]}<br>" if GROUP_OF[b] in ("Added", "Other") else "") + count_deals(totals[b][0]) for b in moved] + ["  "]
        fig = go.Figure(go.Bar(
            x=[groups, labels], base=[0] + bases + [0], y=[result.start_total] + heights + [result.end_total],
            marker=dict(color=[MUTED] + [COLOR[b] for b in moved] + [MUTED], cornerradius=3),
            text=[short(result.start_total)] + [short(totals[b][1], sign=True) for b in moved] + [short(result.end_total)],
            textposition="outside", textfont=dict(color=SECONDARY), cliponaxis=False,
            customdata=[""] + [MEANING[b] for b in moved] + [""],
            hovertemplate="%{text}<br>%{customdata}<extra></extra>",
        ))
        st.subheader("How the pipeline got from start to end")
        top = max([result.start_total, result.end_total] + [b + h for b, h in zip(bases, heights)]) or 1
        # hovermode "x": the whole column answers a click, so a thin bar is as easy to pick as a tall one
        fig = style(fig, 440).update_layout(bargap=0.35, margin=dict(l=8, r=8, t=28, b=8), hovermode="x")
        fig.update_yaxes(tickformat="~s", range=[0, top * 1.12])
        fig.update_xaxes(tickfont=dict(color=SECONDARY), tickangle=0)
        clicked = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="waterfall", config=NO_TOOLBAR)
        colours = {}
        for b in moved:
            colours.setdefault(COLOR_WORD[b], []).append(LABEL[b].lower())
        st.caption("Grey is the pipeline at the start and the end. " + "; ".join(f"{word.capitalize()}: {', '.join(names)}"
                                                                                for word, names in colours.items())
                   + ". The bars add up exactly from start to end. Click a bar to list its deals.")
        with st.expander("What each bar means"):
            st.markdown("\n".join(f"- **{LABEL[b]}**: {MEANING[b]}" for b in BUCKETS))

        options = [BIGGEST] + [LABEL[b] for b in moved]
        points = clicked.selection.points if clicked and clicked.selection else []
        token = tuple(p.get("point_index") for p in points)
        if points and st.session_state.get("last_click") != token:
            index = points[0].get("point_index", 0)
            if 1 <= index <= len(moved):
                st.session_state["bar_pick"] = LABEL[moved[index - 1]]
        st.session_state["last_click"] = token
        if st.session_state.get("bar_pick") not in options:
            st.session_state["bar_pick"] = BIGGEST
        pick = st.pills("Show deals", options, key="bar_pick", selection_mode="single") or BIGGEST
        if pick == BIGGEST:
            frame = all_rows[all_rows["What happened"] != "No value change"]
            st.caption(f"The 15 largest of {count_deals(len(frame))} that moved the pipeline, by the amount that moved." if len(frame) > 15
                       else f"All {count_deals(len(frame))} that moved the pipeline, largest first.")
            frame = frame.head(15)
            show_table(frame, cur, "biggest", ["Deal", "What happened", f"Amount ({cur})", "Owner", "Close date", "CRM"], wide=True)
        else:
            bucket = next(b for b in moved if LABEL[b] == pick)
            st.caption(MEANING[bucket])
            inside = [c for c in result.changes if any(b == bucket for b, _ in c.parts)]
            show_table(deal_rows(ds, inside, cur, start, end, bucket), cur, f"bucket-{bucket}",
                       ["Deal", f"Pipeline effect ({cur})", "Owner", "Stage", "Close date", "CRM"], wide=True)

    with tabs[1]:
        left, middle, right = st.columns([2, 1, 1])
        search = left.text_input("Find a deal or owner", placeholder="Type part of a name")
        buckets = middle.multiselect("What happened", [LABEL[b] for b in BUCKETS if totals[b][0]], placeholder="Anything")
        flag_names = sorted({f for fl in all_rows["Flags"] for f in fl.split(", ") if f})
        flag = right.selectbox("Flag", ["Any"] + flag_names)
        view = all_rows
        if len(view) and search:
            needle = search.lower()
            view = view[view["Deal"].str.lower().str.contains(needle, regex=False) | view["Owner"].str.lower().str.contains(needle, regex=False)]
        if len(view) and buckets:
            view = view[view["What happened"].apply(lambda text: any(b in text for b in buckets))]
        if len(view) and flag != "Any":
            view = view[view["Flags"].str.contains(flag, regex=False)]
        if len(view):
            view = view.reindex(view[f"Pipeline effect ({cur})"].abs().sort_values(ascending=False, kind="stable").index)
        st.caption(f"{len(view)} of {len(all_rows)} changed deals, largest pipeline effect first.")
        show_table(view, cur, "all-changes", most=15)
        csv_rows = pd.DataFrame(report.rows(ds, result), columns=report.COLUMNS)
        st.download_button("Download CSV", csv_rows.map(report.csv_cell).to_csv(index=False).encode("utf-8"), "changes.csv", "text/csv")

    with tabs[2]:
        per_owner = {}
        for c in result.changes:
            owner = str((c.after or c.before).get("owner_id") or "")
            for b, v in c.parts:
                per_owner.setdefault(owner, {}).setdefault(b, 0.0)
                per_owner[owner][b] += v
        if per_owner:
            order = sorted(per_owner, key=owner_name, reverse=True)  # alphabetical from the top: plotly draws the first bar at the bottom
            fig = go.Figure()
            for b in BUCKETS:
                values = [per_owner[o].get(b, 0.0) for o in order]
                if any(values):
                    fig.add_trace(go.Bar(name=LABEL[b], y=[plotly_text(owner_name(o)) for o in order], x=values, orientation="h",
                                         marker=dict(color=COLOR[b]),
                                         hovertemplate="%{y}<br>" + LABEL[b] + ": %{x:,.0f} " + cur + "<extra></extra>"))
            st.subheader("What moved each owner's pipeline")
            st.caption("Bars to the right added pipeline, bars to the left took it out. Won deals leave the open pipeline, so they sit "
                       "on the left in green: a rep with a long green bar closed a lot, which is good.")
            height = 90 + 30 * len(order)
            st.plotly_chart(style(fig, height).update_layout(barmode="relative", showlegend=True, legend=dict(orientation="h", y=1.02, x=0,
                            yanchor="bottom")).update_xaxes(tickformat="~s", showgrid=True, gridcolor=GRID, zeroline=True,
                            zerolinecolor=BASELINE), width="stretch", config=NO_TOOLBAR)
            present = [b for b in BUCKETS if any(b in per_owner[o] for o in per_owner)]
            table = pd.DataFrame([{"Owner": owner_name(o), **{LABEL[b]: per_owner[o].get(b, 0.0) for b in present},
                                   "Net excl. wins": sum(v for b, v in per_owner[o].items() if b != "won")}
                                  for o in reversed(order)])
            st.dataframe(table, hide_index=True, width="stretch", height=table_height(len(table), 25),
                         column_config={c: st.column_config.NumberColumn(format="%+,.0f", width="small") for c in table.columns if c != "Owner"})
            st.caption(f"Amounts in {cur}, the pipeline effect of each kind of change. Won and lost show as minus because they left the "
                       "open pipeline. The last column leaves wins out, so closing deals never counts against a rep.")
        else:
            st.info("No deal moved the pipeline value in this range.")

    with tabs[3]:
        several = len({p for p, _ in list(by_stage["start"]) + list(by_stage["end"])}) > 1
        slots = [(p["id"], s["id"], (f"{p['label']}\n" if several else "") + s["label"]) for p in ds.pipelines for s in p["stages"]
                 if not s["is_closed"] and ((p["id"], s["id"]) in by_stage["start"] or (p["id"], s["id"]) in by_stage["end"])]
        known = {(p, s) for p, s, _ in slots}
        slots += [(p, s, (f"{ds.pipeline_label(p)}\n" if several else "") + f"{s} (not in the CRM's stage list)")
                  for p, s in dict.fromkeys(list(by_stage["start"]) + list(by_stage["end"])) if (p, s) not in known]
        st.subheader("Open pipeline by stage, start and end")
        st.plotly_chart(start_end_bars([label for _, _, label in slots], [by_stage["start"].get((p, s), 0) for p, s, _ in slots],
                                       [by_stage["end"].get((p, s), 0) for p, s, _ in slots]), width="stretch", config=NO_TOOLBAR)
        flows = [(c.deal_id, c.before["pipeline"], c.before["stage_label"], c.after["stage_label"], c.after["amount"]) for c in result.changes
                 if c.before and c.after and c.before["stage"] != c.after["stage"] and not c.before["is_closed"]
                 and c.before["pipeline"] == c.after["pipeline"]]
        st.subheader(f"Where deals moved ({count_deals(len(flows))})")
        if flows:
            st.caption("Each row is the stage deals were in at the start, each column where they were at the end, with the deal count "
                       "and their amount. Deals created during the range had no stage at the start, so they are not in this grid, and its "
                       "won and lost counts can be lower than the tiles.")
            for p in ds.pipelines:
                mine = [(f, t_, a) for _, pid, f, t_, a in flows if pid == p["id"]]
                if mine:
                    if several:
                        st.markdown(f"**{report.plain(p['label'])}**")
                    show_grid(grid(mine, {s["label"]: s["order"] for s in p["stages"]}))
            moved_ids = {deal_id for deal_id, *_ in flows}
            st.subheader("The deals in the grid")
            show_table(all_rows[all_rows["id"].isin(moved_ids)], cur, "stage-moves", ["Deal", "Owner", "Stage", f"Amount ({cur})",
                                                                                      "Days in stage", "CRM"])
        else:
            st.caption("None of the deals open at the start of the range changed stage. Deals created during it are in the Overview.")

    with tabs[4]:
        if not any(value for (_, field), changes in ds.history.items() if field == "forecast_category" for _, value in changes):
            st.info("This source has no forecast categories. HubSpot keeps them in the Forecast category deal property, and Salesforce "
                    "in the opportunity's Forecast Category.")
        else:
            seen = set(by_category["start"]) | set(by_category["end"])
            present = [c for c in CATEGORIES[:-1] if c in seen] + sorted(seen - set(CATEGORIES)) + (["Not set"] if "Not set" in seen else [])
            won = -totals["won"][1]
            st.subheader("Pipeline by forecast category, start and end")
            st.plotly_chart(start_end_bars(present + ["Won in range"], [by_category["start"].get(c, 0) for c in present] + [0],
                                           [by_category["end"].get(c, 0) for c in present] + [won], 340), width="stretch", config=NO_TOOLBAR)
            st.caption("Open deals in the scope, by category. The last bar is what was won during the range.")
            summary = [f"{report.plain(c)} {short(by_category['start'].get(c, 0))} -> {short(by_category['end'].get(c, 0))}" for c in present]
            if summary:
                st.markdown("Open pipeline by category, start -> end: " + ", ".join(summary) + ".")
            moves = []
            for c in result.changes:
                was, now = scope.contains(c.before), scope.contains(c.after)
                if not (was or now or c.parts):
                    continue
                origin = (c.before.get("forecast_category") or "Not set") if was else "New or pulled in"
                if now:
                    target = c.after.get("forecast_category") or "Not set"
                elif c.after is None:
                    target = "Deleted"
                elif c.after["is_closed"]:
                    target = "Won" if c.after["is_won"] else "Lost"
                else:
                    target = "Pushed out" if scope.after_window(c.after) else "Moved out"
                if origin != target:
                    moves.append((c.deal_id, origin, target, (c.before if was else c.after)["amount"]))
            st.subheader(f"Where each category's deals went ({count_deals(len(moves))})")
            if moves:
                st.caption("Each row is where deals started: a category, or new or pulled in during the range. Each column is where they "
                           "ended: a category, or won, lost, pushed out, moved out, or deleted. Cells show the deal count and their amount "
                           "at the start (at the end for new ones), so a category's start minus what left plus what arrived is its end, "
                           "apart from amount changes.")
                order = {c: i for i, c in enumerate(["New or pulled in", "Not set", *store.FORECAST[:4], "Won", "Lost", "Pushed out",
                                                     "Moved out", "Deleted"])}
                show_grid(grid([(f, t_, a) for _, f, t_, a in moves], order))
                move_of = {deal_id: f"{f} -> {t_}" for deal_id, f, t_, _ in moves}
                rows = all_rows[all_rows["id"].isin(move_of)].assign(Move=lambda d: d["id"].map(move_of))
                show_table(rows, cur, "forecast-moves", ["Deal", "Owner", "Move", f"Amount ({cur})", "Stage", "Close date", "CRM"])
            else:
                st.caption("None of the deals open at the start of the range changed forecast category.")

    with tabs[5]:
        for flag, title, text in FLAGS:
            flagged = all_rows[all_rows["Flags"].str.contains(flag, regex=False)] if len(all_rows) else all_rows
            flagged = flagged.sort_values("Amount now", ascending=False)
            worth = sum(c.after["amount"] for c in result.changes if flag in c.flags and c.after)
            if not len(flagged):
                st.caption(f"{title}: none in this range.")
                continue
            st.subheader(f"{title}: {count_deals(len(flagged))}, {short(worth, cur)}")
            st.caption(text)
            columns = {"close date pushed": ["Deal", "Owner", f"Amount ({cur})", "Close date", "Close date moves", "CRM"],
                       "forecast down": ["Deal", "Owner", "Forecast", f"Amount ({cur})", "Close date", "CRM"],
                       "stalled": ["Deal", "Owner", "Stage", f"Amount ({cur})", "Days in stage", "CRM"]}.get(
                flag, ["Deal", "Owner", "Stage", f"Amount ({cur})", "Close date", "CRM"])
            show_table(flagged, cur, f"flag-{flag}", columns, most=8)

    with tabs[6]:
        changed = list(dict.fromkeys(all_rows["id"]))  # biggest movers first
        rest = sorted((d for d in open_at_end if d not in set(changed)), key=lambda d: ds.deals[d].get("name") or "")
        wanted = st.text_input("Search every deal by name", placeholder="Type part of a deal name, or leave empty for this range")
        if wanted.strip():
            needle = wanted.strip().lower()
            choices = sorted((d for d, deal in ds.deals.items() if needle in (deal.get("name") or "").lower()),
                             key=lambda d: ds.deals[d].get("name") or "")[:200]
            st.caption(f"{count_deals(len(choices))} match" + (" (the first 200)." if len(choices) == 200 else "."))
        else:
            choices = changed + rest
            st.caption(f"{count_deals(len(changed))} changed in this range" + (f", then {len(rest)} more deals open in this view" if rest else "")
                       + ". Type above to search every deal.")
        deal_id = st.selectbox("Deal", choices, format_func=lambda d: ds.deals[d].get("name") or d, index=0 if choices else None,
                               placeholder="Pick a deal")
        if deal_id:
            deal_detail(deal_id, "lookup")


body()
