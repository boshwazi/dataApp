# Implementation Plan — datawazi

**datawazi** is an AI-powered take on Power BI: the same Get Data → Transform → Model → Report flow,
but the AI does every step. There are no manual tools. Users steer each stage in plain
English through a chat panel whose powers are scoped to that stage (similar to Snowflake
Cortex Code). It launches as a public, paid SaaS product.

Product requirements: [AI Data Analytics Web App — Product Requirements](https://claude.ai/code/artifact/d1d31ca6-f2a5-4981-8233-16842f397991)

## Decisions so far

| Topic | Decision |
| --- | --- |
| Name | **datawazi** |
| Market | Public SaaS from day one: any company signs up, gets a workspace and pays a subscription |
| Structure | **Datasets** (Get Data, Transform, Model) are reusable; **Dashboards** (Report, Ask, Export) are built on a dataset |
| Roles | Admin, Creator, Viewer per workspace |
| Platform | Supabase: Postgres with row-level security, Auth, file storage |
| Payments | CHIP first (cards and FPX, lowest cost), behind a provider interface so Stripe and Bayarcash can be added |
| AI costs | We run the AI; every plan includes monthly AI credits, with top-ups |
| Plans | Free, Pro (per creator), Team (viewer seats, admin controls) |
| Landing and login look | Calm, serious B2B, light, consistent with the dashboard house style |
| AI provider | `ilmu-glm-5.1` via `https://api.ilmu.ai/v1` (OpenAI-compatible); base URL, model and key are settings |
| Workflow | Build and test locally, commit to `main`, push. No feature branches or PRs |

## Core principle: the AI decides, the engine executes

The AI never runs code or SQL directly. Each stage asks the AI for a structured plan made of
operations from a fixed catalogue. The backend validates the plan, compiles it to SQL and runs
it on DuckDB.

- **Repeatable:** saved steps replay the same way on refreshed data, without calling the AI again.
- **Undoable:** every stage keeps a history, so one click reverts the last change.
- **Validated:** a change is refused if it would break the data. Examples: a join that multiplies
  rows, a key that repeats, a type change that blanks most of a column, or a breakdown that
  doesn't add up to the total.
- **Private:** the AI sees column profiles, a few sample rows and aggregates, never the full dataset.
- **Traceable:** every step, relationship, measure and visual is marked `AI` or `Approved`, with the AI's reason.
- **Injection-resistant:** text inside the data cannot make the AI run anything. The AI can only
  return catalogue operations, and each is checked before it runs.

## Status

| # | Delivers | State |
| --- | --- | --- |
| M0 | Project setup, Settings page, connection test (including tool-calling support check) | Done |
| M1 | Get Data: CSV, Excel, Google Sheets; column profiling; AI table descriptions | Done |
| M2 | Transform: operation catalogue, Applied Steps, undo, AI cleaning, lookup-table splits | Done |
| M3 | Model: relationships with checks, date table, measures, diagram | Done |
| M4 | Report: KPI cards, charts, checked insights (house style) | Done (first draft) |
| M5 | Datasets + Dashboards, draft and publish, new or existing data on Home, migration | Done |
| M6 | Stage chat agents, Full AI and Review modes, stale markers, activity log | Done |
| M7a | Ask on published dashboards: answers from the model's measures with "how this was calculated" | Done |
| M7b | PDF export: print-ready pages, cover, AI executive summary with checked numbers (browser "Save as PDF") | Done |
| M8 | Data refresh: replace files, scheduled Google Sheets pulls, checks on refresh, auto-publish, refresh history | Done |
| M9 | Linked tables from other datasets, certified datasets, multiple fact tables, lineage view | Done |
| M10a | Sign-in (Supabase Auth tokens checked on every request), workspaces, admin/creator/viewer roles, invite links, a folder per workspace, isolation tests across every endpoint | Done (needs a Supabase project to sign in for real) |
| M10b | Postgres (schema `datawazi`) for workspaces, members, hashed invites and records, with row-level security on every table and requests run as the signed-in user; invite emails via Supabase; published versions backed up to Supabase Storage; one-time import of local data | Done (draft data files still on the server's disk) |
| M11 | Power BI: import .pbix (model and data, via PBIXRay) and .pbip (TMDL model, then data); DAX translated to measures with helper splits and an import report; export a .pbip (TMDL, DAX, Parquet data) that opens and matches in Power BI Desktop | Done |
| M11b | Power BI follow-ups: connector and snapshot labels per table, DirectQuery and live-connection files flagged, refresh from a newer .pbix through the refresh checks | Done |
| M13 (first part) | Professional workspace: icon rail, Ctrl I AI panel, Transform grid with a column menu, model diagram with drag-to-relate and a formula bar, report canvas with Build and Format panes , page slicers and filters respected by every visual, Ask and chat , click to cross-filter, date drill-down, Merge and Append dialogs, colour tokens and a dark theme, one stylesheet with dead CSS removed | Done; alignment guides, density and analyst testing still to do |
| M12 (first part) | Time intelligence, calculated columns, related-table measure filters, IF and SWITCH, inactive relationships; formula bar, AI and Power BI DAX in and out | Done; the AI engine work (evaluation suite, tool loop, Explain per visual) still to do |
| UX | Direction A "Clear Analyst": new tokens and font, sidebar shell, breadcrumbs, Ctrl+K search, dialogs and toasts, pipeline stepper with a next step, Present mode, formatted chat replies with Stop, phone and tablet layouts | Done |
| UX2 | Data-platform look (Databricks/Snowflake-like): dark console sidebar with sections, Inter and JetBrains Mono, dense catalog tables with filter, compact controls; more chart types (pie, stacked, area, scatter) with house-style tips instead of refusals; Removed rows per Transform step | Done |

## Product structure

The app copies Power BI's best practice of one shared semantic model with many thin reports
on top. Changes to the model flow to every report, and nobody rebuilds the same data twice.

```
Workspace (a company)
├── Members: Admin / Creator / Viewer
├── Datasets  ── Get Data → Transform → Model     (reusable, versioned: draft / published)
└── Dashboards ── built on one published dataset ── Report pages · Ask · Export
```

- **Home** offers **New dashboard → Use existing data** or **Bring new data**. "Bring new data"
  runs the dataset steps first, then continues into the dashboard. Both paths end with the AI
  building the report.
- **Datasets** have a draft and a published version. Dashboards always read the published
  version, so a creator can rework a dataset without breaking live dashboards. Publishing checks
  every dependent dashboard and flags visuals that would break (for example, a removed measure).
- **Dashboards** have a draft and a published version too. Viewers only ever see published dashboards.
- Today's "project" becomes one dataset plus one dashboard, which makes migration simple.

## Roles

| Can… | Admin | Creator | Viewer |
| --- | --- | --- | --- |
| See published dashboards and use their Ask chat | ✓ | ✓ | ✓ |
| Export dashboards to PDF | ✓ | ✓ | ✓ |
| Build and publish datasets and dashboards, use stage chats | ✓ | ✓ | |
| Invite people, change roles | ✓ | | |
| Billing, plan, AI credit top-ups | ✓ | | |
| Workspace settings, delete workspace, see audit log | ✓ | | |

Later: row-level security inside a dataset (for example, viewers in a region see only their
region), set by admins per role.

## Stage chat agents (every stage)

Each stage has a chat panel on the right. Its agent can **read** everything upstream and can
only **change** its own stage. It also answers questions about the current state ("why is this
relationship inactive?").

| Agent | Can change | Example requests |
| --- | --- | --- |
| Get Data | Add a Google Sheet by link, rename or remove a source, re-profile | "Rename sales_2024 to Sales" |
| Transform | Add, edit, remove or reorder steps; split lookup tables | "Don't merge orders with returns", "Treat blank region as Unknown" |
| Model | Relationships, measures, date table, hidden columns, formats | "Connect orders to products by SKU", "Add a profit margin measure" |
| Report | Pages, visuals, titles, insights | "Swap the donut for a bar chart", "Add a page on customer churn" |
| Ask (viewers too) | Nothing (read-only) | "Why did Q3 sales drop?" |
| Export | Narrative, section order, which visuals to include | "Make the summary shorter" |

How a chat request works:

1. The agent receives the request, the stage's state and upstream context.
2. It replies with an answer, a change set made only of its stage's operations, or both.
3. The backend validates the change set and states its impact, for example "removes column
   `region`, used by 2 measures".
4. In **Full AI** mode the change applies at once. In **Review** mode the user approves it first.
   Either way it is logged and can be undone.
5. Later stages and dashboards affected by the change are marked "needs re-run", and their
   own agent can repair them.

The Ask agent answers from the model with `evaluate`. It shows each number with "how this was
calculated" (the measure, its filters, the tables used), which builds trust.

## Multi-dataset models

People will want one dashboard over several sources, for example sales from the POS system,
targets from a Google Sheet and headcount from HR. Power BI's guidance is to extend a governed
model with a little extra data, rather than stitch many models together, and to share
dimensions such as Date, Customer and Product across facts
([Microsoft Learn](https://learn.microsoft.com/en-gb/power-BI/guidance/composite-model-guidance),
[PowerBI.tips](https://powerbi.tips/2025/03/07/managing-multiple-datasets-ep-404/),
[Tabular Editor](https://tabulareditor.com/blog/composite-models-in-power-bi-and-fabric)).
datawazi follows the same idea in three levels:

1. **Many sources in one dataset (works today).** A dataset can hold any number of files and
   sheets, and the model can have several fact tables. For example, `sales` and `targets` can
   both relate to shared `Date` and `Product` dimensions, which Power BI calls a galaxy schema.
   Measures from different facts sit side by side in one visual because they share those dimensions.
2. **Linked tables from other datasets (built in M9).** In Get Data, choose "From another
   dataset" and pick clean tables from any published dataset, such as a certified `Customers`
   table.
   - The link is read-only and keeps its lineage ("from Customer Master v4").
   - When the source dataset publishes a new version, the linked table shows "update available".
   - Updating re-checks the relationships and dashboards that use it.
   - Copies are made by the app, never by AI-written SQL.
3. **Certified datasets (built in M9).** Admins mark core datasets, such as Customer Master and
   Calendar, as certified. Certified datasets appear first when linking, and changes to them
   list every dataset and dashboard downstream.

The Model agent gets new actions: link a table from another dataset, and relate a second fact
table through shared dimensions. It also warns about the classic many-facts traps: totals that
don't add up across facts, and filters that can't reach a fact because there's no path. A
lineage view shows source → steps → model → visuals for any number on a dashboard.

## Feature roadmap (research summary)

The 2026 consensus is that natural-language questions and AI-written insights are now
expected. Trust comes from a governed semantic layer and "show your work". Text-to-SQL over raw
tables is unreliable on real schemas, while answers that compile through defined measures are
far more accurate
([Cube](https://cube.dev/articles/what-is-conversational-bi),
[Teradata](https://www.teradata.com/insights/data-analytics/what-is-conversational-analytics),
[TDWI](https://tdwi.org/webcasts/2026/09/biz-all-expert-panel-semantic-layers-the-foundation-for-trusted-generative-bi.aspx),
[Big Data Boutique](https://bigdataboutique.com/talk-to-your-data)).
datawazi is built that way already. These are the features that would add the most for
business users, in rough priority order:

| Feature | Why it matters | Notes |
| --- | --- | --- |
| **Data refresh** | Dashboards go stale without it; this is the most common BI need after building | Re-pull Google Sheets on a schedule. Upload a new file to replace an old one, keeping its steps. The checks run on refresh, then auto-publish if they pass. Refresh history. |
| **Alerts and digests** | People don't open dashboards daily; Tableau Pulse made this the expected default | "Tell me when Revenue drops 10% vs last month." Weekly AI digest of what changed, by email (WhatsApp later for Malaysia). |
| **Sharing** | Viewers need an easy way in | Invite viewers by email, share links with expiry, embed in Notion or an intranet, scheduled PDF by email. |
| **Row-level security** | One dashboard for every region or branch | Rules per role ("Region = viewer's region"). The Ask agent respects them too. |
| **Data quality monitors** | Wrong numbers cost trust | After each refresh: row-count drops, new unknown categories, blanks rising, broken relationships. Alerts the creator. |
| **Database and cloud connectors** | Companies keep data in systems, not files | Postgres, MySQL, SQL Server, BigQuery, Excel on OneDrive and SharePoint, Google Drive folders. Read-only credentials, kept encrypted. |
| **Templates** | Faster first value | Starter dashboards (sales, finance, HR, inventory) that map onto your columns with AI help. |
| **Bahasa Malaysia** | Home market | Interface and AI answers in BM or English, per user. |
| **Comments and annotations** | Turns dashboards into a team conversation | Comment on a visual, @mention people, annotate a spike ("promo week"). |
| **Forecast and what-if** | A common follow-up question | Simple, explained trend forecasts with ranges; "what if price +5%" on a measure. The method is always shown. |
| **Version history** | Safety for creators | Every published version is kept; compare and roll back datasets and dashboards. |
| **Usage analytics** | Admins see value | Most-viewed dashboards, popular questions, AI credit use per workspace. |
| **Mobile view** | Managers read on phones | A single-column layout for published dashboards and Ask. |

## Platform architecture (hosted)

| Part | Choice |
| --- | --- |
| Frontend | React + Vite + TypeScript (as now), served from a CDN |
| API | FastAPI (as now), in containers, stateless |
| Metadata | Supabase Postgres. Every table carries `workspace_id`, with row-level security on it |
| Sign-in | Supabase Auth: email magic link, Google, Microsoft. SAML SSO for Team customers later |
| Files | Supabase Storage, per workspace: uploads, plus one DuckDB file per dataset version |
| Compute | DuckDB in worker processes. Each request opens only its own workspace's files |
| Jobs | A queue for AI runs, refreshes and PDF exports, so the API never blocks on the AI |
| AI | Server-side provider key; per-call usage recorded against the workspace's credits |
| Region | Singapore, Supabase's closest region to Malaysia. Revisit if a customer requires Malaysian hosting |

The code already goes through one storage interface (`ProjectStore`) and stamps
`workspace_id` on every record, so moving from local files to Supabase is mostly a swap of
that layer.

## Security

- **Tenant isolation, two layers.** The API resolves the workspace from the signed-in user's
  verified token and checks membership and role on every request. Postgres row-level security
  also enforces `workspace_id`, so a missed filter in code can't leak another company's data.
  Storage paths are per workspace, and file links are signed and short-lived.
- **Tests for isolation.** Every endpoint is tested as a user from another workspace and must
  return 404. The same goes for viewers trying creator actions.
- **Sign-in.** Supabase tokens are verified in FastAPI on every request, with short sessions and
  refresh rotation. Admins can require Google or Microsoft sign-in for their workspace.
- **Secrets.** The AI provider key, payment keys and the database service key live in the host's
  secret store. They are never sent to the browser, and never logged.
- **The AI's reach.**
  - The AI returns only catalogue operations. It never writes SQL or code.
  - DuckDB runs with external file and network access turned off.
  - The Ask agent is read-only.
  - Data sent to the AI is limited to profiles, 5 sample rows and aggregates. A workspace
    setting turns sample rows off.
- **Uploads.** Size limits, CSV and Excel only, parsed in a worker. No macros or formulas are
  executed (openpyxl reads values only).
- **Payments.** Card details never touch our servers: CHIP's hosted checkout and saved-card
  tokens handle them. Webhooks are verified by signature, and each event is processed once.
- **Operations.**
  - Encryption in transit (TLS) and at rest (Supabase default).
  - Daily backups with point-in-time recovery.
  - An audit log of every AI and human change.
  - Rate limits per user and per workspace.
  - Error monitoring that strips data values.
- **Privacy and legal (Malaysia PDPA).**
  - Privacy notice, terms of service and a data processing agreement.
  - A published list of exactly what is sent to the AI provider, and where it is processed.
  - Workspace data export and deletion on request, and a retention policy.
  - Cross-border transfer (Singapore hosting, the AI provider's region) reviewed with a lawyer before launch.
- **Before launch.** Run a `/security-review` of the code and an external penetration test.

## Billing and payments

**Provider: CHIP first**, behind a `PaymentProvider` interface:

| Provider | Card | FPX | Recurring | ≈ cost on RM 99 |
| --- | --- | --- | --- | --- |
| CHIP | 2.0% credit / 1.0% debit | RM 1 (B2B RM 2) | Saved-card recurring | RM 1.98 |
| Bayarcash | n/a | RM 1 (less on paid tiers) | FPX Direct Debit mandate RM 1.50 | RM 1.50 |
| Stripe | 3% + RM 1 (+1% international) | 3% + RM 1 | Stripe Billing | RM 3.97 + Billing fee |

Rates are from published pages and comparisons in October 2026. Confirm them with each
provider before signing.

- **CHIP** collects monthly card payments and one-off FPX payments (annual plans, credit top-ups).
- **Stripe** is added when international customers arrive.
- **Bayarcash direct debit** is added if companies ask for bank mandates.

Since CHIP has no subscription engine, we build a small one:
- Plans, seats and renewal dates in Postgres.
- A daily renewal job charges saved cards.
- Failed payments are retried on day 1, 3 and 7, then the workspace drops to read-only.
- Receipts and invoices include SST.
- Admins see billing history.

**AI credits:**
- Each AI call records its tokens against the workspace in a credits ledger.
- Plans include monthly credits, and top-ups are bought with FPX or card.
- When credits run out, the AI stops and dashboards stay viewable.

**Plans** (prices set from measured AI cost per project during the beta):

| Plan | For | Includes |
| --- | --- | --- |
| Free | Trying it out | 1 dataset, 1 dashboard, small monthly AI credits, 1 creator |
| Pro (per creator) | Analysts | More datasets and dashboards, more credits, Google Sheets refresh, PDF export |
| Team | Organisations | Viewer seats, admin controls, audit log, SSO (later), priority support |

## Landing page, sign-in and onboarding

Superseded: the current design and process are in [PHASE2_PLAN.md](PHASE2_PLAN.md), under
*C. Login and accounts* (milestone M15). That covers brand directions, three hero mockups to
choose from, the data-platform look, a sign-in redesign and onboarding.

## Milestones from here

Phase 1 (M0 to M10b) is done, with M11, M11b and most of M12 and M13. The order to Gate 1 (approved
22 October 2026) is in [PHASE2_PLAN.md](PHASE2_PLAN.md):
- M12b AI engines and custom instructions (done)
- M21 Autopilot (data straight to dashboard) (done)
- M24 Refresh you can rely on (done)
- M22 Semantic models (done)
- M23 Dashboard templates
- M14 Performance and code health
- M16 Permissions and RLS
- pulled from M20: extend a dataset, Power BI report export, notifications, Bahasa Malaysia, help docs
- **Gate 1: product complete for a private beta, still local**
- then M15 accounts and brand, M17 billing, M18 go online, private beta, M19 compliance, launch, rest of M20

Development stays local until M18.

## Stack (today)

| Part | Choice |
| --- | --- |
| Backend | Python, FastAPI |
| Engine | DuckDB (one database file per project) |
| Ingestion | pandas + openpyxl |
| Frontend | React + Vite + TypeScript |
| Charts | ECharts (SVG renderer) |
| AI client | `openai` SDK pointed at any OpenAI-compatible base URL |
