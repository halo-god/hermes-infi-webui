import { request as pwRequest, type APIRequestContext } from "@playwright/test";

/**
 * Pre-run data cleanup: deletes leftovers from previous E2E runs (teams and
 * scheduled tasks named "E2E …") so `.first()` selectors never hit stale
 * duplicate rows. Deleting a team cascades to its group conversations.
 *
 * Must run AFTER the admin login (needs an access token).
 */
export async function cleanupE2EData(apiUrl: string, token: string): Promise<void> {
  const ctx = await pwRequest.newContext({ baseURL: apiUrl });
  const auth = { Authorization: `Bearer ${token}` };
  try {
    await cleanupTeams(ctx, auth);
    await cleanupScheduledTasks(ctx, auth);
  } finally {
    await ctx.dispose();
  }
}

async function cleanupTeams(ctx: APIRequestContext, auth: Record<string, string>) {
  const res = await ctx.get("/api/v1/teams", { headers: auth });
  if (res.status() !== 200) return; // endpoint unavailable — don't fail the run
  const teams = (await res.json()) as { id: string; name: string }[];
  for (const t of teams.filter((t) => t.name?.startsWith("E2E "))) {
    await ctx.delete(`/api/v1/teams/${t.id}`, { headers: auth }).catch(() => {});
  }
}

async function cleanupScheduledTasks(ctx: APIRequestContext, auth: Record<string, string>) {
  const res = await ctx.get("/api/v1/scheduled", { headers: auth });
  if (res.status() !== 200) return;
  const tasks = (await res.json()) as { id: string; name: string }[];
  for (const t of tasks.filter((t) => t.name?.startsWith("E2E "))) {
    await ctx.delete(`/api/v1/scheduled/${t.id}`, { headers: auth }).catch(() => {});
  }
}
