# AI Data Analytics

An AI-powered take on Power BI Desktop: Get Data → Transform → Model → Report, with the AI
doing every step and each stage steered by a plain-English chat. See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

**Status:** M2 — Transform: AI-built Applied Steps on top of M1's Get Data and column profiling.

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
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # open http://localhost:5173
```

Single server (serves the built frontend):

```bash
(cd frontend && npm run build)
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000   # open http://localhost:8000
```

Then open **Settings** and click **Test connection**. It checks the key and model, and
whether the provider supports tool calling and JSON mode (used by the stage agents).

## Get Data

Create a project on the Home page, then add data on its **Get Data** tab:

- **Files:** drop CSV (any delimiter) or Excel `.xlsx` files. Each Excel sheet with data becomes a table.
- **Google Sheets:** paste the sheet's link. It must be shared as "Anyone with the link can view".
- **Sample data:** a deliberately messy sales, customers and products set from [samples/](samples/).

Every table is loaded as raw text (types and cleaning are Transform steps) and profiled:
detected type, blanks, distinct values, ranges, common values and data quality issues. With
an API key set, the AI then describes the table and each column. It sees only the profile and
5 sample rows, never the full data. Project data is stored in `data/projects/` (one DuckDB
file per project).

## Transform

Each loaded table gets a query on the **Transform** tab. **Clean with AI** has the AI propose
Applied Steps from a fixed catalogue (change type, trim, change case, replace values, blanks
to null, fill blanks, remove duplicates, remove or rename columns, filter rows, merge, append).
The AI never writes SQL: the engine checks each step against the columns at that point,
compiles it to SQL itself and refuses steps that fail, break another table, or make a merge
multiply rows. Steps replay from the raw table on every change, are marked `AI` or `Approved`
with the AI's reason, and can be reordered, removed or undone. Click a step to see its rows
before and after, and its impact. The results are stored as tables in the project's `clean`
schema, which the Model stage will read.

## Tests

```bash
cd backend && ../.venv/bin/pytest
cd frontend && npm run typecheck
```

Backend tests use a fake AI client, so they run without a key or network access.
