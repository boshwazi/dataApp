/** The help articles: short, task-first, in the app's own words. Rendered with the Markdown component. */

export type Article = { id: string; title: string; summary: string; for: "everyone" | "creators" | "admins"; body: string };

export const ARTICLES: Article[] = [
  {
    id: "start",
    title: "Start here: from a spreadsheet to a dashboard",
    summary: "Drop a file on Home and Autopilot does the rest.",
    for: "creators",
    body: `
On **Home**, drop a CSV or Excel file on **Drop a spreadsheet and get a dashboard**, or click **Choose files**.

Autopilot then works through five steps, and you can watch each one finish:

1. **Read your data**: the AI describes each table.
2. **Clean the tables**: it adds steps such as fixing types, trimming spaces and removing duplicates.
3. **Build the model**: relationships between tables, a Date table and measures such as Revenue.
4. **Publish the dataset**, so dashboards can use it.
5. **Design a dashboard**: pages of KPI cards and charts, kept as a **draft**.

Everything Autopilot adds is checked first and marked **Added by Autopilot**, so you can review it. Open any step to see what it did, change it, or ask the AI. Nothing reaches viewers until you click **Publish** on the dashboard.

Already have a dataset? On its **Get Data** page, click **Do it for me**.
`,
  },
  {
    id: "get-data",
    title: "Getting data in",
    summary: "Files, shared links, Google Sheets, Power BI files and tables from other datasets.",
    for: "creators",
    body: `
Open a dataset and go to **Get Data**:

- **Files**: drop CSV or Excel files (each Excel sheet becomes a table), up to 200 MB.
- **Shared links**: paste a link to a Google Sheet, or to a file on OneDrive, SharePoint or Google Drive shared as "Anyone with the link can view". These can be pulled again on a schedule.
- **Link a table from another dataset**: reuse a clean table someone else published, such as a certified Customers table. It follows that dataset when it publishes.
- **Import from Power BI**: a .pbix file (model and data), or a .pbip project as a .zip.
- **Sample data**, to try things out.

Each table is profiled as it loads: types, blanks, ranges and issues such as values with currency symbols. The AI describes what each column means.
`,
  },
  {
    id: "transform",
    title: "Cleaning tables (Transform)",
    summary: "Steps that run in order on every refresh.",
    for: "creators",
    body: `
**Transform** shows each table as a grid with **Applied steps** on the right. Steps run in order, from the raw data, every time the data changes.

- Click or right-click a column for its actions: change type, trim, replace values, fill blanks, split, and more.
- **Remove duplicates**, **Add column** (a calculated column), **Merge** (join two tables) and **Append** (stack tables) are in the toolbar.
- **Clean with AI** suggests steps; each one waits for **Accept** or **Reject**.
- **Before**, **After** and **Removed** show what a step changed.

Undo works step by step. Nothing reaches dashboards until the dataset is published.
`,
  },
  {
    id: "model",
    title: "Relationships and measures (Model)",
    summary: "How tables connect, and the numbers dashboards show.",
    for: "creators",
    body: `
The **Model** diagram shows tables and how they relate. Drag a column onto another table's column to relate them; the engine checks the match before you drop.

**Measures** are the numbers on dashboards. Write them in the formula bar, for example:

- \`SUM([Column])\`, \`AVERAGE( )\`, \`COUNTROWS()\`, \`DISTINCTCOUNT( )\`, \`MIN( )\` or \`MAX( )\` over a table's columns
- \`[Revenue] / [Orders]\`: a formula over other measures
- \`YTD([Revenue])\`, \`QTD( )\`, \`MTD( )\`, \`LASTYEAR([Revenue])\`, \`LASTYEARYTD( )\` and \`ROLLING([Revenue], 3)\` for time intelligence (they need the **Date table**)
- \`IF([Qty] >= 10, [Qty] * [Price], 0)\` and \`SWITCH( )\` for conditions, and measure filters such as Region is North

Every measure is checked as you type and shows its total. **Suggest with AI** proposes relationships, a Date table and measures for you to accept.
`,
  },
  {
    id: "dashboards",
    title: "Building dashboards",
    summary: "Pages, visuals, templates, slicers and drill-down.",
    for: "creators",
    body: `
Click **New dashboard**, pick a published dataset, and choose how to start: **Let the AI choose** a template, a template such as **Sales performance** or **Finance**, or **A blank page**. You can describe what you need in your own words.

On the canvas:

- Drag fields from the right onto the page to add visuals; drag and resize visuals on the grid.
- **Build** and **Format** set a visual's type, fields, title and colours.
- **Slicers** let each viewer filter a page for themselves; **Page filters** apply to everyone.
- Clicking a bar or slice filters the rest of the page; date charts drill down from year to month to day.

**Publish** makes the draft what viewers see. **View as viewer** shows it as they will.
`,
  },
  {
    id: "ask",
    title: "Asking questions about the data",
    summary: "Ask in plain words; every number is checked.",
    for: "everyone",
    body: `
On any dashboard, click **Ask** (or press **Ctrl I**) and type a question, such as "Which region grew fastest this year?" or "Why did revenue drop last quarter?".

The answer comes from the dataset itself, not from guesses. Open **How this was calculated** to see the measures, filters and tables used. Numbers in AI-written text that don't match the data are flagged as unverified.

Slicers and filters on the page apply to Ask, and so do any limits on what you're allowed to see.

**Explain** on a chart asks the same question about that chart.
`,
  },
  {
    id: "refresh",
    title: "Keeping data up to date",
    summary: "Schedules, replacing files, appending rows and rolling back.",
    for: "creators",
    body: `
Open a dataset's **More actions** menu and choose **Refresh data**.

- **Schedules**: Google Sheets, shared links and linked tables can be pulled every hour, every 6 hours, or daily, on weekdays or weekly at a time you choose (Malaysia time). A pull that can't download is retried, then you get a notification.
- **Uploaded files**: click **Replace file** with a newer one.
- **Append rows**: for files that only hold new rows, set the table to **Append rows**; choose a key column to skip rows already loaded.
- **Publish automatically when the checks pass**: new data reaches dashboards without a click; a refresh that fails its checks is never published.
- **Published versions**: the last five are kept. **Roll back to this** if new data was wrong.
`,
  },
  {
    id: "share",
    title: "Sharing, roles and who sees what",
    summary: "Workspace roles, dashboard sharing and row-level security.",
    for: "creators",
    body: `
**Workspace roles** (admins set them on **Members**):

- **Admin**: everything, plus people, groups and the audit log.
- **Creator**: builds and publishes datasets and dashboards.
- **Viewer**: sees published dashboards and asks questions about them.

**Sharing a dashboard**: in its **More actions** menu, choose **Share**: everyone in the workspace (the default), or chosen people and groups.

**Row-level security**: in a dataset's **More actions** menu, choose **Security**. A role limits rows, with a fixed filter (Region is North) or by matching a column to each person's email. Once a dataset has roles, viewers in none of them see no data. Creators can check with **See it as** on a published dashboard.
`,
  },
  {
    id: "semantic",
    title: "Combining datasets",
    summary: "Semantic models and extending a dataset.",
    for: "creators",
    body: `
**Semantic models** (in the left rail) put tables from several published datasets in one model, without loading them again:

- **Merge datasets** takes every table. A table with the same name and columns in two datasets (Customers, say) is kept once, so the models meet on it.
- **Pick tables** takes only the tables you choose.

Relationships and measures come along, and the AI looks for links between the tables.

**Extend this dataset** (in a dataset's **More actions** menu) starts a new dataset on top of a published one: its tables and model are inherited, read-only and kept in step, and you add your own data and measures alongside.
`,
  },
  {
    id: "powerbi",
    title: "Working with Power BI",
    summary: "Import .pbix files, export models and dashboards.",
    for: "creators",
    body: `
- **Import**: on **Get Data**, import a .pbix (model and data) or a .pbip project. DAX measures are translated; the import report lists any that weren't.
- **Export a dataset**: **More actions > Download for Power BI** gives a .pbip project with the model in TMDL, measures in DAX and the data as Parquet files.
- **Export a dashboard**: on a dashboard, **More actions > Download for Power BI** adds its pages as Power BI report pages.

Unzip the project, open the .pbip in Power BI Desktop, set the **DataFolder** parameter to the project's data folder if you moved it, and refresh.
`,
  },
  {
    id: "notices",
    title: "Notifications and email digests",
    summary: "The bell, and a weekly or monthly email of a dashboard.",
    for: "everyone",
    body: `
The **bell** in the left rail lists what happened while you were away: a refresh that failed, Autopilot finishing, a dashboard shared with you or updated, a digest.

**Email digests** (a dashboard's **More actions > Email digest**) send a dashboard's page summaries and KPI numbers every week or month, with a link to it. They go only to workspace members the dashboard is shared with. With row-level security, a digest carries no numbers, just the link.
`,
  },
  {
    id: "language",
    title: "Bahasa Malaysia",
    summary: "Use the main screens and the AI in Bahasa Malaysia.",
    for: "everyone",
    body: `
Click your workspace at the bottom of the left rail and choose **Bahasa Melayu** under **Language**. The main screens switch to Bahasa Malaysia, and the AI writes its answers, insights and suggestions in Bahasa Malaysia. Numbers stay as the data gives them, so they can still be checked.

The choice is kept on this computer and browser.
`,
  },
  {
    id: "instructions",
    title: "Telling the AI how your business works",
    summary: "Custom instructions for the workspace, a dataset or a dashboard.",
    for: "creators",
    body: `
Write instructions such as "Amounts are in RM", "Our financial year starts in April" or "Call customers clients", and every AI that works on that part follows them, after its own rules:

- For the whole workspace: the workspace menu, **AI instructions for the workspace** (admins).
- For a dataset or a dashboard: its **More actions > AI instructions**.

The data's facts and the checks always come first.
`,
  },
];
