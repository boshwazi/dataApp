# Security, performance and flow audit — 3 November 2026

datawazi holds companies' private data, so this audit looks at how that data could leak, be changed by the wrong
person, or be reached from outside, and at how the app behaves when something fails.

## What was already sound

| Area | What we checked | Result |
|---|---|---|
| Sign-in | Every `/api/` call except health, sign-in settings and the payment callback needs a token; the token's signature, expiry, audience and issuer are verified | Sound |
| Workspaces kept apart | Every record id must be 12 hex characters and is looked up inside the caller's own workspace (files and Postgres); Postgres also has row-level security | Sound: an id from another workspace is simply not found |
| Roles | Viewers can read published dashboards and ask about them; everything else needs creator; members and settings need admin. Checked on the server, not only hidden in the app | Sound |
| SQL injection (Postgres) | Only fixed table names go into SQL text; every value is a parameter | Sound |
| SQL injection (DuckDB, the data engine) | The AI never writes SQL: it picks tables and columns from a fixed list, and measures use a small formula language parsed and compiled by the server. Names and text are quoted; values are parameters. DuckDB runs with external access off, so SQL can't read other files or URLs | Sound |
| Invites | 24 random bytes, stored hashed, compared in constant time, expire, and only the invited email can accept | Sound |
| Joining by email domain | Only offered to sign-ins that verify the email (email, Google); Microsoft is left out on purpose | Sound |
| Cross-site scripting | No raw HTML is rendered anywhere; AI text is rendered as plain React elements without links | Sound |
| Cross-site request forgery | The API uses a bearer token, not cookies, and allows no other sites (no CORS) | Not applicable |
| Payments | The CHIP callback is checked by its RSA signature; totals are re-checked against the server's own prices | Sound |
| Uploads | Capped at 200 MB per file, 50 MB per fetched link | Sound (see "still to do") |
| Frontend packages | `npm audit` | 0 known vulnerabilities |

## Found and fixed

| # | Finding | Risk | Fix |
|---|---|---|---|
| 1 | Platform admins were recognised by email from any sign-in. A Microsoft tenant can put any address in its token ("nOAuth"), so with Microsoft sign-in on, someone could claim an operator's address and change the AI key or where AI requests (with data samples) are sent | High | Platform admin now also needs a sign-in that verifies the email (email or Google) |
| 2 | Without sign-in configured, the app treats every request as an admin. If that copy were ever reachable from a network, all data would be open | High | A copy without sign-in now refuses anything not from the computer it runs on |
| 3 | No browser security headers: any site could frame the app (clickjacking), and nothing limited where scripts load from | Medium | Content Security Policy (scripts only from the app, no framing, no plugins), `X-Frame-Options: DENY`, `nosniff`, a strict referrer policy, permissions policy, HSTS over https, and `Cache-Control: no-store` on every API response so company data never sits in a cache |
| 4 | Shared-link import followed redirects without checking them, so an allowed host that redirects could make the server fetch an internal address (SSRF, for example a cloud metadata service) | Medium | The link and every redirect must be https and resolve to a public address |
| 5 | No rate limits: a runaway script could hammer the API or use up a month's AI credits in minutes | Medium | Per person: 300 changes and 120 AI calls a minute (hosted only; the local copy is one person) |
| 6 | Every signed-in user could read the AI provider's address, model and the key's last four characters | Low | Others now see only whether AI is set up |
| 7 | A dataset's name went into a download header unescaped | Low | Quotes, slashes and control characters are removed |

All seven are covered by `backend/tests/test_protect.py`.

## Still to do (needs a decision or your accounts)

1. **Supabase: keep "Confirm email" on** (Authentication → Providers → Email). Invites and domain joins trust the email address; an unconfirmed sign-up must not get a session.
2. **Microsoft sign-in**: when it's added, restrict it to known tenants, or require the `xms_edov` claim, so its email can be trusted for invites.
3. **The AI key is stored as plain text** in the settings store. In production, set it from the environment (`LLM_API_KEY`), which never writes it to disk, or encrypt it with a key held outside the database.
4. **Python packages**: `pip-audit` isn't installed, so the backend's packages weren't checked against the vulnerability database. Add it to CI: `pip install pip-audit && pip-audit`.
5. **Excel files that expand hugely** (a 200 MB .xlsx can unpack to far more): set a lower upload cap per plan, and check the unpacked size before reading.
6. **Rate limits are per server process.** With more than one server, move the counters to Postgres or Redis.
7. Before launch: an external penetration test, and the PDPA items already on the list (privacy notice, data protection officer, breach procedure).

## Performance

Measured on the local copy with the sample data, by loading every page fresh.

| Measure | Before | After |
|---|---|---|
| Downloaded per full page load | about 590 KB | 80–150 KB (compressed) |
| Repeat visits | page files fetched again | page files cached for a year (names change with every build) |
| Repeated API calls on a page | Home asked for the sign-in settings twice | none |
| Time until a page shows its content | Home 130–200 ms, settings pages about 370 ms, dataset stages 0.8–0.9 s | unchanged (mostly downloading and starting the page's code) |

API responses are deliberately not compressed, so a secret is never compressed alongside text a visitor chose (BREACH).

## Flow

- Every internal link on every page (41) was followed: all land on a working page.
- The blank white screens came from page files that no longer existed after a new build (an open tab asked for the old
  names; the server log showed 404s). A page whose file fails to load now reloads once for the new version, and
  every page sits inside an error boundary: if anything still fails, it shows a message with Reload and Go to Home,
  and the sidebar stays usable.
