/** How tightly tables, grids and panels are spaced: comfortable (the default) or compact, per browser. */

export type Density = "comfortable" | "compact";

const KEY = "datawazi.density";

export function savedDensity(): Density {
  try {
    return localStorage.getItem(KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function applyDensity(choice: Density = savedDensity()) {
  document.documentElement.dataset.density = choice;
}

export function setDensity(choice: Density) {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    // private mode: the choice lasts until reload
  }
  applyDensity(choice);
}
