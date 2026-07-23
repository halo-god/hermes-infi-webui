import { http } from "@/api/client";

export interface ProfileProposal {
  id: string;
  profile_id: string;
  proposed_prompt: string;
  rationale: string | null;
  eval_score_before: number;
  eval_score_after: number;
  diff_ratio: number;
  dataset_summary: Record<string, unknown>;
  status: "pending" | "approved" | "rejected";
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
}

export const profileEvolutionApi = {
  async triggerEvolve(profileId: string): Promise<void> {
    await http.post(`/profile-evolution/profiles/${profileId}/evolve`, {}, { validateStatus: () => true });
  },
  async evolveStatus(profileId: string): Promise<{ status: string; detail?: string | null; finished_at?: string | null }> {
    return (await http.get(`/profile-evolution/profiles/${profileId}/evolve/status`)).data;
  },
  async listProposals(params?: { status?: string; profile_id?: string }): Promise<ProfileProposal[]> {
    return (await http.get<ProfileProposal[]>("/profile-evolution/proposals", { params })).data;
  },
  async reviewProposal(id: string, status: "approved" | "rejected", reviewNote?: string): Promise<ProfileProposal> {
    return (await http.patch<ProfileProposal>(`/profile-evolution/proposals/${id}`, { status, review_note: reviewNote ?? null })).data;
  },
};
