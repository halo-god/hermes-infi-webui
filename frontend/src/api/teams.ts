import { http, mediaTicket } from "./client";
import type { Knowledge, Member, Team, TeamDetail, TeamPolicy, WorkspaceFileVersion } from "@/types";

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
  async setSharedProfiles(id: string, profileIds: string[]): Promise<TeamDetail> {
    return (await http.put<TeamDetail>(`/teams/${id}/shared-profiles`, { profile_ids: profileIds })).data;
  },
  async listKnowledge(id: string, folderId?: string, recursive?: boolean): Promise<Knowledge[]> {
    const params: Record<string, string> = {};
    if (folderId) params.folder_id = folderId;
    if (recursive) params.recursive = "true";
    return (await http.get<Knowledge[]>(`/teams/${id}/knowledge`, { params })).data;
  },
  async createKnowledgeFolder(id: string, name: string, folderId?: string): Promise<Knowledge> {
    return (await http.post<Knowledge>(`/teams/${id}/knowledge/folder`, { name, folder_id: folderId || null })).data;
  },
  async moveKnowledge(id: string, kid: string, folderId: string | null): Promise<void> {
    await http.patch(`/teams/${id}/knowledge/${kid}/move`, { folder_id: folderId });
  },
  async clearChannel(id: string): Promise<void> {
    await http.delete(`/teams/${id}/channel/messages`);
  },
  async addKnowledge(id: string, data: { name: string; kind?: string; size_bytes?: number; folder_id?: string | null }): Promise<Knowledge> {
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
    return `${API_BASE}/teams/${id}/knowledge/${kid}/raw?ticket=${encodeURIComponent(mediaTicket.current())}`;
  },
  async updateKnowledgeContent(id: string, kid: string, content: string): Promise<string> {
    const r = await http.patch<{ content: string | null }>(`/teams/${id}/knowledge/${kid}`, { content });
    return r.data.content || "";
  },
  async knowledgeVersions(id: string, kid: string): Promise<WorkspaceFileVersion[]> {
    return (await http.get(`/teams/${id}/knowledge/${kid}/versions`)).data;
  },
  async knowledgeChunksCount(id: string, kid: string): Promise<{ count: number; rag_enabled: boolean }> {
    return (await http.get(`/teams/${id}/knowledge/${kid}/chunks-count`)).data;
  },
  async restoreKnowledgeVersion(id: string, kid: string, versionNum: number): Promise<string> {
    const r = await http.post<{ content: string | null }>(`/teams/${id}/knowledge/${kid}/restore/${versionNum}`);
    return r.data.content || "";
  },
  async uploadKnowledge(id: string, file: File, folderId?: string | null, onProgress?: (pct: number) => void): Promise<void> {
    const fd = new FormData();
    fd.append("file", file);
    if (folderId) fd.append("folder_id", folderId);
    await http.post(`/teams/${id}/knowledge/upload`, fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 600000,  // 10 min — Docling processing large PDFs/PPTXs can take minutes
      onUploadProgress: onProgress ? (ev: { loaded?: number; total?: number }) => {
        if (ev.total) onProgress(Math.round((ev.loaded || 0) / ev.total * 100));
      } : undefined,
    });
  },
  async getChannel(id: string): Promise<{ channel: import("@/types").Conversation; channel_mode: string }> {
    return (await http.get(`/teams/${id}/channel`)).data;
  },
  async getProjectGroup(projectId: string): Promise<{ channel: import("@/types").Conversation; channel_mode: string }> {
    return (await http.get(`/projects/${projectId}/group`)).data;
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
