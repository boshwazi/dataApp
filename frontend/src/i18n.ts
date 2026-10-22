/**
 * The interface language: English, or Bahasa Malaysia for the main screens (Home, quick start, Autopilot,
 * dashboards for viewers, Ask, notifications). Text is looked up by its English wording, so untranslated
 * text simply stays English. The choice is kept by the browser and sent with every request, so the AI
 * writes in the same language. Changing it reloads the page.
 */

export type Language = "en" | "ms";

const KEY = "datawazi.language";

function saved(): Language {
  try {
    return localStorage.getItem(KEY) === "ms" ? "ms" : "en";
  } catch {
    return "en";
  }
}

export const language: Language = saved();

export function setLanguage(next: Language) {
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private windows may refuse; it stays English */
  }
  window.location.reload();
}

/** English text, translated when the person works in Bahasa Malaysia. {name} placeholders are filled from vars. */
export function t(text: string, vars?: Record<string, string | number>): string {
  const chosen = language === "ms" ? (MS[text] ?? text) : text;
  return vars ? chosen.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? "")) : chosen;
}

const MS: Record<string, string> = {
  // Rail and workspace menu
  Home: "Utama",
  Datasets: "Set data",
  "Semantic models": "Model semantik",
  Dashboards: "Papan pemuka",
  Members: "Ahli",
  Settings: "Tetapan",
  Notifications: "Pemberitahuan",
  Help: "Bantuan",
  "On this computer": "Di komputer ini",
  "New workspace": "Ruang kerja baharu",
  "AI instructions for the workspace": "Arahan AI untuk ruang kerja",
  "Sign out": "Log keluar",
  Theme: "Tema",
  Light: "Cerah",
  Dark: "Gelap",
  Computer: "Ikut komputer",
  Language: "Bahasa",
  Density: "Kepadatan",
  Comfortable: "Selesa",
  Compact: "Padat",
  Admin: "Pentadbir",
  Creator: "Pencipta",
  Viewer: "Pemapar",

  // Home
  "Filter by name": "Tapis mengikut nama",
  "New dataset": "Set data baharu",
  "New dashboard": "Papan pemuka baharu",
  "Connect your AI provider first:": "Sambungkan penyedia AI anda dahulu:",
  "open Settings": "buka Tetapan",
  "and run the connection test.": "dan jalankan ujian sambungan.",
  "The AI isn't connected yet. The people running datawazi need to set it up.":
    "AI belum disambungkan. Pengendali datawazi perlu menyediakannya.",
  "{name}: the scheduled refresh failed": "{name}: muat semula berjadual gagal",
  "{name}: the scheduled refresh passed with warnings": "{name}: muat semula berjadual lulus dengan amaran",
  "{name}: the refresh after a linked table changed failed": "{name}: muat semula selepas jadual terpaut berubah gagal",
  "{name}: the refresh after a linked table changed passed with warnings":
    "{name}: muat semula selepas jadual terpaut berubah lulus dengan amaran",
  "Open Refresh": "Buka Muat semula",
  "No dashboards yet": "Belum ada papan pemuka",
  "Nothing published yet": "Belum ada yang diterbitkan",
  "A dashboard is built by the AI from a dataset's measures. Click New dashboard to make the first one.":
    "Papan pemuka dibina oleh AI daripada ukuran set data. Klik Papan pemuka baharu untuk membuat yang pertama.",
  "Dashboards show up here once someone in your team publishes them.":
    "Papan pemuka akan muncul di sini sebaik sahaja seseorang dalam pasukan anda menerbitkannya.",
  Name: "Nama",
  Dataset: "Set data",
  Pages: "Halaman",
  Status: "Status",
  Updated: "Dikemas kini",
  Removed: "Dibuang",
  "Draft changes": "Ada perubahan draf",
  Published: "Diterbitkan",
  Draft: "Draf",
  "Load, clean and model data once, then reuse it in any number of dashboards.":
    "Muat, bersihkan dan modelkan data sekali, kemudian guna semula dalam mana-mana papan pemuka.",
  "No datasets yet": "Belum ada set data",
  "Start with a CSV, an Excel file, a Google Sheet or the sample data.":
    "Mulakan dengan fail CSV, Excel, Google Sheet atau data contoh.",
  Tables: "Jadual",
  Steps: "Langkah",
  Measures: "Ukuran",
  "Used by": "Digunakan oleh",
  "Draft changes, v{v} live": "Ada perubahan draf, v{v} aktif",
  "Published v{v}": "Diterbitkan v{v}",
  "Not published": "Belum diterbitkan",
  "Just now": "Baru sahaja",
  "{n} min ago": "{n} minit lalu",
  "{n} h ago": "{n} jam lalu",
  Yesterday: "Semalam",
  "{n} days ago": "{n} hari lalu",

  // New dashboard
  "Use existing data": "Guna data sedia ada",
  "Build on a published dataset; it stays in sync.": "Bina atas set data yang diterbitkan; ia kekal selaras.",
  "No published datasets yet.": "Belum ada set data yang diterbitkan.",
  "Bring new data": "Bawa data baharu",
  "Load files or a Google Sheet; the AI cleans and models it first.":
    "Muat naik fail atau Google Sheet; AI membersihkan dan memodelkannya dahulu.",
  "Dashboard name": "Nama papan pemuka",
  "Create dashboard": "Cipta papan pemuka",
  "Start with data": "Mula dengan data",
  "Creating…": "Sedang mencipta…",
  "Designing the pages…": "Sedang mereka bentuk halaman…",
  "Start with": "Mula dengan",
  "Let the AI choose": "Biar AI pilih",
  "It picks the template that fits this data and fills it.": "AI memilih templat yang sesuai dengan data ini dan mengisinya.",
  "A blank page": "Halaman kosong",
  "Add visuals yourself, or ask the AI later.": "Tambah visual sendiri, atau minta bantuan AI kemudian.",
  "What should it show? (optional)": "Apa yang patut ditunjukkan? (pilihan)",
  "For example: a monthly view for branch managers, focused on which products are slowing down":
    "Contohnya: paparan bulanan untuk pengurus cawangan, fokus pada produk yang semakin perlahan",

  // Quick start
  "Drop a spreadsheet and get a dashboard": "Letakkan hamparan dan dapatkan papan pemuka",
  "CSV or Excel. Autopilot cleans it, models it and designs a draft dashboard; you can change any step later.":
    "CSV atau Excel. Autopilot membersihkan, memodelkan dan mereka bentuk draf papan pemuka; anda boleh ubah mana-mana langkah kemudian.",
  "Choose files": "Pilih fail",
  "Creating the dataset…": "Sedang mencipta set data…",
  "Uploading…": "Sedang memuat naik…",

  // Autopilot
  "Let datawazi build it for you": "Biar datawazi bina untuk anda",
  "Building your dashboard": "Sedang membina papan pemuka anda",
  "Your dashboard is ready": "Papan pemuka anda sudah siap",
  "Autopilot stopped": "Autopilot berhenti",
  "Autopilot cleans your tables, builds the model, publishes the dataset and designs a dashboard. It accepts the AI's checked changes for you; the dashboard stays a draft until you publish it.":
    "Autopilot membersihkan jadual, membina model, menerbitkan set data dan mereka bentuk papan pemuka. Ia menerima perubahan AI yang telah disemak bagi pihak anda; papan pemuka kekal draf sehingga anda menerbitkannya.",
  "You can leave this page; it keeps going. Open any step to see what it did, or ask the AI to change something.":
    "Anda boleh tinggalkan halaman ini; ia terus berjalan. Buka mana-mana langkah untuk melihat apa yang dibuat, atau minta AI mengubah sesuatu.",
  "Everything Autopilot added is marked, so you can review it, change it in each step, or ask the AI.":
    "Semua yang ditambah oleh Autopilot ditanda, jadi anda boleh menyemak, mengubahnya dalam setiap langkah, atau bertanya kepada AI.",
  "Start Autopilot": "Mulakan Autopilot",
  "Starting…": "Sedang bermula…",
  "Add some data first in": "Tambah data dahulu di",
  "Get Data": "Dapatkan Data",
  "Read your data": "Baca data anda",
  "Clean the tables": "Bersihkan jadual",
  "Build the model": "Bina model",
  "Publish the dataset": "Terbitkan set data",
  "Design a dashboard": "Reka papan pemuka",
  "Working on it…": "Sedang diusahakan…",
  Waiting: "Menunggu",
  "See what it did": "Lihat apa yang dibuat",
  "Fix it": "Betulkan",
  "View dashboard": "Lihat papan pemuka",
  "Run again": "Jalankan semula",
  "Try again": "Cuba lagi",
  "Open {stage}": "Buka {stage}",
  "Step {n}: {status}": "Langkah {n}: {status}",

  // Notifications
  "Notifications, {n} new": "Pemberitahuan, {n} baharu",
  "Nothing yet. Failed refreshes, finished Autopilot runs and dashboards shared with you show up here.":
    "Belum ada. Muat semula yang gagal, Autopilot yang selesai dan papan pemuka yang dikongsi dengan anda akan muncul di sini.",
  "just now": "baru sahaja",

  // Dashboard top bar
  Edit: "Sunting",
  "View as viewer": "Lihat sebagai pemapar",
  "Draft, not published": "Draf, belum diterbitkan",
  ", data as of {date}": ", data setakat {date}",
  "Ask AI": "Tanya AI",
  Ask: "Tanya",
  Present: "Persembahkan",
  Publish: "Terbitkan",
  "Publishing…": "Sedang menerbitkan…",
  "More actions": "Tindakan lain",
  Rename: "Namakan semula",
  "AI instructions": "Arahan AI",
  Share: "Kongsi",
  "Email digest": "Ringkasan e-mel",
  Activity: "Aktiviti",
  "Export PDF": "Eksport PDF",
  "Download for Power BI": "Muat turun untuk Power BI",
  "Downloaded a Power BI project (.zip) with these pages. Unzip it and open the .pbip in Power BI Desktop.":
    "Projek Power BI (.zip) dengan halaman ini dimuat turun. Nyahzip dan buka fail .pbip dalam Power BI Desktop.",
  "See all the data": "Lihat semua data",
  "See it as {name}": "Lihat sebagai {name}",
  "Nothing published yet.": "Belum ada yang diterbitkan.",
  "Loading…": "Sedang dimuatkan…",

  "Publish the draft to show it here.": "Terbitkan draf untuk memaparkannya di sini.",

  // Ask
  "Ask about this data": "Tanya tentang data ini",
  "Answers show how each number was calculated": "Jawapan menunjukkan bagaimana setiap nombor dikira",
  "Try:": "Cuba:",
  "Working it out…": "Sedang mengira…",
  "Ask a question about this data…": "Tanya soalan tentang data ini…",
  "Ask a question about the data": "Tanya soalan tentang data",
  "How this was calculated": "Bagaimana ini dikira",
  "Add to the page": "Tambah ke halaman",
  "Thinking…": "Sedang berfikir…",
  Stop: "Henti",
  Send: "Hantar",
  Close: "Tutup",
  Explain: "Terangkan",
};
