import { http } from "@/api/client";

export interface AdminSkill {
  id: string;
  name: string;
  description: string;
  content: string;
  owner_id: string | null;
  team_id: string | null;
  profile_id: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface EvolveStatus {
  status: "idle" | "running" | "done" | "error";
  detail?: string | null;
  finished_at?: string | null;
}

export interface DatasetExample {
  query: string;
  skill_content_snapshot: string;
  output_trace: string | null;
  label: string | null;
  source: "real" | "synthetic";
}

export interface DatasetPreview {
  skill_id: string;
  skill_name: string;
  examples: DatasetExample[];
  summary: { real_count?: number; synthetic_count?: number; earliest?: string | null; latest?: string | null };
}

export interface SkillProposal {
  id: string;
  skill_id: string;
  proposed_content: string;
  proposed_description: string | null;
  rationale: string | null;
  eval_score_before: number;
  eval_score_after: number;
  diff_ratio: number;
  dataset_summary: { real_count?: number; synthetic_count?: number; earliest?: string | null; latest?: string | null };
  status: "pending" | "approved" | "rejected";
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
}

export const skillEvolutionApi = {
  listSkills: (): Promise<AdminSkill[]> =>
    http.get("/skill-evolution/skills").then((r) => r.data),
  previewDataset: (skillId: string): Promise<DatasetPreview> =>
    http.get(`/skill-evolution/skills/${skillId}/preview-dataset`).then((r) => r.data),
  evolve: (skillId: string): Promise<{ status: string }> =>
    http.post(`/skill-evolution/skills/${skillId}/evolve`).then((r) => r.data),
  evolveStatus: (skillId: string): Promise<EvolveStatus> =>
    http.get(`/skill-evolution/skills/${skillId}/evolve/status`).then((r) => r.data),
  listProposals: (params?: { status?: string; skill_id?: string }): Promise<SkillProposal[]> =>
    http.get("/skill-evolution/proposals", { params }).then((r) => r.data),
  review: (proposalId: string, payload: { status: "approved" | "rejected"; review_note?: string }): Promise<SkillProposal> =>
    http.patch(`/skill-evolution/proposals/${proposalId}`, payload).then((r) => r.data),
};
