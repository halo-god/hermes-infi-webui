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

export interface MemoryEpisode {
  id: string;
  conversation_id: string | null;
  title: string;
  summary: string;
  consolidated_at: string;
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  trigger_conditions: { keywords?: string[]; always?: boolean };
  content: string;
  enabled: boolean;
  profile_id: string | null;
}

export interface SkillCreate {
  name: string;
  description?: string;
  content: string;
  trigger_conditions?: { keywords?: string[]; always?: boolean };
  profile_id?: string | null;
  enabled?: boolean;
}

export interface SkillUpdate {
  name?: string;
  description?: string;
  content?: string;
  trigger_conditions?: { keywords?: string[]; always?: boolean };
  profile_id?: string | null;
  enabled?: boolean;
}

export const memoryApi = {
  get: (): Promise<Memory> => http.get("/memory").then((r) => r.data),
  update: (payload: Partial<Memory>): Promise<Memory> =>
    http.put("/memory", payload).then((r) => r.data),
  consolidate: (): Promise<{ status: string }> =>
    http.post("/memory/consolidate").then((r) => r.data),
  consolidateStatus: (): Promise<ConsolidateStatus> =>
    http.get("/memory/consolidate/status").then((r) => r.data),
  episodes: (q = ""): Promise<MemoryEpisode[]> =>
    http.get("/memory/episodes", { params: q ? { q } : {} }).then((r) => r.data),
  listSkills: (): Promise<Skill[]> => http.get("/memory/skills").then((r) => r.data),
  createSkill: (payload: SkillCreate): Promise<Skill> =>
    http.post("/memory/skills", payload).then((r) => r.data),
  updateSkill: (id: string, payload: SkillUpdate): Promise<Skill> =>
    http.patch(`/memory/skills/${id}`, payload).then((r) => r.data),
  deleteSkill: (id: string): Promise<void> => http.delete(`/memory/skills/${id}`).then(() => undefined),
};
