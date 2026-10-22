# datawazi Phase 2: from working app to paid, secure SaaS

Phase 1 built the product: Get Data → Transform → Model → Report, with the AI doing each step,
chats in every stage, publishing, refresh, PDF export, lineage, sign-in, workspaces, roles,
invites and Postgres with row-level security.

Phase 2 makes it something Malaysian companies can sign up for, trust with their data and pay
for. It has twelve workstreams, delivered as ten milestones (M11 to M20).

Development stays on this computer (local testing, no domain or hosting) until the product is
further along. Going online is M18, just before the private beta.

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

#### Free and low-cost options (for testing and the beta)

Free offers change often. These were checked in October 2026 and should be confirmed on each
provider's own page before relying on them.

**Show the app to testers without hosting it: Cloudflare Tunnel (free)**
- `cloudflared` makes an outbound connection from this computer and gives a public HTTPS address. No server, and no open ports ([guide](https://flaviocopes.com/cloudflare-quick-tunnels/)).
- A **quick tunnel** needs no account. It gives a random `*.trycloudflare.com` address that lasts while it runs, which is fine for a demo call.
- A **named tunnel** gives a stable address on your own domain, and needs that domain's DNS on Cloudflare (still free) ([guide](https://flaviocopes.com/cloudflare-tunnel/)).
- Limits: the app is only reachable while this computer is on. Sign-in redirect URLs must include the tunnel address.
- This is the recommended way to start the private beta at no cost.

**Free domains and subdomains**

| Option | What you get | Good for | Catch |
| --- | --- | --- | --- |
| Platform subdomains (`*.trycloudflare.com`, `*.onrender.com`, `*.fly.dev`, `*.pages.dev`) | An address on the host's domain | Testing | Looks temporary; you can't set up email sending (no DNS control) |
| [is-a.dev](https://unicorn.is-a.dev/posts/free-domains/) | `datawazi.is-a.dev`, set up through a GitHub pull request | A developer preview | Meant for personal and developer projects, not a commercial product; approval takes time |
| [Open Domains](https://www.alternativeto.net/software/freenom/) (`is-cool.dev` and similar) | Subdomains with A, CNAME, MX and TXT records | Testing email setup | Not a brand customers will trust |
| eu.org | A free `*.eu.org` name with full DNS | Long-term free name | Slow manual approval; not a business brand |
| A paid `.com` through Cloudflare | `datawazi.com` at cost, about US$10/year | **Launch** | Costs a little; needed for Resend email, customer trust and Google/Microsoft sign-in verification |

Free domains work for testing. For a product companies pay for, the US$10/year `.com` is the
one paid item worth having. Freenom, the old source of free `.tk` and `.ml` domains, no longer
offers them.

**Free or near-free hosting**

| Option | Free part | Fits datawazi? |
| --- | --- | --- |
| This computer + Cloudflare Tunnel | Everything | **Yes, for testing and an early beta.** Only up while the PC is on |
| [Oracle Cloud Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm) | ARM virtual machines that stay up, with a disk | **Yes:** a real server with persistent disk for the DuckDB files. Reports say new accounts got 2 CPUs and 12 GB from mid-2026, down from 4 and 24 ([report](https://braindetox.kr/en/posts/oracle_always_free_tier_reduced_2026.html)). Idle machines can be reclaimed, and Singapore capacity isn't confirmed |
| Render free web service | Sleeps after 15 minutes idle; free Postgres deleted after 90 days ([Render](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026.md)) | Not really: slow cold starts, and no persistent disk on free |
| Koyeb free | One very small instance | Too small for DuckDB with real files |
| Railway | A US$5 trial credit, then pay | Trial only |
| Fly.io | No free tier for new accounts; small machines from about US$2–5/month ([comparison](https://agentdeals.dev/hosting-free-tier-comparison-2026)) | **The cheapest paid choice in Singapore**, as planned for launch |
| Supabase Free | 2 projects, small database; pauses after a week idle; no downloadable backups | Fine for testing and staging; production needs Pro (US$25/month) |

**Free tunnels compared** (checked October 2026; confirm on each provider's page)

A tunnel gives this computer's datawazi a public HTTPS address with no server, no domain and no
open ports. The app is reachable only while the PC is on and the tunnel runs.

| Tunnel | Free address | Free limits | Notes |
| --- | --- | --- | --- |
| [ngrok](https://ngrok.com/docs/pricing-limits/free-plan-limits) | One **stable** dev domain, `<name>.ngrok-free.app` | 1 GB out a month, 20,000 requests, 3 endpoints; no session time limit | Easiest stable address. Visitors' browsers see an ngrok warning page once, then a cookie hides it for days ([write-up](https://merginit.com/blog/19062026-free-developer-tunnels-comparison)) |
| [Cloudflare quick tunnel](https://flaviocopes.com/cloudflare-quick-tunnels/) | Random `*.trycloudflare.com`, new each run | No published cap | No account, no warning page; but the address changes, so sign-in settings need updating each time |
| Cloudflare named tunnel | Stable, on **your own** domain | Free plan | Best once a domain is bought (M18); no warning page |
| [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel.md) | Stable `<machine>.<tailnet>.ts.net` | Unpublished bandwidth limits; ports 443, 8443, 10000 only; still in beta | Fine for a few testers; not a brand |

**How to share datawazi with ngrok** (about 10 minutes, nothing to buy)

1. Sign up at ngrok.com (free), install the agent, and add your auth token (`ngrok config add-authtoken …`).
2. Run datawazi on port 8000 as usual, then run `ngrok http 8000`. The dashboard shows your `https://<name>.ngrok-free.app` address, which stays the same each time.
3. Set `PUBLIC_URL=https://<name>.ngrok-free.app` in `.env` and restart datawazi, so invite emails link to the public address.
4. In Supabase, go to **Authentication → URL Configuration**: add `https://<name>.ngrok-free.app/**` to Redirect URLs, and set it as the Site URL while testing.
5. Send testers the address.
   - Sign-in, invites and dashboards work as they do locally.
   - 1 GB a month covers demos and a small beta. Big uploads count against it.

**Limits to keep in mind**
- The app is down when this PC sleeps or the tunnel stops.
- The warning page and the `ngrok-free.app` name look temporary to customers.
- Free tunnels are for testing and beta, not for paying customers.
- None of this changes security: sign-in, roles and row-level security apply as usual.

**Suggested path**

1. **Now:** local only, as today.
2. **Private beta:**
   - **ngrok's free stable address** from this computer: the simplest setup, recommended.
   - Or a Cloudflare quick tunnel for one-off demos.
   - Or Oracle Cloud Always Free in Singapore if beta users need it always on.
   - A Supabase Free project in Singapore.
   - Cost: RM 0.
3. **Public launch:**
   - Buy `datawazi.com` (about US$10/year).
   - Fly.io Singapore (about US$5–40/month).
   - Supabase Pro (US$25/month).
   - Resend Free.

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
- **Found while building Phase 2, to fix:**
  - **Parse in a sandbox.** Power BI files are parsed in the API process. Move parsing to a sandboxed worker with time and memory limits, like the other uploads.
  - **Encrypt the AI key.** It sits in a local settings file. Move it to the secret store with the other keys.
  - **Re-check Google Sheets links after redirects.** Fetching is limited to `docs.google.com`; make sure redirects can't lead elsewhere (server-side request forgery).
  - **Review the admin powers.** The service account used for background jobs bypasses row-level security. Keep its use to named jobs, log each use, and never use it for a user's request.
  - **Sign-in brute force.** Supabase limits sign-in attempts. Add the app's own limits on invites and password-less code checks.
- **Secure coding review:** an OWASP review skill against Top 10:2025, ASVS 5.0 and the LLM Top 10 on every milestone, plus the external penetration test before launch.
- **Reviews.**
  - Run `/security-review` on every milestone's changes.
  - Add an OWASP review skill (below).
  - Book one external penetration test before public launch.

### C. Login and accounts

- **Brand, landing page (hero) and sign-in, designed before they're built (M15):**
  1. **Brand first.** A real datawazi logo and wordmark to replace today's placeholder. Make 3 directions as a small HTML page (as with the brand directions in Phase 1), choose one, then turn it into icons, favicon, email header and social card.
  2. **Hero directions.** 3 different hero designs as working HTML mockups, made with the `design-taste-frontend` and `frontend-design` skills, so it doesn't look like a template. The hero opens with the product itself: a short live sequence of a messy spreadsheet becoming a checked dashboard (the motion skills can make it), not a stock gradient and a big number. Choose one, then build.
  3. **Landing page:**
     - a headline that says what it does
     - the product sequence
     - how it works in 4 steps
     - why to trust it: checks, approve and undo, what the AI sees, data kept in Singapore, PDPA
     - Power BI import and export
     - pricing, FAQ, and a footer with the legal pages
     - English, with Bahasa Malaysia later
  4. **Look:** the app's data-platform look, so the site and product feel like one thing: Inter and JetBrains Mono, the console blue, yellow only for AI. Fast and accessible: no heavy scripts, good contrast, and it works on phones.
  5. **Sign-in page redesign:**
     - **layout:** a two-part screen, with the sign-in form on one side and a quiet product preview, or what your company gets, on the other
     - **sign-in options:** Google, Microsoft and the email code
     - **care for the details:** clear errors (expired link, wrong browser), "trouble signing in?" help, and a company-domain hint for invites
     - **matching emails:** sign-in emails in the same look, from the datawazi domain (with Resend, M18)
  6. **Sign-up and onboarding:**
     1. Sign in.
     2. Name the workspace.
     3. Invite teammates (or skip).
     4. Choose sample data, your own file or a Power BI file.
     5. The AI builds a first dashboard while you watch.
     6. A short checklist shows what to try next.
  7. **Checks:** accessibility and design audit (`web-design-guidelines`, `ui-ux-pro-max`); screenshots at phone, tablet and desktop sizes; time to first dashboard for a new user.
  8. **Where it lives:** built and tested locally in M15. At `/` for visitors who aren't signed in, with the app behind it. Public once the app is online (M18), first on the ngrok address, then on the domain.
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
- **Row-level security (RLS) inside datasets.** There are two kinds, and both matter:
  - **Between companies (done in M10b).** Postgres row-level security and the API keep each workspace's records apart, tested on every endpoint and directly in the database.
  - **Within a company's data (this milestone).** Who sees which rows of a dataset.
    - **Roles:** a dataset defines roles such as "Northern region" with a rule (`sales[Region] = "Northern"`) or a dynamic rule tied to the person (`Managers[Email] = the signed-in user's email`, like Power BI's `USERPRINCIPALNAME()`).
    - **Assignment:** roles are given to groups or people in the dataset's settings. Someone with no role sees nothing, unless a rule allows all.
    - **Enforcement:** the rule filters the table it names and flows through relationships to related tables. That's the same model as Power BI, and it uses the engine's existing relationship paths.
    - **Everywhere:** the filter is added by the engine to every query for that viewer, including visuals, KPI comparisons, Ask, PDF export, share links and exports. The AI never sees rows a viewer couldn't.
    - **Testing:** creators can **view as** a role or a person to check it. Each role shows how many rows it allows per table.
    - **Power BI roles** from imported files become these roles.
    - **Tests:** every query path is tested with a restricted viewer, including totals that must not leak (a KPI's "vs last year" must use only allowed rows).
  - This was "Later" in the Phase 1 plan. Team customers will expect it.
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
6. **Roles** (Power BI row-level security) become row-level security rules in the dataset (M16).
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

**After import: sources and refresh**

A `.pbix` holds data for **Import-mode** tables only: the result of Power BI's last refresh, whatever
connector produced it. datawazi reads that copy, so it needs no connector or credentials, and none
are stored in the file. Planned follow-ups:

1. **Honest labels.**
   - Show each imported table's original connector, read from its Power Query text (SQL Server, SharePoint, Salesforce, Excel…), and "snapshot as of <last refresh>", so nobody mistakes it for live data.
   - **DirectQuery tables** carry no data. Flag them clearly as needing data.
   - **Live-connection `.pbix` files** (a report on a model in the Power BI service) have no model inside. Say so up front, instead of importing an empty model.
2. **Refresh from a newer `.pbix`.**
   - Refresh in Power BI Desktop, then upload the file to the same dataset. Its tables are replaced by name through the existing refresh checks (row changes, totals) and history.
   - Measures and relationships are re-checked, not re-imported, so edits made in datawazi stay.
3. **datawazi's own connectors** (later): SQL Server, PostgreSQL, MySQL and SharePoint/OneDrive Excel.
   - Credentials encrypted per workspace.
   - Scheduled refresh as with Google Sheets.
   - For databases inside a company network, a small **gateway agent** the company runs, like Power BI's on-premises data gateway, because a hosted service can't reach inside.
4. **Power BI service connector** (later): sign in with Microsoft and read a published semantic model's tables through the Power BI REST and XMLA APIs.
   - Covers live-connection reports.
   - Refresh stays in Power BI, and datawazi pulls the latest data.

**Checks and safety**

- **Round trip:** exporting a dataset and importing it again gives the same tables, relationships and measure results. This is an automated test.
- **Real-Desktop check:** the exported project is opened with this machine's Power BI tooling (the Power BI Desktop bridge and modelling MCP from the Power BI skills) before each release.
- **Untrusted files.**
  - Parsing runs in a separate worker with time and memory limits, and the plan's upload size limit applies.
  - Nothing in the file is executed: no M, no DAX, no macros.
  - PBIX passwords or embedded credentials are never read or stored.
- **Limits by plan:** import and export are in Pro and Team. Free can import one `.pbix` to try it.

### J. AI engines: gaps and improvements

Every engine follows the same rule: the AI proposes structured operations and the engine checks
and runs them. That rule stays. The gaps below come from testing with the real AI and reading the
code, for example the Uber duplicates, the refused pie chart and "which row was removed?".

| Engine | What it does today | Gaps | Improvements |
| --- | --- | --- | --- |
| **Get Data** (`ai_profile`) | Describes each table and column from profiles and 5 sample rows | No semantic types (currency, email, phone, IC number, postcode). No personal-data detection. No hints between tables | Detect semantic types and **personal data** (names, IC numbers, phones, emails), flag it for PDPA, offer masking. Suggest keys across tables at load time. Detect Bahasa Malaysia headers and values |
| **Transform** (`ai_transform`) | Proposes cleaning steps from the profile, distinct values and samples | Plans in one pass without seeing effects. Step order can lose data (the Uber case). Doesn't know what the model needs | **Plan, check, repair loop:** dry-run every step, show row and value changes, and send any failed check back to the AI once. Built-in ordering rules (remove duplicates before casting, trim before matching). Learn from rejected steps per workspace. Data-quality rules that run on every refresh, such as "order_id unique" and "amount > 0" |
| **Model** (`ai_model`) | Relationships, date table and measures in one pass, from checked key candidates | Measures limited to sum, average, count, distinct count, min, max and arithmetic, with filters only on the measure's own table. No calculated columns, no IF/SWITCH, no way to use an inactive relationship. No time intelligence beyond KPI cards. No hierarchies, display folders or synonyms. No review of model quality | **Time-intelligence measure kinds:** YTD, previous year, year-on-year %, rolling 3 or 12 months, month-to-date, each checked by the engine. **Hierarchies** (Year → Quarter → Month, Category → Product). **Synonyms** for Ask ("revenue" = "sales"). A **model best-practice check**, like Tabular Editor's analyser: unused columns, missing formats, ambiguous paths, many-to-many risks |
| **Report** (`ai_report`) | Designs pages from house-style rules; writes checked insights | **No filters or slicers on dashboards** (the biggest gap for business users). No drill-down. Insights are one sentence per visual. No consistent colour per category across visuals | **Page filters and slicers** (date range, segment, region) that every visual and Ask respect. **Drill-down** on hierarchies. **Anomaly notes:** the engine finds outliers and changes with statistics, and the AI only explains what it found. The same category keeps the same colour everywhere. Alt text for every chart |
| **Stage chats** (`chat.py`) | One request, one reply with checked actions; Full AI or Review | Can't look things up mid-answer (only summaries in context, which is why it couldn't show the duplicate). Wide tables crowd the prompt. Replies arrive all at once | **Read tools in a short tool loop:** preview rows, run a measure, show removed rows, explain a step. Called by the AI, run by the engine, read-only. **Streaming replies.** Context trimmed to the columns that matter. Clarifying questions when a request is ambiguous |
| **Report chat** (`chat.py`, Report stage) | Changes pages and visuals from the dashboard's layout | **Can't answer questions about the data.** It sees which visuals exist but not their numbers, so "explain the trend in the Monthly trips chart" gets a refusal. It can't look anything up in the model either | **Answer from the data, with the same checks as Ask:** (1) it receives each visual's computed data (the values behind the chart, trimmed for size), so questions about what's on the page are answered from those numbers; (2) **when the answer isn't on the dashboard, it queries the data model** through Ask's planner (measures, columns, filters), with "how this was calculated"; (3) every number is checked against the data before it's shown, as Ask and insights already are; (4) it can **turn an answer into a visual** ("add that as a chart"), as a reviewable change. Also: an **Explain** button on each visual (trend, peaks, dips, how two measures move together), questions about a selected visual, and replies that name the visual they're about |
| **Ask** (`ask.py`) | Plans up to 3 queries over measures, answers with "how this was calculated" and checked numbers | No relative dates ("last quarter", "this year so far"). Can't use dashboard filters. Bar and line answers only. No "why" analysis | **Relative dates** resolved by the engine against the data's latest date. Respect page filters and row-level security (M16). **"Why did X change?"**: the engine splits the change by each dimension, and the AI narrates the biggest contributors. **Pin an answer** to the dashboard as a visual. Saved questions |
| **Export summary** (`export.py`) | 3 to 5 checked bullets for the PDF | One tone. Whole dashboard only | Per-page summaries; audience choice (executive or analyst); comparison with the last export ("since last month…") |
| **Refresh** (`refresh.py`) | Rule checks after each refresh (rows, blanks, totals) | No narrative. No alerts on unusual values | A plain-words summary of what changed. Alerts when a measure moves beyond its usual range |

**Across all engines**

- **Evaluation suite.** A fixed set of sample datasets, such as sales, the Uber trips and an HR file, with expected outcomes:
  - steps that must or must not appear
  - relationships that must be found
  - Ask questions with known answers

  Run it on every prompt change and every model or provider change, with a score per engine. Without it, AI quality can only be judged by hand.
- **Provider choice and fallback.** Only ilmu GLM 5.1 is tested so far. Add a second provider, with per-engine choice (a cheaper model for profiling, a stronger one for modelling) and automatic fallback when one fails or times out.
- **Cost and speed metering.** Tokens, time and cost per call per engine. This feeds AI credits (billing) and shows which engine to optimise.
- **Personal data and prompt safety.**
  - Mask personal-data columns before samples go to the AI.
  - A workspace switch to send no sample rows at all.
  - Treat cell values as data in every prompt, which defends against prompt injection hidden in uploaded files.
- **Feedback.** Thumbs up or down and approve or reject rates per engine, so weak spots show up in numbers.
- **Bahasa Malaysia.** Every engine replies in the user's chosen language, and numbers keep their checked values.
- **One answering engine.** Ask (published dashboards), the Report chat (drafts) and the stage chats all use the same "plan a query → run it in the engine → check every number" pipeline. A question about data gets a real, checked answer wherever it's asked, never "I can't see the numbers".

### K. Product design: from guided tool to professional workspace

**Why it still feels like a toy.** The look is cleaner now, but the app behaves like a wizard: the
AI does each step and the user mostly approves. Professional tools (Power BI, Databricks, Hex,
Tableau) let people **work directly**, with the AI as a fast assistant rather than the only way in.
The signs that read as "toy" today:

- **No direct manipulation.** You can't drag a field onto a chart, drag a visual to move or resize it, drag between columns to make a relationship, or right-click a column for its actions.
- **No properties panes.** A chart's fields, sort, colours and labels can only be changed by asking the AI.
- **Thin data views.** Tables don't sort, filter, resize columns or scroll smoothly through large data.
- **Chatty tone and many "AI" badges**, where pro tools are terse and precise.
- **No formula bar** with autocomplete and inline errors for measures.
- **Dashboards with no filters or slicers** (in M12), no cross-highlighting, no drill-down.
- **No object explorer.** There's no tree of datasets → tables → columns → measures to browse and search, like Databricks' catalog or Power BI's Data pane.
- **No dark mode and no density setting.**

**What to build**

1. **Design system first.** A small component library with one look, written once and used everywhere:
   - data grid (virtualised, sortable, filterable, resizable columns, copy cells)
   - resizable split panes, tabs, tree, popover menus and right-click menus
   - tooltips, formula input, toolbar
   - light and dark themes; comfortable and compact density
   - keyboard shortcuts with a cheat sheet (`?`)

   It replaces the current stylesheet. A component gallery page keeps it consistent.
2. **Three-pane editors, like Power BI Desktop:**
   - **Transform (like Power Query):**
     - the data grid in the middle
     - column-header menus for every step (change type, rename, remove, replace, fill)
     - Applied Steps on the right
     - a step's effect highlighted in the grid (changed cells, removed rows)
   - **Model:**
     - a diagram you can drag tables around in
     - drag a column onto another table to make a relationship, with the engine's checks shown as you drop
     - a properties pane for tables, columns and measures
     - a **formula bar** for measures with autocomplete of tables, columns and measures, live checking and a preview value
   - **Report:**
     - a **canvas** with a snap grid: drag to place, resize, align and distribute visuals
     - a **Fields pane**: drag fields onto a visual's slots (axis, values, legend)
     - a **Format pane** for titles, labels, colours and sort
     - page and visual filters, slicers, cross-highlighting, drill-down
     - undo and redo for every edit
3. **The AI as an assistant, not the only path.** Every AI action becomes an ordinary edit you can see and change in those panes. AI suggestions appear inline, in the grid, the diagram or on the canvas, as reviewable changes. The "AI" badge stays only for changes still waiting for review.
4. **Workspace navigation:**
   - an **object explorer** tree on the left of each editor
   - global search across tables, columns, measures and visuals
   - recent items and favourites on Home
   - tabs for open datasets and dashboards
5. **Tone and polish:**
   - Shorter, precise copy.
   - Numbers right-aligned in tabular figures everywhere.
   - Consistent empty, loading and error states.
   - Optimistic updates with undo toasts.
   - Motion only where it explains a change.
6. **How it's checked.**
   - An audit with the installed `ui-ux-pro-max`, `design-taste-frontend` and `web-design-guidelines` skills.
   - Screenshots at three widths.
   - Testing with 3 to 5 real analysts on three tasks each: clean a file, build a model, make a dashboard. Fix what they stumble on.
   - Benchmark side by side against Power BI Desktop for the same tasks.

### L. Performance and code health

Measured in October 2026:
- The stylesheet is about 4,000 lines of layered overrides (30 `!important`).
- Every Transform change re-runs and re-profiles every table.
- The DuckDB file is reopened on each request.
- Undo history keeps up to 30 full copies inside each dataset record.
- Dashboards recompute every visual each time they open.
- The main JavaScript bundle is about 590 KB.

**Backend speed**
- **Incremental Transform.** Re-run only the changed table and the tables that depend on it. Profile lazily: a cheap row count straight away, the full profile in the background.
- **Cached results.** Visual data, KPI comparisons and Ask queries are cached per dataset version and filter set. Published versions never change, so their cache never goes stale. Dashboards then open instantly.
- **Connection reuse.** A small pool of DuckDB connections per dataset instead of reopening the file on each request. Read-only connections for published versions.
- **Lighter records.** Undo history and the activity log move out of the dataset record into their own Postgres tables. Saves write only what changed.
- **Background work.** AI runs, refreshes, Power BI parsing and exports go to the job queue, with progress in the browser and nothing blocking a request.
- **Scale tests.** Load 1 million and 10 million row tables. Measure time to import, clean, model and open a dashboard, and set limits per plan from the results.

**Frontend speed**
- Split the bundle so each page loads only what it uses: the Supabase client, charts and editors load on demand.
- A request cache (stale-while-revalidate) so moving between pages doesn't refetch everything.
- Virtualised grids and lists.
- Skip re-rendering charts whose data didn't change.

**Code health**
- **One API contract:** generate the TypeScript client and types from the backend's OpenAPI schema, so frontend and backend can't drift.
- **Checks in CI:**
  - Ruff and Pyright (type checking) for Python.
  - ESLint and the TypeScript compiler for the frontend.
  - Playwright end-to-end tests for the key journeys.
  - Dependency audits.
- **Split large modules** by concern: `chat.py` (650 lines), `model.py`, `transform.py`, and `Dashboard.tsx` (700 lines). Same behaviour, smaller files, tests kept green.
- **Rewrite the stylesheet** on the design system's tokens and components (K). Delete the override layers.
- **One error model:** structured error codes from the API, the same error component everywhere, and messages that say what to do.
- **Hunt the flaky test** seen in Phase 1. Make every test independent of timing.


## Order to Gate 1 (approved 22 October 2026)

Product first: everything that makes the app work comes before the domain, payment and going online.

1. **M12b** AI engines and custom instructions
2. **M21** Autopilot: from data straight to dashboard, with the job queue
3. **M24** Refresh you can rely on
4. **M22** Semantic models: build from existing tables, and AI merge of models
5. **M23** Dashboard templates
6. **M14** Performance and code health
7. **M16** Permissions and RLS
8. From M20, pulled forward: extend a dataset, Power BI report export, notifications and scheduled digests, Bahasa Malaysia, help docs; and the M13 leftovers

**Gate 1: product complete for a private beta, still local.** Everything above works and has been tried by
3 to 5 analysts and 2 to 3 business owners with no technical background (who try Autopilot).

After Gate 1: M15 accounts and brand, M17 billing, M18 go online, the private beta, M19 compliance and
hardening, public launch, then the rest of M20 (database connectors with a gateway agent, Power BI service
connector).

**Autopilot defaults (approved):** the AI's changes are checked by the engine and accepted at once, each one
marked "Added by Autopilot" and open to review, undo and the stage's chat; the dataset is published; the
dashboard stays a draft until you publish it, so nothing reaches viewers unseen.

## Milestones

| # | Delivers | Needs from you |
| --- | --- | --- |
| **M11** Power BI interoperability ✓ done | Import `.pbix` (model and data) and `.pbip` (model, then match data); DAX to measures with an import report; export a `.pbip` project with TMDL, DAX and data files; round-trip tests | A few real `.pbix`/`.pbip` files to test with |
| **M11b** Power BI follow-ups ✓ done | Connector and "snapshot as of" labels per table; DirectQuery and live-connection files flagged; refresh a dataset by uploading a newer `.pbix` | Nothing |
| **M12** AI engines and model depth | **Report chat answers from the data: done early** (each visual's numbers in its context, the data model queried through Ask when the answer isn't on the dashboard, every number checked); still to do: an Explain button per visual and "add that as a chart". **Time intelligence: done** (year, quarter and month to date, same period last year, last year to date, rolling months; one range join per measure; in the formula bar as YTD([Revenue]) and so on; the Model AI and chat propose them; Power BI's TOTALYTD, SAMEPERIODLASTYEAR, DATEADD, DATESYTD and DATESINPERIOD import and export). **Model depth: done** (calculated columns as a Transform "Add column" step; measure filters on related tables with an editor; IF, SWITCH, AND, OR, NOT and comparisons in measures and columns; measures that use an inactive relationship; all through Power BI DAX import and export). Later: model-level calculated columns, many-to-many, two-way filtering. **AI:** evaluation suite; metering per call; dashboard filters and slicers; time-intelligence measures; hierarchies and synonyms; chat read tools in a tool loop with streaming; Transform plan-check-repair loop and ordering rules; Ask relative dates and "why" analysis; personal-data detection and masking; Power Query (M) from a `.pbip` replayed as Transform steps by the AI, with review | Nothing (uses the current AI key); a second AI provider key later, for fallback |
| **M12b** AI engines and custom instructions ✓ done (streaming moved to M14) | Custom instructions for the AI, for the workspace (in Settings) and per dataset or dashboard, added to every AI prompt; an evaluation suite of fixed questions with expected numbers; an Explain button per visual and "add that as a chart"; Ask resolves relative dates (this year so far, last quarter) and answers "why did X change?" by splitting the change by each dimension; chat read tools in a tool loop with streaming | Nothing |
| **M21** Autopilot ✓ done | "Do it for me" on Get Data, and a quick start on Home (drop a spreadsheet, get a draft dashboard): describe, clean, model, publish and design a dashboard in one run, as a background job; a progress screen with numbered steps (a spinning ring on the current one, a tick when done) ending in "View dashboard"; every change marked "Added by Autopilot" in the normal editors, open to review, undo and the stage's chat; the job queue it needs (pulled from M14). Each step calls the same checked endpoints as the step-by-step flow; what the engine refuses is listed on the progress screen; a failed step says where to fix it; the dashboard stays a draft | Nothing |
| **M22** Semantic models ✓ done | A Semantic models section: build a model from tables of several published datasets without loading them again; AI merge of whole models (their tables, relationships and measures, clashing names renamed, shared dimensions such as Customer or Date found and conformed, relationships across proposed and checked). Built as a dataset whose tables are all linked, so each follows its dataset when it publishes (M24); the source models' relationships, date table and measures come along through the usual checks; clashing measures are renamed and formulas follow; then the Model AI proposes links across, for review | Nothing |
| **M23** Dashboard templates ✓ done | Page blueprints (Executive overview, Sales performance, Operations, Finance, Customers) with slots such as "main money measure by month"; start from a template (the AI fills the slots with your measures), from a description, or let the AI choose from what the data looks like. Built in New dashboard: pick a template, let the AI choose, or start blank, with an optional description in your own words; slots nothing fits are skipped, never invented, and every visual is checked and left for review | Template priorities (the five above are the defaults until you say otherwise) |
| **M24** Refresh you can rely on ✓ done | Files refreshed from shared OneDrive, SharePoint and Google Drive links on a schedule (private files after M15's sign-in apps); schedules at a time of day in Malaysia time, run by the job queue, with retries and alerts when a refresh fails or warns; append-only loads for large growing files; AI insights rewritten and re-checked after each refresh; datasets that link a table refresh when its source publishes; roll back to an earlier published version (the last 5 are kept). Built: alerts show in the app (on Home and the Refresh page); email alerts come with notifications. Shared-link downloads follow each provider's public-link format and are tested with stand-ins, not yet against live OneDrive, SharePoint and Drive files | Nothing |
| **M13** Professional workspace UI | **First part done** ([mockups](design/workspace-mockups.html)): icon rail and slim top bar; Ctrl I opens the AI as a floating panel; three-pane editors: a Power Query-style Transform grid (type tags, column quality, changed cells, a column menu that adds checked steps, Applied steps with Accept and Reject); a model diagram you arrange by dragging and relate by dragging one column onto another (the engine's checks show before you drop), a formula bar with autocomplete and a live check, and a properties pane; a report canvas on a 12-column grid (move, resize, drop fields to add visuals) with Build and Format panes; one column on phones; page slicers (each viewer's own choice, never saved) and fixed page filters that every visual, KPI, Ask and the Report chat respect; click a bar, slice or row to filter the rest of the page; date charts drill down (Year, month, day) and back up; Merge and Append dialogs in Transform; every colour on theme tokens, with a dark theme (Light, Dark or the computer's setting; PDF exports stay light). One stylesheet: unused rules and always-overridden declarations removed, checked pixel by pixel on 52 screens in both themes. **Still to do:** alignment guides; density; folding the remaining layered rules for the same components together (with fewer !important); tested with 3 to 5 analysts | 3 to 5 analysts for an hour each |
| **M14** Performance and code health ✓ done (the rest moved to M18) | **Done:** measured through the real API at 1M and 10M rows. Column profiling no longer tries every date format on every value, typed columns aren't re-guessed, and above 1M rows the type checks run on a 1M-row sample (counts stay exact); the CSV reader uses the fast parser unless a file has extra values; Transform rebuilds only tables whose steps or data changed; dashboard results are cached per published version, visual and filter. 1M rows end to end: 178 s to 15 s (a step 27 s to under 1 s, publish 30 s to under 1 s). 10M rows: 186 s to 72 s (a step about 11 s, publish 0.4 s, a dashboard about 1 s). Frontend: every page loads when first opened, charts and the sign-in library only where used; the first download went from 1.24 MB to 0.3 MB. Ruff and ESLint, clean; a CI workflow runs them with the tests and the build. The intermittent socket error in the tests is fixed. **Moved to M18:** generated API client, Pyright, Playwright in CI, splitting the largest modules, one error model, connection reuse. **Not needed yet:** history out of records (the largest record is 137 KB) | Nothing |
| **M15** Accounts and brand | Logo and brand, landing page, sign-up and onboarding, account and workspace settings, MFA, Google and Microsoft sign-in, delete and export | Logo direction; Google and Microsoft app registrations |
| **M16** Permissions and RLS ✓ done (share links moved to M18) | **Done:** row-level security roles on each dataset (fixed filters and a per-person filter matched to each viewer's email; members by email or group), enforced where dashboards open their data so visuals, slicer values, Ask, Explain and exports all go through them, and never dropped where page filters are (KPI comparisons); a viewer in no role sees no data; a role restricts the tables that reach its filtered table, and the role editor lists the ones it doesn't; AI insights are hidden from restricted viewers; creators can see a dashboard as any role (and as one person); simple Power BI roles imported; dashboards shared with the whole workspace (the default) or chosen people and groups; owners recorded; groups and an audit log (members, groups, roles, sharing, publishing) on the Members page. **Moved to M18:** share links (they need the public address) | Default sharing: the whole workspace, until you say otherwise |
| **M17** Billing | Plans and limits, AI credit metering, CHIP checkout and renewals (CHIP sandbox while local), receipts, billing page | CHIP sandbox account; final prices later |
| **M18** Go online | Beta: an ngrok free address (or Cloudflare Tunnel, or Oracle Always Free) with a Supabase Free project in Singapore (RM 0). Launch: domain, Resend email, Fly.io with HTTPS, Supabase Pro. Both: staging, CI, secrets, durable data files, job queue, rate limits, security headers; from M14: generated API client, Pyright, Playwright in CI, the largest modules split, one error model, connection reuse; from M16: share links | Beta: nothing to buy. Launch: the domain, a Fly.io account, Supabase Pro |
| **Private beta** | 5 to 10 friendly companies on Free, invited by you, with feedback built in | Pick the beta companies |
| **M19** Compliance and hardening | Legal documents, DPO, breach procedure, transfer assessment, monitoring, backup drill, load test, security review, external penetration test | Lawyer review; DPO named; pen-test budget |
| **Public launch** | Billing switched to live CHIP | CHIP live account (registered business, SSM) |
| **M20** Product depth | Extend a dataset, Power BI report export (dashboards as report pages), database connectors with a gateway agent, Power BI service connector, notifications, scheduled digests, templates, Bahasa Malaysia, help docs | Template priorities |

Why this order:
- **Local first, product first.** Everything up to Gate 1 builds and tests on this computer with the current Seoul test project and Gmail sender. Nothing there needs a domain.
- **Some security work can start early.** Parts of M18 (CI, rate limits, security headers) and of M14 (the job queue) don't need hosting and can be pulled forward whenever convenient.
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

1. **Domain name (at M18):** until then, an ngrok free address or another free option works (see *Free and low-cost options*). `datawazi.com`, `.my`, `.ai` or `.io`? `.com.my` needs a registered Malaysian business.
2. **Hosting:** Fly.io in Singapore (recommended), or AWS or Azure for enterprise buyers who ask.
3. **Default sharing:** new dashboards visible to the whole workspace, or private until shared?
4. **Beta:** which 5 to 10 companies, and when.
5. **Business registration** for CHIP and for issuing invoices: an existing company, or a new Sdn Bhd?
6. **Prices:** finalised after measuring AI cost per workspace in the beta.
7. **Power BI scope for M11:** model import and export first (recommended), with dashboards as Power BI report pages in M20. Or both in M11.
8. **AI engine priorities for M12:** recommended order is evaluation suite and metering, then dashboard filters, then time intelligence, then chat read tools.
