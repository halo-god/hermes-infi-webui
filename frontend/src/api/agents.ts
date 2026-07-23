import { http } from "./client";
import type { Agent } from "@/types";

export interface Profile {
  id: string;
  name: string;
  handle: string;
  scope: "personal" | "team" | "global";
  color: string;
  icon: string;
  desc: string;
  default_agent_id: string;
  default_model: string;
  team_id: string | null;
  is_active: boolean;
  path: string | null;
  system_prompt?: string | null;
  skills?: string[];
  featured?: boolean;
  knowledge_ids?: string[];
  knowledge_folder_ids?: string[];
  knowledge_team_ids?: string[];
  mcp_server_names?: string[];
  is_moa?: boolean;
  moa_target_profile_ids?: string[];
  // Digital Employee HR attributes
  employee_no?: string | null;
  department?: string | null;
  position?: string | null;
  employee_status?: string;
  hired_at?: string | null;
}

export interface WorkRecord {
  id: string;
  profile_id: string;
  conversation_id: string | null;
  message_id: string | null;
  event_type: string;
  summary: string;
  tokens_used: number;
  duration_ms: number | null;
  feedback: string | null;
  created_at: string;
}

export interface Performance {
  total_messages: number;
  total_tokens: number;
  positive_count: number;
  negative_count: number;
  avg_duration_ms: number;
  daily: { date: string; count: number; tokens: number }[];
}

export interface ScanResult {
  found: number;
  created: number;
  updated: number;
  agents: Agent[];
  version: string | null;
  hermes_path: string | null;
  errors: string[];
}

export interface ScanProfilesResult {
  created: number;
  message: string;
  version: string | null;
  profiles_found: number;
  hermes_path: string | null;
  hermes_home: string | null;
  errors: string[];
}

export interface ProfileCreate {
  name: string;
  handle: string;
  scope?: string;
  color?: string;
  icon?: string;
  desc?: string;
  default_agent_id?: string;
  default_model?: string;
  team_id?: string | null;
  system_prompt?: string | null;
  skills?: string[];
  featured?: boolean;
  knowledge_ids?: string[];
  knowledge_folder_ids?: string[];
  knowledge_team_ids?: string[];
  employee_no?: string | null;
  department?: string | null;
  position?: string | null;
  employee_status?: string;
  hired_at?: string | null;
}

export interface ProfileUpdate {
  name?: string;
  handle?: string;
  scope?: string;
  color?: string;
  icon?: string;
  desc?: string;
  default_agent_id?: string;
  default_model?: string;
  team_id?: string | null;
  is_active?: boolean;
  system_prompt?: string | null;
  skills?: string[];
  featured?: boolean;
  knowledge_ids?: string[];
  knowledge_folder_ids?: string[];
  knowledge_team_ids?: string[];
  mcp_server_names?: string[];
  is_moa?: boolean;
  moa_target_profile_ids?: string[];
  employee_no?: string | null;
  department?: string | null;
  position?: string | null;
  employee_status?: string;
  hired_at?: string | null;
}

export const agentsApi = {
  async list(): Promise<Agent[]> {
    return (await http.get<Agent[]>("/agents")).data;
  },
  async scanAgents(): Promise<ScanResult> {
    return (await http.post("/agents/scan")).data;
  },
};

export const profilesApi = {
  async list(): Promise<Profile[]> {
    return (await http.get<Profile[]>("/profiles")).data;
  },
  async create(data: ProfileCreate): Promise<Profile> {
    return (await http.post<Profile>("/profiles", data)).data;
  },
  async update(id: string, data: ProfileUpdate): Promise<Profile> {
    return (await http.patch<Profile>(`/profiles/${id}`, data)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/profiles/${id}`);
  },
  async scan(): Promise<ScanProfilesResult> {
    return (await http.post("/profiles/scan")).data;
  },
  async clone(id: string): Promise<Profile> {
    return (await http.post<Profile>(`/profiles/${id}/clone`)).data;
  },
  async export(id: string): Promise<Record<string, string>> {
    return (await http.get(`/profiles/${id}/export`)).data;
  },
  async import(profiles: Record<string, string>[]): Promise<Profile[]> {
    return (await http.post<Profile[]>("/profiles/import", { profiles })).data;
  },
  async getWorkRecords(id: string, limit = 20): Promise<WorkRecord[]> {
    return (await http.get<WorkRecord[]>(`/profiles/${id}/work-records`, { params: { limit } })).data;
  },
  async getPerformance(id: string): Promise<Performance> {
    return (await http.get<Performance>(`/profiles/${id}/performance`)).data;
  },
};
