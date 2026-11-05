# datawazi UI/UX overhaul: proposal

5 November 2026. A study, not yet built. The preview is [overhaul-preview.html](overhaul-preview.html), with a
screenshot in [overhaul-preview.png](overhaul-preview.png).

## Direction in one line

**DataHub's layout and colours, built to Vercel's Geist discipline.** DataHub provides the dark teal sidebar,
the cyan for "you are here", the greeting overview and the card grid. Vercel/Geist
([Refero style](https://styles.refero.design/style/f24daf3a-d43f-4dec-85a9-8ac1d5148a03)) provides the
monochrome canvas, Geist Sans at weights 400–500, Geist Mono for small uppercase labels, a 6px radius
everywhere, hairline rings instead of drop shadows, and near-black primary buttons.

The two already share their base: DataHub's redesign tokens (`#171717` ink, `#4D4D4D`, `#EAEAEA` lines,
Geist) were taken from Vercel. So the mix is natural, not a compromise.

## What's wrong with the current UI

Seen on Home, Dashboard, Transform and Model, and measured in `frontend/src/styles.css`:

| Problem | Evidence | Effect |
|---|---|---|
| Too many colours with jobs | Blue for actions, yellow for AI *and* for "where you are", purple-ish selections, green/orange/red status | Every screen is busy; you can't tell at a glance what wants your attention |
| Yellow does two jobs | The ringed current step and AI suggestions both use yellow | You can't tell whether something is AI's or just selected |
| The sidebar is icon-only by default (38px) | No labels until you expand it | New users guess what the icons mean; DataHub shows names |
| Primary buttons are glossy | Gradient plus inset highlight on `Publish` | Reads as "tactile", not "precise"; fights the flat data surfaces |
| The stylesheet has grown by override | 9,655 lines; 89 distinct hex colours; 42 `box-shadow`s; 10 different radii (2–12px); a "Night components" block at line 9343 that re-styles earlier rules | Every change risks a side effect; the look drifts screen to screen |
| Labels and data share one typeface | Column types, counts, step numbers and dates sit in body type | Metadata competes with content |
| The AI panels are loud | Large yellow boxes on Model and Dashboard ("Suggested by AI", "Waiting for you (12)") | AI dominates; the user's own work looks secondary |

What's good and stays: the information architecture (dataset steps, Applied Steps, Before/After, Lineage),
Ctrl+K, density settings, the error boundary, and the plain-English copy.

## The system

### Colour: one accent, one AI marker, everything else neutral

| Token | Value | Job |
|---|---|---|
| `--bg` | `#FAFAFA` | Page canvas |
| `--surface` | `#FFFFFF` | Cards, panels, inputs |
| `--line` / `--line-strong` | `#EBEBEB` / `#D9D9D9` | Hairlines; hover borders |
| `--ink` … `--ink-4` | `#171717` `#4D4D4D` `#666666` `#A3A3A3` | Text ramp; `--ink` also fills primary buttons |
| `--accent` | `#00A9E2` (text `#0072A3`, tint `#E8F7FD`) | **Only** where you are: current nav item, current step, selected row or column, focus ring, links |
| `--ai` | `#B45309` on `#FFF8EB` | **Only** AI work waiting for your decision; a small mono `AI` tag on anything AI made |
| `--ok` `--warn` `--bad` | `#18A058` `#F5A524` `#E5484D` | Status dots and quality bars only, never fills |
| `--side` | `#13262E` | Sidebar (DataHub's navy-teal) |

Rules: primary actions are near-black (`--ink`), never coloured. Cyan never means AI. Amber never means
selection. Once you accept an AI change it loses its amber and keeps only the small `AI` tag, as Applied Steps
already does with `AI`/`Approved`.

### Type

- **Geist Sans** for all UI: 14px body, 13.5px controls, 15px section titles, 30px greeting (weight 450,
  -0.05em). No weight above 600.
- **Geist Mono**, 11px uppercase with 0.071em tracking, for labels: table headers, eyebrows, column types
  (`ABC`, `DATE`, `1.2`), step numbers (`01`–`05`), counts, dates in lists, the status bar and keyboard hints.
  Data cells in the Transform grid use mono 12.5px so digits line up.
- Self-host both (DataHub already ships `Geist-Variable.woff2`; Geist Mono is OFL too). Drop Host Grotesk and
  Fragment Mono.

This departs from DataHub on one point: DataHub uses Geist Sans only, with mono for DAX/code. datawazi is a
data tool, so the mono labels earn their place. Keep them to metadata.

### Shape and depth

- Radius: 6px for buttons, inputs and cards; 4px for tags and kbd; 999px only for status pills. Remove the
  other radii.
- No drop shadows on the page. Cards use a 1px ring (`0 0 0 1px rgba(0,0,0,.08)`). Only floating things
  (menus, the Ctrl+K palette, dialogs, toasts) get DataHub's `--shadow-pop`.
- 4px spacing grid. Card padding 16px; page padding 28–32px; 12px between elements.

### Components to rebuild (one file each, replacing the scattered rules)

Button (default, primary, ghost, ai, danger; sizes 26/32), icon button, segmented control (Before/After,
list/grid), input/select/search with `Ctrl K`, pill (status dot + text), `AI` tag, card, data table (mono
headers, hairline rows), sidebar nav item, step bar, panel header (mono label + count), empty state, toast.

## Screen by screen

**Sidebar.** Expanded by default at 232px with labels (DataHub), collapsible to icons. Workspace name under
the logo. Search/Ctrl K at the top. Sections: main (Home, Dashboards, Datasets, Semantic models), then a
mono-caps `WORKSPACE` group (Members, Billing, Settings). Current item: cyan tint, 2px cyan left rule, cyan
icon. Account at the foot.

**Top bar.** Breadcrumb (muted parents, ink current), a status pill (`Draft · v1 is live`), then actions at
the right: secondary (outline), `Ask AI` (amber outline), primary (`Publish`, near-black). One primary per view.

**Home.** DataHub's greeting card with 3–4 mono-labelled counts and the striped cyan bar sparkline (activity
over the last 9 weeks). The counts include "AI changes to review" so pending AI work is visible from the start.
Recent dashboards as thumbnail cards plus a dashed "New dashboard" card. Datasets and dashboards as hairline
tables, not boxed lists. The "Next steps" checklist becomes a slim dismissible strip, shown only until it's done.

**Dataset step bar.** `01 ✓ Get data — 02 Transform — 03 Model — 04 Publish — 05 Dashboard` in one row:
mono numbers, green tick when done, the current step on a grey tab with a cyan number. No yellow ring.

**Transform.** Three panes divided by hairlines (tables | grid | Applied Steps). The grid: mono column-type
eyebrow, a 2px quality bar under each header (green, with amber for blanks), mono cells, and the changed column
tinted cyan. Applied Steps are numbered in mono; the selected one has a cyan left rule; a pending AI step is
an amber card with Accept/Reject inline. A mono status bar at the foot (`● ALL STEPS VALID · 40 OF 40 ROWS`).

**Model.** Same rules on the diagram: table cards with ring borders and mono column types, relationship lines
in `--ink-4` (cyan when selected), the Date table no longer yellow. The "Waiting for you (12)" list moves into
one review queue: a compact list with Accept all, each item one line until expanded.

**Dashboard editor.** Page thumbnails in the left rail as now. KPI cards follow the house style but lose the
yellow left edge (that was "AI-made"; use the `AI` tag). The right properties panel uses mono section labels
(`PAGE`, `SLICERS`, `FIELDS`). Dashboards themselves stay light, as already decided.

**Ask / chat panels.** A right drawer with the Vercel terminal feel: user turns plain, AI turns with `✦`
and a mono "How this was calculated" disclosure. "Unverified" becomes an amber pill, not inline red text.

**Dark theme.** Keep it (the app follows the device). Rebuild it from the same tokens: `#0A0A0A` canvas,
`#111` surfaces, `#1F1F1F` lines; the sidebar stays `#13262E`, so it barely changes between themes.

**Landing page.** Out of scope for this pass; it was just redone. Once the app is done, align its type and
buttons so the jump from landing to app doesn't jar.

## Motion and small details (from the reference galleries)

- **AI fills the grid** (recent.design "AI Spreadsheet Fill"): when Clean with AI or Build model runs, the
  affected column or table shimmers once, then cells settle row by row (about 300ms, `--ease-out`). This shows
  the AI's work landing where it applies, instead of only in a side panel.
- **Hold to publish** (60fps.design, Opal "Hold to Commit"): optional for publishing a dataset that would break
  visuals. You hold for 600ms while the button fills. It's safer than a dialog, and it stays a dialog under
  reduced motion.
- **Card to full screen** (60fps.design, Trackables "Card Morph"): opening a dashboard from Home expands its
  card into the page. Optional, for later.
- Keep the existing curves (`--ease-out`, `--ease-drawer`). Hover transitions 120ms; panels 200ms; nothing
  over 300ms in the app.
- Linear's rule fits: **one** accented primary call to action per view.

The other galleries (navbar.gallery, bentogrids, 404s, designspells) are mostly marketing or mobile, so they
added little for an app. A plain DataHub-style error page (already in `ErrorBoundary`) is enough.

## How to build it

The safest path is a fresh design-system layer, moved over screen by screen, not edits to the 9,655-line file.

1. **Foundations.** New `tokens.css` (light + dark) and `components.css`; self-host Geist and Geist Mono;
   map the old variable names to new values, so every screen shifts at once without layout changes. Check every
   page in light and dark.
2. **Shell.** Sidebar, top bar, Ctrl+K palette, step bar, toasts.
3. **Home, Dashboards and Datasets lists.**
4. **Transform and Get Data** (grid, Applied Steps, AI pending state, status bar).
5. **Model** (diagram, review queue).
6. **Dashboard editor, Ask, Present, PDF export** (keep dashboards light and charts on the house palette).
7. **Settings, Members, Billing, Account, Help, Onboarding.**
8. **Cleanup.** Delete superseded rules from `styles.css` (target under 4,000 lines), run the `break-ui`
   worst-case check on each screen, and do an accessibility pass (contrast, focus rings, reduced motion).

Each step is one commit to `main` and is checked in the browser in light, dark and compact density.

## Decisions (5 November 2026)

1. **Brand colour.** The logo mark keeps its blue with the yellow AI slice for now; it's revisited with the landing page.
2. **AI colour.** Amber, matching the logo's yellow slice.
3. **Mono labels.** Geist Mono for labels and metadata.
4. **Sidebar default.** Expanded with labels (done in step 2).

## Progress

- **Step 1, foundations: done.** Geist and Geist Mono are self-hosted (`@fontsource-variable`). The landing page
  keeps Host Grotesk and Fragment Mono. A "Geist foundations" block at the end of `styles.css` re-points
  every token in light, dark and the always-light dashboard surface. It also flattens buttons (near-black
  primary, hairline secondary) and cards (6px, no shadow), and turns the sidebar's current item cyan. Font weights
  went down one step (700/650 → 600, 600/550 → 500), because Geist reads heavier than Host Grotesk.
- **Step 2, shell: done.**
  - The sidebar opens with names by default (232px). It has the workspace name under the logo, search with
    Ctrl K at the top, mono section labels, and a cyan tint and rule on the current item. Ctrl+\ still
    collapses it to icons, and the top-bar search returns when it's collapsed.
  - The top bar is white with a hairline, and its status is a pill.
  - Dataset steps are numbered in mono (`01 ✓ Get Data — 02 Transform — 03 Model`).
  - Menus, the Ctrl K palette and toasts are flat and use Geist.
  - Editor tools are ghost buttons; the AI tool keeps its amber outline.
  - Known issue for step 3: Home's Datasets table is wider than a phone screen (424px on a 375px screen).
