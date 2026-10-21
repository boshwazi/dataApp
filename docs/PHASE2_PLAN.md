# datawazi Phase 2: from working app to paid, secure SaaS

Phase 1 built the product: Get Data → Transform → Model → Report, with the AI doing each step,
chats in every stage, publishing, refresh, PDF export, lineage, sign-in, workspaces, roles,
invites and Postgres with row-level security.

Phase 2 makes it something Malaysian companies can sign up for, trust with their data and pay
for. It has nine workstreams, delivered as seven milestones (M11 to M17).

Development stays on this computer (local testing, no domain or hosting) until the product is
further along. Going online is M15, just before the private beta.

## Where the gaps are

| Area | Today | Missing for launch |
| --- | --- | --- |
| Hosting | Runs on this laptop | A server in Singapore, a domain with HTTPS, staging, automatic deploys |
| Data location | Supabase in **Seoul**; draft data files on local disk | Supabase in Singapore; every data file in durable storage |
| Email | Personal Gmail sender (test only, some inboxes drop it) | Resend on the datawazi domain |
| Security | Token checks, roles, RLS, isolation tests, upload size cap | Rate limits, security headers, MFA, audit log, secret store, AI-specific defences, external test |
| Login | Email code, Google and Microsoft buttons | Landing page, sign-up flow, onboarding, account settings, delete account, SSO for Team |
| Permissions | Workspace-wide roles (admin, creator, viewer) | Per-dashboard sharing, groups, row-level security inside datasets, share links |
| Payment | None | Plans and limits, AI credits, CHIP checkout and renewals, receipts with tax |
| Legal | None | PDPA 2024 duties, privacy notice, terms, DPA, DPO, breach procedure |
| Power BI | None | Import `.pbix` and `.pbip` models; export as a `.pbip` project (and `.pbix` through Desktop) |

## Workstreams

### A. Foundation and hosting

- **Domain and email.** Buy `datawazi.com`, or similar, through Cloudflare. Send from `login@mail.datawazi.com` through Resend, with SPF, DKIM and DMARC records. Raise Supabase's email limit.
- **Move to Singapore.** Create a new Supabase project in `ap-southeast-1`, re-run the schema and move the data.
  - This is easiest now, while there are no customers.
- **Hosting: Fly.io in Singapore (`sin`).** `flyctl` is already installed on this machine.
  - One container serves the API and the built frontend.
  - A persistent volume holds the DuckDB working files. Machines run in the same region as the database.
- **Durable data files.** Today only published versions are backed up to Storage. Phase 2 also backs up draft data files, so losing the server loses nothing.
  - Restore runs automatically when a file is missing, as published versions already do.
- **Background jobs.** AI runs, refreshes and PDF summaries move to a job queue, a Postgres-backed queue in the same database.
  - A slow AI call then never blocks other users, and work survives a restart. The browser shows progress.
- **Environments.**
  - **Staging and production** are separate Supabase projects and separate Fly apps.
  - **CI:** GitHub Actions runs the 195 tests and the build on every push.
  - **Deploys:** pushes to `main` deploy to staging; production deploys after a check.
- **Secrets.** The AI key, database URL and Supabase secret key go to Fly secrets. The Settings page stops writing the AI key to a file.

### B. Security

The baseline is [OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/) level 2, plus the OWASP Top 10 for LLM applications, since the AI reads customer data. The biggest risk in multi-tenant SaaS is one company seeing another's data ([Techsy launch checklist](https://techsy.io/vi/blog/saas-security-checklist-before-launch-40-checks), [BugRaptors](https://www.bugraptors.com/blog/enterprise-saas-testing)), and two layers already guard against it: the API check and Postgres RLS, both tested. Still to do:

- **Rate limits.** Per user and per workspace on every endpoint, much tighter on AI endpoints, sign-in and invites. They stop abuse and runaway AI bills. Answer with clear "try again in a minute" messages.
- **Security headers.**
  - A Content-Security-Policy allowing only our origin, Supabase and Google Fonts.
  - HSTS.
  - `X-Frame-Options` (or `frame-ancestors`), relaxed later only for deliberate embeds.
  - `Referrer-Policy` and `Permissions-Policy`.
- **Sessions.**
  - Short access tokens; Supabase already rotates refresh tokens.
  - "Sign out everywhere" in account settings.
  - Removing a member revokes their access on the next request. This already works, because roles are checked live.
