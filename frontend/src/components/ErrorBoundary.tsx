import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "../i18n";

/**
 * A page's code is fetched when the page is first opened (see page() in main.tsx). After a new version is deployed,
 * an open tab asks for files that no longer exist; reloading once picks up the new version. The guard stops a reload
 * loop when the files are truly missing (offline, or a broken deploy): then the error is shown instead.
 */
const RELOADED_KEY = "datawazi.reloaded-for-update";

export function isChunkError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError/i.test(message);
}

export function reloadForUpdate(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOADED_KEY) ?? 0);
    if (Date.now() - last < 30_000) return false; // reloaded a moment ago and it still fails
    sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
  } catch {
    return false; // can't remember the attempt, so don't risk a loop
  }
  window.location.reload();
  return true;
}

type Props = { children: ReactNode; resetKey?: string };
type State = { error: Error | null };

/** Catches a page that fails to load or draw, so the screen never goes blank. Moving to another page resets it. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (isChunkError(error) && reloadForUpdate()) return;
    console.error("Page failed", error, info.componentStack);
  }

  componentDidUpdate(previous: Props) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const update = isChunkError(error);
    return (
      <div className="page-error" role="alert">
        <h2>{update ? t("This page couldn't load") : t("Something went wrong on this page")}</h2>
        <p className="muted">
          {update
            ? t("datawazi may have just been updated, or the connection dropped. Reloading usually fixes it.")
            : t("Your data is safe. Reload the page, or go back to Home and try again.")}
        </p>
        <div className="actions">
          <button onClick={() => window.location.reload()}>{t("Reload")}</button>
          <a className="button secondary" href="/">{t("Go to Home")}</a>
        </div>
      </div>
    );
  }
}
