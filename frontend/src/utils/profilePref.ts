// Remember the last-used profile per browser — replaces the silent "first
// profile wins" fallback so multi-profile users get their actual assistant
// back on the next conversation.
const KEY = "hermes:default-profile";

export function rememberProfile(id: string | null): void {
  if (id) {
    localStorage.setItem(KEY, id);
  } else {
    localStorage.removeItem(KEY);
  }
}

export function storedProfileId(): string | null {
  return localStorage.getItem(KEY);
}

/** Pick the default profile: remembered one when it still exists, else first. */
export function pickDefaultProfile<T extends { id: string }>(profiles: T[]): T | null {
  if (!profiles.length) return null;
  const remembered = storedProfileId();
  if (remembered) {
    const hit = profiles.find((p) => p.id === remembered);
    if (hit) return hit;
  }
  return profiles[0] || null;
}
