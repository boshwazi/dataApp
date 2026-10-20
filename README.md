# AI Data Analytics

An AI-powered take on Power BI Desktop: Get Data → Transform → Model → Report, with the AI
doing every step and each stage steered by a plain-English chat. See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

**Status:** M0 — project setup, Settings page and AI connection test.

## Requirements

- Python 3.11+
- Node.js 20+

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
(cd frontend && npm install)
```

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
whether the provider supports tool calling and JSON mode (used by the stage agents later).

## Tests

```bash
cd backend && ../.venv/bin/pytest
cd frontend && npm run typecheck
```

Backend tests use a fake AI client, so they run without a key or network access.
