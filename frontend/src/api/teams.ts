import { http, tokenStore } from "./client";
import type { Knowledge, Member, Team, TeamDetail, TeamPolicy } from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export const teamsApi = {
  async list(): Promise<Team[]> {
    return (await http.get<Team[]>("/teams")).data;
  },
  async create(data: { name: string; handle?: string; tagline?: string; color?: string }): Promise<TeamDetail> {
    return (await http.post<TeamDetail>("/teams", data)).data;
  },
  async get(id: string): Promise<TeamDetail> {
    return (await http.get<TeamDetail>(`/teams/${id}`)).data;
  },
  async update(id: string, data: { name?: string; tagline?: string; color?: string }): Promise<Team> {
    return (await http.patch<Team>(`/teams/${id}`, data)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/teams/${id}`);
  },
  async members(id: string): Promise<Member[]> {
    return (await http.get<Member[]>(`/teams/${id}/members`)).data;
  },
  async addMember(id: string, email: string, role = "member"): Promise<Member> {
    return (await http.post<Member>(`/teams/${id}/members`, { email, role })).data;
  },
  async updateMember(id: string, memberId: string, role: string): Promise<Member> {
    return (await http.patch<Member>(`/teams/${id}/members/${memberId}`, { role })).data;
  },
  async removeMember(id: string, memberId: string): Promise<void> {
    await http.delete(`/teams/${id}/members/${memberId}`);
  },
  async policy(id: string): Promise<TeamPolicy> {
    return (await http.get<TeamPolicy>(`/teams/${id}/policy`)).data;
  },
  async updatePolicy(id: string, policy: Record<string, Record<string, boolean>>): Promise<TeamPolicy> {
    return (await http.put<TeamPolicy>(`/teams/${id}/policy`, { policy })).data;
  },
  async setSharedAgents(id: string, agentIds: string[]): Promise<TeamDetail> {
    return (await http.put<TeamDetail>(`/teams/${id}/shared-agents`, { agent_ids: agentIds })).data;
  },
  async addKnowledge(id: string, data: { name: string; kind?: string; size_bytes?: number }): Promise<Knowledge> {
    return (await http.post<Knowledge>(`/teams/${id}/knowledge`, data)).data;
  },
  async deleteKnowledge(id: string, kid: string): Promise<void> {
    await http.delete(`/teams/${id}/knowledge/${kid}`);
  },
  async updateKnowledge(id: string, kid: string, data: { name?: string; kind?: string; size_bytes?: number }): Promise<Knowledge> {
    return (await http.patch<Knowledge>(`/teams/${id}/knowledge/${kid}`, data)).data;
  },
  async knowledgeContent(id: string, kid: string): Promise<string> {
    const r = await http.get<{ content: string | null }>(`/teams/${id}/knowledge/${kid}`);
    return r.data.content || "";
  },
  knowledgeRawUrl(id: string, kid: string): string {
    const token = tokenStore.access || "";
    return `${API_BASE}/teams/${id}/knowledge/${kid}/raw?access_token=${encodeURIComponent(token)}`;
  },
  async updateKnowledgeContent(id: string, kid: string, content: string): Promise<string> {
    const r = await http.patch<{ content: string | null }>(`/teams/${id}/knowledge/${kid}`, { content });
    return r.data.content || "";
  },
  async uploadKnowledge(id: string, file: File): Promise<void> {
    const fd = new FormData();
    fd.append("file", file);
    await http.post(`/teams/${id}/knowledge/upload`, fd, { headers: { "Content-Type": "multipart/form-data" } });
  },
  async getChannel(id: string): Promise<{ channel: import("@/types").Conversation; channel_mode: string }> {
    return (await http.get(`/teams/${id}/channel`)).data;
  },
  async setChannelMode(id: string, channel_mode: string): Promise<{ channel_mode: string }> {
    return (await http.patch(`/teams/${id}/channel/mode`, { channel_mode })).data;
  },
  async generateInviteToken(id: string, role: string, expiresDays: number): Promise<{ token: string; url: string; role: string }> {
    return (await http.post(`/teams/${id}/invite-token`, { role, expires_days: expiresDays })).data;
  },
  async joinByToken(token: string): Promise<{ team_id: string; role: string; joined: boolean; message: string }> {
    return (await http.post(`/teams/join-by-token`, { token })).data;
  },
};
