import { useSyncExternalStore } from "react";

/** Light, dark, or whatever the computer is set to. The choice is kept in this browser. */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "theme";
const dark = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function savedTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function resolve(choice: ThemeChoice): "light" | "dark" {
  return choice === "system" ? (dark?.matches ? "dark" : "light") : choice;
}

/** Sets <html data-theme> (the stylesheets key off it) and tells charts to repaint. */
export function applyTheme(choice: ThemeChoice = savedTheme()) {
  document.documentElement.dataset.theme = resolve(choice);
  window.dispatchEvent(new Event("themechange"));
}

export function setTheme(choice: ThemeChoice) {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    // private mode: the choice lasts until reload
  }
  applyTheme(choice);
}

dark?.addEventListener("change", () => savedTheme() === "system" && applyTheme());

function subscribe(callback: () => void) {
  window.addEventListener("themechange", callback);
  return () => window.removeEventListener("themechange", callback);
}

/** The theme on show now, for code that paints its own colours (charts). */
export function useTheme(): "light" | "dark" {
  return useSyncExternalStore(subscribe, () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"));
}

/** Where something is painted: on the app's theme, or on a light surface (dashboards stay light in every theme). */
export type Surface = "theme" | "light";

let lightProbe: HTMLElement | null = null;

/** A token's current value, for libraries that need real colours rather than CSS variables. */
export function token(name: string, surface: Surface = "theme"): string {
  if (surface === "light" && !lightProbe?.isConnected) {
    // An empty element with the light surface's tokens, read like any other.
    lightProbe = document.createElement("div");
    lightProbe.className = "light-surface";
    lightProbe.hidden = true;
    document.body.appendChild(lightProbe);
  }
  return getComputedStyle(surface === "light" ? lightProbe! : document.documentElement).getPropertyValue(name).trim();
}
