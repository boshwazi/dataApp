/**
 * What a workspace's first steps have done that the server doesn't record: whether someone has asked a
 * question, and whether the next-steps list was hidden. Kept by the browser, per workspace.
 */

type Flag = "asked" | "hidden";

function key(workspace: string | null | undefined, flag: Flag) {
  return `datawazi.onboarding.${workspace ?? "none"}.${flag}`;
}

export function onboardingFlag(workspace: string | null | undefined, flag: Flag): boolean {
  try {
    return localStorage.getItem(key(workspace, flag)) === "1";
  } catch {
    return false;
  }
}

export function setOnboardingFlag(workspace: string | null | undefined, flag: Flag) {
  try {
    localStorage.setItem(key(workspace, flag), "1");
  } catch {
    /* private windows may refuse; the list just shows again */
  }
}