- **MFA.** Required for admins and optional for everyone, using Supabase Auth's TOTP. A workspace setting can require it for all members.
- **Audit log.** One record per security-relevant event: sign-in, member added or removed, role changed, invite sent or accepted, dataset published, export, settings changed. Admins can see the workspace's log.
- **AI-specific defences** (OWASP LLM Top 10):
  - **Prompt injection.** Text inside uploaded data is wrapped and labelled as data in prompts, never as instructions. Agents may only return catalogue operations, which the engine re-checks; that's the main defence, and it's already in place.
  - **Data sent to the AI.** A workspace setting turns off sample rows, so only profiles and aggregates are sent. Every AI call is logged with its purpose.
  - **Cost abuse.** Per-workspace AI credit limits (see billing) and per-user rate limits.
  - **Output handling.** AI text is rendered as text and safe Markdown only. This is done.
- **Files.** Keep the 200 MB cap but enforce it per plan. Reject zip bombs and huge sheet counts, and parse in a worker with a time limit. Excel macros and formulas already don't run.
- **Dependencies.** Weekly `pip-audit` and `npm audit` in CI, with Dependabot updates.
- **Reviews.**
  - Run `/security-review` on every milestone's changes.
  - Add an OWASP review skill (below).
  - Book one external penetration test before public launch.

### C. Login and accounts

