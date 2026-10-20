# Implementation Plan — AI Data Analytics Web App

An AI-powered take on Power BI Desktop: the same Get Data → Transform → Model → Report flow,
but the AI does every step. There are no manual tools. Users steer each stage in plain English
through a chat panel whose powers are scoped to that stage (similar to Snowflake Cortex Code).

Product requirements: [AI Data Analytics Web App — Product Requirements](https://claude.ai/code/artifact/d1d31ca6-f2a5-4981-8233-16842f397991)

## Decisions so far

| Topic | Decision |
| --- | --- |
| Login and workspaces | Skipped for local testing; added in the hosted phase |
| AI provider | `ilmu-glm-5.1` via `https://api.ilmu.ai/v1` (OpenAI-compatible), using the `openai` Python SDK |
| Provider setup | Base URL, model and key are settings, so other OpenAI-compatible providers plug in |
| API key | Local `.env` or the Settings page; never committed, never sent to the browser |

## Core principle: the AI decides, the engine executes

The AI never runs code or SQL directly. Each stage asks the AI for a structured plan made of
operations from a fixed catalogue. The backend validates the plan, compiles it to SQL and runs
it on DuckDB.

- **Repeatable:** saved steps replay the same way on refreshed data, without calling the AI again.
- **Undoable:** each stage's steps are an ordered list, like Power Query's Applied Steps.
- **Validated:** joins and relationships are checked for row counts, key uniqueness and match rate before they are accepted.
- **Private:** the AI sees column profiles and a few sample rows, never the full dataset.
- **Traceable:** every step, relationship, measure and visual is marked `AI` or `Approved`, with the AI's reason.

## Stack

| Part | Choice |
| --- | --- |
| Backend | Python, FastAPI |
| Engine | DuckDB (one database file per project, external file access disabled for AI-written SQL) |
| Ingestion | pandas + openpyxl |
| Frontend | React + Vite + TypeScript |
| Charts | ECharts |
| AI client | `openai` SDK pointed at any OpenAI-compatible base URL |

## App sections

Global pages:

| Section | Power BI equivalent | Purpose |
| --- | --- | --- |
| Home | Start screen | List, create and open projects |
| Settings | Options | AI provider, base URL, model, API key, connection test |

Inside a project, a left rail in pipeline order:

| Tab | Power BI equivalent | What the AI does | What the user sees |
| --- | --- | --- | --- |
| Get Data | Get Data | Profiles every new source | Sources, table previews, column profiles |
| Transform | Power Query | Builds Applied Steps to clean and merge tables | Steps list with reasons and badges, before/after preview |
| Model | Model view | Designs relationships, date table and measures | Model diagram, relationships, measures with values |
| Report | Report view | Picks visuals and layout, writes insights | Report pages with charts and insight sentences |
| Ask | Q&A | Answers questions from the model (read-only) | Answers, charts, and how each number was calculated |
| Export | Export to PDF / PowerPoint | Restructures the report into a narrative | PDF (deck later) |

Shared on every project tab:

- **Stage chat panel** on the right, scoped to the current tab (below).
- **Run all** button: runs every stage in full-AI mode.
- **Mode switch:** *Full AI* applies changes immediately; *Review* shows each change for approval first.
- **Stale markers:** when an upstream change affects a later stage, that tab is flagged "needs re-run".
- **Activity log** drawer: every AI and human change across stages, with undo.

## Stage-scoped chat agents

Each tab has its own agent. All agents can **read** the context from earlier stages (schemas,
steps, model) but can only **change** their own stage.

| Agent | Can change | Example requests |
| --- | --- | --- |
| Get Data | Add a Google Sheet by link, rename or remove a source, re-profile | "Rename sales_2024 to Sales" |
| Transform | Add, edit, remove or reorder steps; preview a step | "Don't merge orders with returns", "Treat blank region as Unknown" |
| Model | Relationships, measures, date table, hidden columns, formats | "Add a profit margin measure", "Make the date table start in 2022" |
| Report | Pages, visuals, layout, titles, insights | "Swap the pie for a bar chart", "Add a page on customer churn" |
| Ask | Nothing (read-only) | "Why did Q3 sales drop?" |
| Export | Narrative, section order, which visuals to include | "Make the summary shorter" |

How a chat request works:

1. The agent receives the request, the current stage state and upstream context.
2. It returns a change set using only its stage's operations (tool calls, or a JSON action list if the provider lacks tool calling; checked in M0).
3. The backend validates the change set and reports its impact, for example "removes column `region`, used by 2 measures".
4. In Full AI mode the change applies immediately; in Review mode the user approves it. Either way it is logged and undoable.
5. Affected later stages are marked stale, and their own agent can repair them.

## Milestones

Each milestone is a separate commit with tests that use a fake AI, so they run without a key.
A deliberately messy sample dataset (sales, customers, products) ships with the repo for
end-to-end checks.

| # | Delivers | You can test |
| --- | --- | --- |
| M0 | Project setup, Settings page, ilmu connection test (including tool-calling support check) | Your key works |
| M1 | Get Data: CSV, Excel, Google Sheets link; column profiling | Load files, see tables and profiles |
| M2 | Transform engine (operation catalogue, replay, undo) + AI cleaning | AI builds Applied Steps |
| M3 | Model: relationships, date table, measures, model diagram | AI-built star schema |
| M4 | Report builder with ECharts and AI insights | A full AI dashboard |
| M5 | Stage chat agents, Run all, Full AI / Review mode, stale markers | Steering each stage in English |
| M6 | Ask tab and PDF export | The end-to-end flow |

## Path to public, paid use

Ship in three phases. Only the cheap choices that make hosting easier are made now: the
provider abstraction, a storage interface for project state, and a `workspace_id` on every
record from day one.

| Phase | Adds |
| --- | --- |
| 1. Local (now) | Single user, local files, your own API key |
| 2. Hosted private beta | Login (e.g. Clerk or Supabase Auth), workspaces and roles, Postgres for metadata, object storage for uploads, a job queue for AI runs, encrypted secrets, usage metering per AI call |
| 3. Paid launch | Stripe billing, plan limits, shared dashboards with viewer seats, scheduled refresh, database connectors, terms of service and a data processing agreement |

Main cost and risk drivers for the paid version:

- **AI usage is the main variable cost.** Meter tokens per stage run and price plans around AI credits. Offer a bring-your-own-key option.
- **Customer data leaves their hands.** Publish exactly what is sent to the AI (profiles and samples), offer data residency options, and follow the privacy law of each market served (for example Malaysia's PDPA).
- **Wrong numbers cost trust.** The validation checks and "how this was calculated" view are part of the product, not polish.

Illustrative plans, to be set from measured AI cost per project during the beta:

| Plan | For | Includes |
| --- | --- | --- |
| Free | Trying it out | 1 project, file uploads, limited AI credits |
| Pro (per creator) | Individual analysts | More projects and credits, Google Sheets refresh, PDF export |
| Team | Organizations | Shared workspaces, viewer seats, database connectors, admin controls |
