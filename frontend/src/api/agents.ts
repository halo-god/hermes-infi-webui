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
}

export interface ScanResult {
  created: number;
  message: string;
  version: string;
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
}

export const agentsApi = {
  async list(): Promise<Agent[]> {
    return (await http.get<Agent[]>("/agents")).data;
  },
  async profiles(): Promise<Profile[]> {
    return (await http.get<Profile[]>("/profiles")).data;
  },
  async createProfile(data: ProfileCreate): Promise<Profile> {
    return (await http.post<Profile>("/profiles", data)).data;
  },
  async updateProfile(id: string, data: ProfileUpdate): Promise<Profile> {
    return (await http.patch<Profile>(`/profiles/${id}`, data)).data;
  },
  async deleteProfile(id: string): Promise<void> {
    await http.delete(`/profiles/${id}`);
  },
  async scanProfiles(): Promise<ScanResult> {
    return (await http.post("/profiles/scan")).data;
  },
  async cloneProfile(id: string): Promise<Profile> {
    return (await http.post<Profile>(`/profiles/${id}/clone`)).data;
  },
  async exportProfile(id: string): Promise<Record<string, string>> {
    return (await http.get(`/profiles/${id}/export`)).data;
  },
  async importProfiles(profiles: Record<string, string>[]): Promise<Profile[]> {
    return (await http.post<Profile[]>("/profiles/import", { profiles })).data;
  },
};