- **Landing page** (with M11's brand work): what datawazi does, a live demo dashboard, pricing, sign-up.
- **Sign-up flow:**
  1. Sign in.
  2. Name the company workspace.
  3. Choose your role in it.
  4. Start from sample data or your own file. The AI builds a first dashboard in under two minutes.
- **Providers.** Turn on Google and Microsoft in Supabase, with app verification so consent screens show "datawazi". Keep the email code.
- **Account settings:** name and avatar, email change (re-verified), MFA, active sessions, sign out everywhere, and delete account.
  - Deleting an account blocks if the person is a workspace's last admin.
- **Workspace settings:**
  - name and logo
  - require MFA or a specific sign-in provider
  - allowed email domains for auto-join
  - AI sample-data switch
  - delete workspace, with a 7-day grace period and an export first
- **SSO (Team plan, later):** SAML through Supabase SSO for companies on Microsoft Entra or Google Workspace.

### D. Permissions

- **Sharing per item.** Today every viewer in a workspace sees every published dashboard. Each dashboard and dataset gets access settings:
  - everyone in the workspace (the default)
  - specific people and groups
  - owner only (a private draft)
- **Groups.** "Finance" or "Sales team", so sharing doesn't mean picking people one by one.
- **Row-level security inside datasets.** Rules such as "viewers in group Northern see `Region = Northern`".
  - The engine adds the filter to every query for that viewer, including Ask and PDF export.
  - Creators can preview a dashboard "as" a group to check it. This was "Later" in the Phase 1 plan; Team customers will expect it.
- **Share links.** A signed, expiring, revocable link to one published dashboard for people outside the workspace. Read-only, with Ask optional. Admins can switch this off.
- **Embedding (later).** Signed embed URLs for intranets, after share links.
- **Ownership.** Datasets and dashboards record an owner. Ownership can be transferred when someone leaves.

### E. Payments and billing

- **Plans** (from the Phase 1 decision):

  | | Free | Pro | Team |
  | --- | --- | --- | --- |
  | Creators | 1 | 1 per seat | Per seat |
  | Viewers | 3 | 10 | Unlimited |
  | Datasets | 2 | 20 | Unlimited |
  | Refresh | Manual | Daily | Hourly |
  | AI credits a month | Small | Medium | Large, pooled |
  | Extras | | Share links, PDF | Row-level security, SSO, audit log export |

  Final numbers come from measuring real AI cost per action, below.
- **AI credits.** Every AI call records its tokens and cost against the workspace in a credits ledger. Today nothing is measured.
  - Plans include monthly credits, and top-ups are bought.
  - At 80% the admin is warned. At 100%, AI actions pause with a clear message while manual work continues.
- **CHIP.** A `PaymentProvider` interface with CHIP first ([pricing](https://chip-in.asia/pricing)):
  - Cards at 2.0% (credit) and 1.0% (debit), with saved-card tokens for monthly renewals.
  - FPX at RM 1 per payment (B2C) or RM 2 (B2B), for annual plans and top-ups.
  - DuitNow QR at 1.6%.
  - No setup or monthly fees.
  - Webhooks are verified by signature and processed once (idempotent). Card details never touch our servers.
- **Billing lifecycle.**
  - Trials.
  - Failed renewals: three retries over 7 days with emails, then read-only, then downgrade to Free.
  - Upgrades and downgrades are prorated.
  - Cancellation takes effect at period end.
- **Receipts and tax.**
  - Every payment gets a numbered receipt or invoice.
  - Service tax is 8% on taxable services, and registration is required above RM 500,000 a year ([Kintsugi SST guide](https://trykintsugi.com/sales-tax-guides/apac/malaysia), to confirm with Customs). Build tax lines in from day one, switched off until registered.
  - MyInvois e-invoicing applies above RM 1 million a year. The threshold was raised in December 2025, and the next phase was postponed to 2027 ([fiscal-requirements](https://www.fiscal-requirements.com/news/4986-malaysia-postpones-mandatory-e-invoicing-to-2027), [Airwallex](https://www.airwallex.com/en-my/blog/e-invoicing-exemption-malaysia-rm1-million)). Keep invoice data in a shape MyInvois accepts, so switching on later is easy.
- **Billing page** (admins): plan, seats, credit usage by month and by person, invoices, payment method, upgrade or cancel.

### F. Legal and compliance (Malaysia PDPA, as amended in 2024)

The 2024 amendments took effect in 2025 ([Baker McKenzie](https://insightplus.bakermckenzie.com/bm/data-technology/malaysia-personal-data-protection-amendment-act-2024-to-come-into-force), [Sidley](https://datamatters.sidley.com/2024/08/07/important-changes-to-malaysias-data-protection-laws/)).

- **Breaches.** Breach notification is mandatory. Write a breach procedure: who decides, who tells the Commissioner and affected customers, and within what time.
- **DPO.** Controllers and processors must appoint a data protection officer, and the Commissioner must be told.
- **Processor duties.** Processors now carry security duties directly. datawazi is a processor for its customers' data, so the DPA states our security measures.
- **Cross-border transfers.** Transfers need the destination to offer equivalent protection. Hosting in Singapore and the AI provider's region need a short transfer assessment.
- **Penalties** are now up to RM 1 million and three years' imprisonment.
- **Data portability.** Workspace export covers all records and data files as a download.
- **Documents:**
  - privacy notice
  - terms of service
  - data processing agreement
  - sub-processor list (Supabase, the AI provider, CHIP, Resend, Fly.io)
  - "what we send to the AI" page
  - cookie notice (essential cookies only, no tracking)
- **Retention.**
  - Deleted workspaces are purged after 30 days.
  - AI call logs are kept for 90 days.
  - Invoices are kept for 7 years, as tax law requires.
- **Review.** Have a Malaysian lawyer review the documents and the transfer assessment before launch. These sources are law-firm summaries, and the regulator's guidelines may have changed.

### G. Product completeness

- **Extend a dataset.** Build on a published dataset (inherit its tables, relationships and measures, read-only and in sync) and add your own sources on top. This is the answer to multi-dataset models.
- **Data safety guard.** Warn when a step makes rows identical and a later step removes them as duplicates. This is what happened to the Uber trips when dates lost their times. Fix the Uber dataset too.
- **Notifications.** In the app and by email: refresh failed, dataset published again, invite accepted, credits running low.
- **Scheduled digests.** Email a dashboard's PDF and AI summary weekly or monthly. This needs server-side PDF rendering.
- **Templates.** Starter datasets and dashboards for common Malaysian SME needs: sales, inventory, HR headcount, finance P&L.
- **Help.** A short docs site, tooltips on first use, and in-app feedback.
- **Bahasa Malaysia.** The interface and the AI's replies, chosen per user.
- **Fixes noted in testing:**
  - Mixed axis formats (RM 180K next to RM 9,000).
  - KPI "vs same period" when the previous year has no data.
  - The flaky test seen once.

### H. Quality and operations

- **End-to-end tests** with Playwright for the key journeys: sign up, load data, AI build, publish, invite a viewer, viewer asks a question, pay. These run in CI against staging.
- **Accessibility audit** with the `web-design-guidelines` skill. Keyboard paths and contrast on every page.
- **Performance.**
  - Split the 590 KB main bundle (the Supabase client and charts load on demand).
  - Measure time to first dashboard.
  - Load-test with 50 concurrent users.
- **Monitoring.**
  - Sentry for errors, with data values stripped.
  - Uptime checks.
  - Structured logs without personal data.
  - An alert when AI or refresh error rates jump.
- **Backups.**
  - Supabase Pro: daily backups for 7 days, with point-in-time recovery as an option ([Supabase production checklist](https://supabase.com/docs/guides/deployment/going-into-prod)). The Free plan has no downloadable backups.
  - Storage copies for data files.
  - A restore drill before launch, and quarterly after.
- **Status page and support:** a support email, a status page, and a simple incident process tied to the breach procedure.

### I. Power BI interoperability (import and export)

Many customers already have Power BI models. Bringing a model in from Power BI, and handing one back
out, lowers the cost of switching both ways and makes datawazi a natural companion to Power BI
rather than a rival to it.

**What each file holds**

| File | Model (tables, columns, relationships, measures, Power Query) | Data | How we read or write it |
| --- | --- | --- | --- |
| `.pbip` project | Yes, as plain-text TMDL files in `<name>.SemanticModel/definition/` ([Microsoft Learn](https://learn.microsoft.com/mt-mt/power-bi/developer/projects/projects-dataset), [endjin](https://endjin.com/blog/why-power-bi-developers-should-care-about-the-tabular-model-definition-language-tmdl)) | No: only the queries that load it | Our own TMDL reader and writer (a documented text format) |
| `.pbix` file | Yes, inside the compressed data model | Yes, compressed (VertiPaq) | Read with [PBIXRay](https://pypi.org/project/pbixray) (MIT licence, maintained; latest release Aug 2026). Writing `.pbix` is not possible: the format is undocumented and binary |
| `.pbit` template | Yes, as JSON | No | Could be written: a zip holding the model schema. Optional |

**Import: "Get Data → From Power BI"**

1. **Upload** a `.pbix`, or a `.pbip` project as a zip.
2. **Read the model.** Tables and column types, relationships (cardinality, active or not, filter direction), measures with their DAX, calculated columns, Power Query (M) per table, the date table, and roles.
3. **Data.**
   - **From a `.pbix`,** PBIXRay reads the tables' data, so the dataset is complete straight away. The tables arrive already clean: Power BI ran the Power Query steps before saving.
   - **From a `.pbip`,** there's no data. The app lists each table's source from its M query, such as a file name, Excel sheet or SQL table, and asks you to upload the matching files or link existing tables. It matches them by name and column set.
4. **Relationships** are added through the engine's normal checks: the one side is unique, types match, and the match rate is shown. Problems are flagged, not hidden.
5. **Measures** are translated from DAX into the measure catalogue. The AI never writes SQL, and that still holds.
   - **Deterministic for common patterns:** `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `COUNTROWS`, `DISTINCTCOUNT`, `DIVIDE`, arithmetic between measures, `SUMX('t', [a] * [b])`, and `CALCULATE` with simple column filters.
   - **The rest go to the Model AI,** which proposes catalogue measures. The engine checks them, and the user approves each one in Review mode.
   - **Anything that can't be expressed** (time intelligence, iterators over virtual tables, and similar) is kept as "Power BI measure, not converted", with its DAX shown, so nothing is silently lost.
6. **Roles** (Power BI row-level security) become row-level security rules in the dataset (M13).
7. **An import report** says what came across, what was converted by rule, what by the AI, and what was left behind.
   - Lineage shows "imported from Sales.pbix".
   - Every converted measure's total is computed, so the user can compare it with Power BI.

**Export: "Download for Power BI"**

- **PBIP project (main path).** A zip holding:
  - `<name>.pbip`
  - `<name>.SemanticModel/` with TMDL: tables with columns and types, relationships, measures written as DAX, format strings, the date table marked as a date table, and descriptions
  - `<name>.Report/` (see below)
  - a `data/` folder with each clean table as Parquet (or CSV)
- **Data loading.** Each table's Power Query reads its file from `data/` through one `DataFolder` parameter. Open the `.pbip` in Power BI Desktop, set the folder once, and click Refresh.
- **Measures to DAX** is fully deterministic, because the measure catalogue is small:
  - aggregate measures become `SUM('t'[c])`, or `SUMX` for expressions
  - filters become `CALCULATE(..., KEEPFILTERS(...))`
  - division becomes `DIVIDE`
- **`.pbix`:** open the exported `.pbip` in Power BI Desktop and choose *File → Save as → .pbix*. That's the supported route; there is no way to write `.pbix` directly.
  - An optional `.pbit` template gives a single file instead. It opens in Desktop and loads from the same data files.
- **Dashboards.** Report pages as Power BI report pages (PBIR): KPI cards become cards, bar, column, line, area, pie and donut charts keep their types, tables become tables, and titles and sort orders carry over. This comes after the model export works.

**Checks and safety**

- **Round trip:** exporting a dataset and importing it again gives the same tables, relationships and measure results. This is an automated test.
- **Real-Desktop check:** the exported project is opened with this machine's Power BI tooling (the Power BI Desktop bridge and modelling MCP from the Power BI skills) before each release.
- **Untrusted files.**
  - Parsing runs in a separate worker with time and memory limits, and the plan's upload size limit applies.
  - Nothing in the file is executed: no M, no DAX, no macros.
  - PBIX passwords or embedded credentials are never read or stored.
- **Limits by plan:** import and export are in Pro and Team. Free can import one `.pbix` to try it.

## Milestones

| # | Delivers | Needs from you |
| --- | --- | --- |
| **M11** Power BI interoperability | Import `.pbix` (model and data) and `.pbip` (model, then match data); DAX to measures with an import report; export a `.pbip` project with TMDL, DAX and data files; round-trip tests | A few real `.pbix`/`.pbip` files to test with |
| **M12** Accounts and brand | Logo and brand, landing page, sign-up and onboarding, account and workspace settings, MFA, Google and Microsoft sign-in, delete and export | Logo direction; Google and Microsoft app registrations |
| **M13** Permissions | Per-item sharing, groups, row-level security in datasets (and imported Power BI roles), share links, owners, audit log | Decide default sharing |
| **M14** Billing | Plans and limits, AI credit metering, CHIP checkout and renewals (CHIP sandbox while local), receipts, billing page | CHIP sandbox account; final prices later |
| **M15** Go online | Domain, Resend email, Singapore Supabase, Fly.io hosting with HTTPS, staging, CI, secrets, durable data files, job queue, rate limits, security headers | Buy the domain; Fly.io account; Supabase Pro on production |
| **Private beta** | 5 to 10 friendly companies on Free, invited by you, with feedback built in | Pick the beta companies |
| **M16** Compliance and hardening | Legal documents, DPO, breach procedure, transfer assessment, monitoring, backup drill, load test, security review, external penetration test | Lawyer review; DPO named; pen-test budget |
| **Public launch** | Billing switched to live CHIP | CHIP live account (registered business, SSM) |
| **M17** Product depth | Extend a dataset, Power BI report export (dashboards as report pages), notifications, scheduled digests, templates, Bahasa Malaysia, help docs | Template priorities |

Why this order:
- **Local first.** M11 to M14 build and test on this computer with the current Seoul test project and Gmail sender. Nothing there needs a domain.
- **Some security work can start early.** Parts of M15 (CI, rate limits, security headers, the job queue) don't need hosting and can be pulled forward whenever convenient.
- **Going online comes before the beta,** because beta companies need a real address, reliable email and data in Singapore.
- **Billing is built before the beta and switched on at launch,** after the beta has shown which limits matter.

## Running costs to expect (at launch scale)

| Item | Cost |
| --- | --- |
| Supabase Pro (production) | US$25/month, plus usage; staging can stay Free |
| Fly.io, Singapore, 1 to 2 small machines plus volume | about US$10–40/month |
| Domain | about US$10/year |
| Resend | Free up to 3,000 emails a month, then US$20/month |
| Sentry | Free tier at first |
| AI provider | Per use; covered by AI credits in each plan |
| CHIP | Per transaction only (rates above) |
| Penetration test, lawyer | One-off; get quotes |

## Skills that would help

The skills already installed cover much of Phase 2:
- `design-taste-frontend` and `frontend-design` for the landing page
- `ui-ux-pro-max` and `web-design-guidelines` for UI and accessibility audits
- `playwright-cli` for end-to-end tests
- the built-in `/security-review`

Worth adding, after reading its SKILL.md and updating SKILLS-INDEX.md as usual:
- **OWASP security skill** ([agamm/claude-code-owasp](https://gittrend.io/repo/agamm/claude-code-owasp)): reviews against OWASP Top 10:2025, ASVS 5.0 and the LLM Top 10, which suits an AI app that reads customer data.

No suitable skills for payments or Supabase came up in your claude.ai skill catalogue. The community "Supabase production checklist" skills listed online repeat Supabase's own checklist, which this plan already includes.

## Decisions needed

1. **Domain name (at M15):** `datawazi.com`, `.my`, `.ai` or `.io`? `.com.my` needs a registered Malaysian business.
2. **Hosting:** Fly.io in Singapore (recommended), or AWS or Azure for enterprise buyers who ask.
3. **Default sharing:** new dashboards visible to the whole workspace, or private until shared?
4. **Beta:** which 5 to 10 companies, and when.
5. **Business registration** for CHIP and for issuing invoices: an existing company, or a new Sdn Bhd?
6. **Prices:** finalised after measuring AI cost per workspace in the beta.
7. **Power BI scope for M11:** model import and export first (recommended), with dashboards as Power BI report pages in M17. Or both in M11.
