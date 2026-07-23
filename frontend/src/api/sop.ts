import { http } from "./client";

export interface SopNode {
  node_id: string;
  name: string;
  instruction: string;
  expected_user_info: string[];
  allowed_actions: string[];
  knowledge_scope?: Record<string, unknown>;
}

export interface SopEdge {
  source_node_id: string;
  next_node_id: string;
  condition: string | null;
  priority: number;
  label: string;
}

export interface SopSkill {
  id: string;
  name: string;
  description: string;
  profile_id: string | null;
  trigger_intents: string[];
  nodes: SopNode[];
  edges: SopEdge[];
  start_node_id: string;
  terminal_node_ids: string[];
  enabled: boolean;
  created_at: string | null;
}

export interface SopSkillCreate {
  name: string;
  description?: string;
  profile_id?: string | null;
  trigger_intents?: string[];
  nodes?: SopNode[];
  edges?: SopEdge[];
  start_node_id: string;
  terminal_node_ids?: string[];
}

export interface SopSkillUpdate {
  name?: string;
  description?: string;
  trigger_intents?: string[];
  nodes?: SopNode[];
  edges?: SopEdge[];
  start_node_id?: string;
  terminal_node_ids?: string[];
  enabled?: boolean;
}

export const sopApi = {
  async list(profileId?: string): Promise<SopSkill[]> {
    return (await http.get<SopSkill[]>("/sop-skills", { params: profileId ? { profile_id: profileId } : {} })).data;
  },
  async create(data: SopSkillCreate): Promise<{ id: string; name: string }> {
    return (await http.post("/sop-skills", data)).data;
  },
  async update(id: string, data: SopSkillUpdate): Promise<{ id: string }> {
    return (await http.put(`/sop-skills/${id}`, data)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/sop-skills/${id}`);
  },
};
