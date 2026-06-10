import { http } from "@/api/client";

export interface Memory {
  notes: string | null;
  user_profile: string | null;
  soul: string | null;
  last_consolidated_at?: string | null;
}

export interface ConsolidateStatus {
  status: "idle" | "running" | "done" | "error";
  detail?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  cooldown_remaining: number;
}

export const memoryApi = {
  get: (): Promise<Memory> => http.get("/memory").then((r) => r.data),
  update: (payload: Partial<Memory>): Promise<Memory> =>
    http.put("/memory", payload).then((r) => r.data),
  consolidate: (): Promise<{ status: string }> =>
    http.post("/memory/consolidate").then((r) => r.data),
  consolidateStatus: (): Promise<ConsolidateStatus> =>
    http.get("/memory/consolidate/status").then((r) => r.data),
};
