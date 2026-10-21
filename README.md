# datawazi

datawazi is an AI-powered take on Power BI Desktop: Get Data → Transform → Model → Report, with the AI
doing every step and each stage steered by a plain-English chat. See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

**Status:** M9 — datasets with linked tables and lineage, dashboards, stage chats, Ask, PDF export and data refresh.

## Requirements

- Python 3.11+
- Node.js 20+

## Setup

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
(cd frontend && npm install)
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt -r backend\requirements-dev.txt
cd frontend; npm install; cd ..
```

On Windows, replace `../.venv/bin/` with `..\.venv\Scripts\` in the commands below.

Add your API key either in the app's Settings page, or in a `.env` file at the repo root
(copy `.env.example`). The key is stored only on your machine and is never sent to the browser.

## Run

Development (two terminals, with hot reload):

```bash
cd backend && ../.venv/bin/uvicorn app.main:default_app --factory --reload --port 8000
cd frontend && npm run dev          # open http://localhost:5173
```

Single server (serves the built frontend):

```bash
(cd frontend && npm run build)
cd backend && ../.venv/bin/uvicorn app.main:default_app --factory --port 8000   # open http://localhost:8000
```

Then open **Settings** and click **Test connection**. It checks the key and model, and
whether the provider supports tool calling and JSON mode (used by the stage agents).

## Sign-in, workspaces and roles

Without sign-in settings, datawazi runs as one person on their own computer, in the
**Local workspace**. To turn sign-in on, create a Supabase project (Singapore region) and add its
URL and publishable key to `.env` (see `.env.example`). Then:

- People sign in with Google, Microsoft or an emailed link. A new account first creates a workspace for its company.
- Each workspace keeps its own datasets and dashboards in its own folder (`data/workspaces/<id>/`).
  - Nobody can open another workspace's data. The API checks this on every request, and the tests try every endpoint.
- **Members** (admins only) creates invite links for an email address and a role. Links last 14 days.
- Roles:
  - **Viewers** see published dashboards, ask questions about them and export them.
  - **Creators** also build and publish datasets and dashboards.
  - **Admins** also manage people.
- **Members** emails invites through your Supabase email setup when `SUPABASE_SECRET_KEY` is set.
  Each email has a sign-in link that lands on the invite, and the link is also copied to send by hand.
- With `DATABASE_URL` set, workspaces, members, invites and dataset and dashboard records live in
  Supabase Postgres (schema `datawazi`, not exposed to the public data API).
  - Every request runs as the signed-in user, so row-level security in Postgres also keeps
    companies apart, and viewers can't change anything.
  - Invite links are stored only as hashes.
  - Data files stay in `data/workspaces/<id>/`. Each published dataset version is also kept in Supabase
    Storage (private bucket `datawazi`), so dashboards survive a new server.
  - On first start, the local records are copied into Postgres. After that, the JSON files are no longer used.
  - Tests run the sign-in and role tests against both stores. The Postgres runs use a throwaway schema
    in the project's database, dropped afterwards.
- Only the emails in `PLATFORM_ADMINS` can change the AI provider in Settings.
  - The first of them to sign in inherits the data built before sign-in was turned on.

## Datasets and dashboards

The app follows Power BI's shared-model practice: data is prepared once and reused.

- A **dataset** holds Get Data, Transform and Model. **Publish** it to freeze a version for dashboards.
  - Before publishing, the app lists any dashboard visuals the change would break, and you confirm first.
- A **dashboard** holds report pages built on one dataset's published version. It has a draft that
  creators edit and a published version that viewers see.
  - Editing the dataset never changes a live dashboard until you publish the dataset again.
- On Home, **New dashboard** offers **Use existing data** (pick a published dataset) or
  **Bring new data** (create a dataset first; publishing it leads back to the dashboard).

Moving around:

- The sidebar holds Home, Datasets, Dashboards and Settings. The top bar shows where you are.
- Press **Ctrl+K** (⌘K on a Mac) to jump to any dashboard or dataset.
- On a dataset, the step bar shows Get Data, Transform, Model, Publish and Dashboard.
  - Finished steps are ticked, and the current one is ringed in yellow.
  - The button on the right takes you to the next step.
- On a dashboard, **Present** shows the pages full screen. Press Esc to leave.
- While the AI is replying, **Stop** stops waiting for the reply.
  - Stopping does not undo a change the AI has already started making.

Projects from earlier versions are moved over automatically. Each becomes a dataset, plus a
dashboard if it had report pages.

## Power BI import and export

**Import:** in Get Data, choose **Import from Power BI**.

- **A `.pbix` file** brings its model and its data:
  - tables, already clean, because Power BI ran their steps before saving
  - relationships, checked like any other
  - measures
- **A `.pbip` project** (zip the project folder) brings the model only.
  - Load each table's data under the same name, then click **Finish import**.
  - Or save the project as `.pbix` in Power BI Desktop and import that.
- **DAX is translated into datawazi measures:**
  - SUM, AVERAGE, MIN, MAX, COUNT, COUNTROWS and DISTINCTCOUNT
  - the X iterators
  - DIVIDE and arithmetic between measures
  - CALCULATE with simple filters on the measure's own table
- **Mixed measures are split.** A measure mixing an aggregation with other measures is split into a helper measure.
- **The rest is kept, not guessed.** Anything else (IF, time intelligence, filters on other tables) stays in the import report, with its DAX and the reason.
- **Date tables.** Power BI's date table, and its automatic date tables, become datawazi's Date table.
- **Safety.** Nothing in the file is run. DAX and Power Query are read as text.

**Export:** on a dataset, choose **⋯ → Download for Power BI**. You get a `.pbip` project in a zip:

- the model in TMDL, with measures written as DAX
- relationships, and the Date table marked as a date table
- each table's clean data as Parquet

Unzip it, open the `.pbip` in Power BI Desktop, set the `DataFolder` parameter if you unzipped it elsewhere, and click Refresh. For a `.pbix`, use **File → Save as**.

Tested both ways with a real `.pbix` (60,000 sales rows): every measure gave the same result, by year, in datawazi and in Power BI Desktop.

## Linked tables, certified datasets and lineage

- **Linked tables:** in Get Data, **Link a table from another dataset** copies a clean table,
  such as a shared Customers table, from another dataset's published version.
  - The copy is read-only and keeps its column types.
  - It shows **update available** when that dataset publishes again, and **Refresh** pulls the
    new version through the usual checks. Scheduled pulls include linked tables.
- **Certified datasets:** mark trusted shared datasets as **Certified**. They are listed first
  when linking, and each dataset shows which other datasets link to it.
- **Several fact tables:** one dataset can hold more than one fact table, for example sales and
  targets. Relate each to the shared dimensions, and their measures combine when split by a
  shared column. The Model AI and chat follow this rule.
- **Lineage:** the **Lineage** tab shows sources → tables → measures → dashboard visuals. Click
  any item to highlight everything that feeds it and everything it feeds.

## Ask

Every published dashboard has an **Ask** panel, open to anyone who can see the dashboard. It
answers questions about the data in plain words.
- The AI never writes SQL. It picks from the dataset's measures and columns, and datawazi
  calculates the numbers with the same engine the dashboard uses.
- Each answer shows **How this was calculated**: the measures and their definitions, what it
  was split and filtered by, and which tables were used.
- Numbers in the answer are checked against the results, and any that don't match are
  flagged "Unverified".
- The conversation stays in the viewer's browser tab.

## Stage chat

Every dataset stage and every dashboard draft has an AI chat that answers questions and
makes changes to that stage only. Each change goes through the same checks as the buttons.
- **Full AI** applies changes at once, and they can be undone.
- **Review** shows the checked plan for approval first.
- When an earlier stage changes, later stages show "needs re-run". An **Activity** log lists
  every change.

## PDF export

**Export PDF** on a published dashboard opens a print-ready copy:
- a cover with the dashboard name, the data's "as of" date, the dataset version and the export date
- one A4 landscape sheet per page, with KPI cards, charts, tables and insights
- an optional **AI executive summary**, with every number checked against the dashboard's data

Click **Save as PDF** and choose "Save as PDF" in the print dialog. Charts are SVG, so the PDF
stays sharp.

## Refresh

Each dataset has a **Refresh** tab. **Replace file** loads newer data for an uploaded table, and
**Pull now** re-downloads a Google Sheet; sheets can also be pulled every hour, every 6 hours,
daily or weekly. Tables keep their names, so Applied Steps, the model and dashboards stay
attached.

Every refresh is checked against the data before it:
- row counts, and columns that disappeared
- tables whose steps now fail
- broken relationships or measures, and relationship match rates that dropped
- dashboard visuals that would break

With **Publish automatically** on, a refresh that passes goes live. A failed one never does,
so dashboards keep the last good version. Every run is kept in the refresh history.

## Get Data

Create a dataset on the Home page, then add data on its **Get Data** tab:

- **Files:** drop CSV (any delimiter) or Excel `.xlsx` files. Each Excel sheet with data becomes a table.
- **Google Sheets:** paste the sheet's link. It must be shared as "Anyone with the link can view".
- **Sample data:** a deliberately messy sales, customers and products set from [samples/](samples/).

Every table is loaded as raw text (types and cleaning are Transform steps) and profiled:
detected type, blanks, distinct values, ranges, common values and data quality issues. With
an API key set, the AI then describes the table and each column. It sees only the profile and
5 sample rows, never the full data. Datasets are stored in `data/datasets/` (one DuckDB file
per dataset, plus a frozen copy per published version) and dashboards in `data/dashboards/`.

## Transform

Each loaded table gets a query on the **Transform** tab. **Clean with AI** has the AI propose
Applied Steps from a fixed catalogue (change type, trim, change case, replace values, blanks
to null, fill blanks, remove duplicates, remove or rename columns, filter rows, merge, append).
The AI never writes SQL: the engine checks each step against the columns at that point,
compiles it to SQL itself and refuses steps that fail, break another table, or make a merge
multiply rows. Steps replay from the raw table on every change, are marked `AI` or `Approved`
with the AI's reason, and can be reordered, removed or undone. Click a step to see its rows
before and after, and its impact. The results are stored as tables in the project's `clean`
schema, which the Model stage reads.

When one table repeats the details of something with its own key (for example product code,
name and category on every order line), the AI can **split** those columns into a lookup
table with one row per key, then remove them from the original. A lookup table is a copy of
the original as it was at that step, so later steps there don't change it. Lookup tables are
marked `AI` until you approve them, and can be removed or undone like any step.

## Model

**Build model with AI** designs a star schema over the Transform output: many-to-one
relationships from fact to dimension tables, a generated **Date** table, and measures. The
engine checks every relationship before keeping it. The "one" side must have one row per
key, the key types must match, and at least half the keys must match. Unmatched keys are
reported. Only one active path may join two tables, so extra relationships are kept
inactive, as in Power BI. Measures are an aggregation (sum, average, count, distinct count,
min, max) over a `[Column]` expression such as `[Qty] * [Unit Price]`, or a formula over
other measures such as `[Revenue] / [Orders]`. The app compiles them itself, so the AI never
writes SQL. The Model tab shows the diagram, each relationship's match rate, and every
measure's current value. Changes can be approved or undone.

## Report

**Build report with AI** designs pages that each answer one question. It uses KPI cards,
bar charts sorted by value, trend lines, donuts only for three slices or fewer, and detail
tables. It then writes one insight per visual and a summary per page. The look follows the
house style for business users:
- A light theme with colour-blind-safe colours.
- At most 7 KPI cards per page.
- Each KPI card shows the value, the change against the same period last year (arrow, sign,
  amount and %), a 12-month sparkline and the top driver. When no comparison is possible,
  the card says why.
- No gauges and no dual axes.

Every number comes from the model. Breakdowns are checked to add up to the total, and the
numbers in each insight are checked against that visual's data at the precision written.
Anything unconfirmed is flagged "Unverified".

## Tests

```bash
cd backend && ../.venv/bin/pytest
cd frontend && npm run typecheck
```

Backend tests use a fake AI client, so they run without a key or network access.
