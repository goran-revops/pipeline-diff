# pipeline-diff

pipeline-diff is an open-source pipeline change tracker for HubSpot and Salesforce: revenue intelligence you run on your own CRM data. It shows what changed in your sales pipeline over any stretch of time: which deals are new, which were won or lost, which close dates got pushed, which amounts went up or down, which deals moved between forecast categories (Pipeline, Best case, Commit), and which deals are past their close date. It puts all of that on a live dashboard you can click through, and it writes the same data as plain files an AI agent can read.

![The pipeline-diff dashboard: a summary sentence, numbers that add up, and a waterfall from start to end pipeline](https://raw.githubusercontent.com/goran-revops/pipeline-diff/main/docs/overview.png)

I built it as an open-source alternative to the pipeline waterfall and deal inspection in tools like Clari and Gong, for RevOps, sales ops, and GTM teams. Revenue and sales ops teams pay for those views because the CRMs do not show week-over-week change well: HubSpot keeps no automatic snapshots, and Salesforce keeps about three months of trend history. pipeline-diff only reads from your CRM, and it rebuilds the past from the CRM's own change history, so the first run can already tell you what happened last week.

```
+----------------+   read-only   +------------------+     +-----------------------------------------+
|  HubSpot or    | ------------> |  pipeline-diff   | --> | data/  deals and every change, as JSONL |
|  Salesforce    |     sync      |                  |     +-----------------------------------------+
+----------------+               +------------------+                         |
                                                        +---------------------+---------------------+
                                                        |                     |                     |
                                                        v                     v                     v
                                               +----------------+   +------------------+   +------------------+
                                               |   dashboard    |   |      report      |   |      export      |
                                               |  live, visual  |   | summary.md, csv  |   | files for agents |
                                               +----------------+   +------------------+   +------------------+
```

## What you see

The dashboard opens with one sentence that says what happened, for example "Pipeline went from USD 8.96M to USD 9.50M (up USD 536k). Added +1.1M (15 created, 18 moved in). Won 202k (6 deals). Lost 331k (9 deals). Pushed out 4k (1 deal). Other -69k (13 raised in amount, 19 cut in amount, 1 deleted)." It calls out how much of the open pipeline is already past its close date. Under it, numbers that add up in money and in deals: start, added, won, lost, pushed out (with a quarter as the scope), other, and end. Then a waterfall: the pipeline value at the start, a bar for each kind of change, and the value at the end. Click a bar to list the deals behind it, and click a deal to see its full history: every stage, amount, close date, owner, and forecast category change, with the amount over time.

![Clicking the Won bar lists the won deals; clicking a deal opens its full history](https://raw.githubusercontent.com/goran-revops/pipeline-diff/main/docs/drilldown.png)

The other tabs:

- **All changes**: every deal that changed, searchable, with each value shown as before -> after and a link into the CRM. Downloads as CSV.
- **By owner**: what moved each rep's pipeline, as one bar per rep split by kind of change, with the dollars in a table.
- **By stage**: open pipeline per stage at the start and the end, and a grid of where deals moved from and to.
- **Forecast**: open pipeline per forecast category at the start and the end, and a grid of where each category's deals went: to another category, or won, lost, pushed out, or deleted, with the new deals that arrived. Each category's start, minus what left, plus what arrived, is its end, apart from amount changes.
- **Flags**: past due deals (open with a close date in the past), close dates pushed (with how far and how many times), deals whose forecast category went down, deals that moved back a stage, stalled deals, and reassigned deals, each with its count and dollar total.
- **Deal lookup**: any deal, found by name, with its full history.

![Forecast tab: pipeline by category at start and end, and where each category's deals went](https://raw.githubusercontent.com/goran-revops/pipeline-diff/main/docs/forecast.png)

![Flags tab: close dates pushed, forecast category down, and more, each with count and dollars](https://raw.githubusercontent.com/goran-revops/pipeline-diff/main/docs/flags.png)

You choose what to compare: since the previous sync, the last 7 or 30 days, quarter to date, or any dates. You choose the scope too: deals closing this quarter (the default, or next quarter in the last two weeks of a quarter), next quarter, or all open deals, and any mix of pipelines and owners. With a quarter as the scope, a deal whose close date moves out of the quarter shows up as pushed out, the way a forecast call talks about it.

## Try it without a CRM

```bash
pip install git+https://github.com/goran-revops/pipeline-diff
pipeline-diff demo
pipeline-diff dashboard
```

`demo` generates two years of demo pipeline history for an invented company: twelve reps, a new business pipeline and a renewals pipeline, a few thousand deals, and weekly snapshots for the last twelve weeks. `pipeline-diff demo --days 7300 --reps 20` makes twenty years for twenty reps, about 55,000 deals, which the dashboard still handles in seconds. Every company, person, and domain in it is made up, and the domains all end in example.com, example.org, or example.net.

## Connect HubSpot

Create a Service Key in your HubSpot account (Development, then Keys) with the `crm.objects.deals.read` and `crm.objects.owners.read` scopes. Forecast categories come from the deal's Forecast category property. A legacy private app token with the same scopes also works. Put it in a `.env` file in the folder you run pipeline-diff from:

```
PIPELINE_DIFF_HUBSPOT_TOKEN=your-key
```

pipeline-diff reads only its own `PIPELINE_DIFF_` settings from `.env`, so a `.env` in a folder you did not write cannot set a proxy or anything else. Then pull the data and open the dashboard:

```bash
pipeline-diff sync hubspot
pipeline-diff dashboard
```

## Connect Salesforce

pipeline-diff logs in with the OAuth client credentials flow, which Salesforce recommends for server-to-server access now that username and password logins are being retired. In Setup, open External Client App Manager and create an app with OAuth turned on, the "Manage user data via APIs (api)" scope, and Client Credentials Flow enabled. In the app's policies, set Run As to a user who can see your opportunities. To see owner changes over time, turn on field history tracking for Opportunity Owner.

```
PIPELINE_DIFF_SF_DOMAIN=yourorg.my
PIPELINE_DIFF_SF_CONSUMER_KEY=the-consumer-key
PIPELINE_DIFF_SF_CONSUMER_SECRET=the-consumer-secret
```

The domain is your My Domain URL without `.salesforce.com`, for example `yourorg-dev-ed.develop.my` for a Developer Edition org. Then run `pipeline-diff sync salesforce`.

## Keep it current

Each sync saves a snapshot, and the dashboard reloads the data every five minutes, so a dashboard left open on a second screen follows along. The dashboard has no login, so it listens only on your own machine (localhost) and nobody else on your network can open it. `--host` changes that, for a trusted network only. Schedule the sync once a day or once an hour:

```bash
# cron on macOS or Linux, every morning at 7
0 7 * * * cd /path/to/folder && pipeline-diff sync hubspot
```

```bash
# Windows Task Scheduler
schtasks /create /tn pipeline-diff /sc daily /st 07:00 /tr "cmd /c cd /d C:\path\to\folder && pipeline-diff sync hubspot"
```

## Reports and files for AI agents

`pipeline-diff report hubspot` writes `summary.md`, the waterfall in words with the deals behind each bar, ready to paste into Slack or an email, and `changes.csv` with one row per changed deal (its main bucket, its net value, and `parts`, which splits the value by bucket the way the summary counts it), to `data/hubspot/report/` unless you pass `--out`. It will not write into a folder that holds files it did not create, unless you add `--force`. Both take the same options as the dashboard, for example `--since sync`, `--since 30d`, or `--scope next-quarter`. Their default scope is all open deals.

`pipeline-diff export hubspot` writes a workspace folder (`data/hubspot/workspace/` unless you pass `--out`) laid out for an AI agent: a short `CLAUDE.md` that says where everything is, one card per deal with its full timeline, a folder per week with the summary and the changes by owner, and generated indexes. Point Claude or another agent at that folder and ask which deals were pushed this week and whose they are. The layout follows [Interpretable Context Methodology](https://github.com/RinDig/icm-architect) (Van Clief and McDermott): small routing files, plain files that hold the state, and nothing a person writes gets overwritten. Each deal has a `notes.md` for your own notes, and a past week's summary stays as you left it. `--refresh` rewrites only the current week, and keeps the old summary as `summary.previous.md`. Deal and owner names are written as plain text with links and images escaped, and the workspace tells the agent to treat them as data, because anyone who can edit a deal in your CRM can write them.

## How the numbers work

Every sync reads each deal's current values and the full history of its stage, amount, close date, owner, pipeline, and forecast category. From that history pipeline-diff can say what any deal looked like at any moment. A waterfall compares two moments and puts each deal in the bucket that says how it entered or left the pipeline, or how its amount changed if it stayed:

| Bucket | The deal |
| --- | --- |
| new | was created during the period |
| reopened | was closed at the start and is open at the end |
| moved in, pulled in | was open but outside the scope at the start, inside it at the end (pulled in: its close date moved into the window) |
| amount up, amount down | was in scope at both ends with a different amount |
| pushed out | left the scope because its close date moved past the window |
| moved out | left the scope while still open, for example to another pipeline or owner |
| won, lost | closed during the period, counted at the amount it closed at |
| removed | was deleted or merged in the CRM |

A deal that closes during the period belongs to the quarter of the close date it had just before it closed. HubSpot, and many reps, set the close date to the day a deal closes, so without this a deal planned for next quarter and won today would look like it was pulled into this one.

A deal that changed amount and then closed or was pushed shows in two bars: the amount change, then the bar it left by, at its final amount. That way won and lost show what deals really closed at, and a deal created and won in the same week shows in both new and won. Start plus every bar always equals end.

Flags sit beside the buckets: past due (open, close date in the past), stalled (no stage change in 30 days), stage forward or back, close date pushed or pulled, forecast up or down, and owner changed. They cover the deals in the scope at either end.

All times come from the CRM's own clock, read from its responses, not from your computer. A laptop clock that is a minute off would otherwise hide changes made during that minute.

## Test accounts

`pipeline-diff seed hubspot --test-account --baseline 100` fills a test account with invented deals, and `pipeline-diff seed hubspot --test-account` changes them in a round: some move stages, some change amount or close date, some are won, lost, deleted, or created. Each round records what every change should produce, and `pipeline-diff seed hubspot --verify` checks the next sync against it. It only ever touches deals it created. It asks the CRM what kind of account it is talking to and refuses anything but a HubSpot developer test account or sandbox, or a Salesforce Developer Edition org or sandbox, and it refuses to switch to a different account than the one it started in.

## What it does not do

- It never writes to your CRM, except `seed` on a test account.
- HubSpot deals deleted before your first sync are only visible if HubSpot still lists them as archived.
- Owner history in Salesforce needs field history tracking on Opportunity Owner. Without it, owner changes show up from the first sync on.
- It reads one currency per source and does not convert between currencies.
- You run it on your own machine. There are no logins, and the data stays in the folder you run it from. That data is real customer data. The data folder gets its own `.gitignore` on the first sync so git skips it, and the reports and workspace are written inside it by default. `changes.csv` prefixes cells that would start a spreadsheet formula.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests cover the waterfall rules on a hand-built history, both CRM clients against recorded API responses, a full seed, sync, and verify loop against an in-memory CRM, the agent workspace, and the dashboard, rendered headless.

## License

MIT
