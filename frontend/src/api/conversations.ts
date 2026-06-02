import { http } from "./client";
import { tokenStore } from "./client";
import type {
  Conversation,
  ConversationDetail,
  Message,
  WorkspaceFile,
  WorkspaceFileVersion,
} from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

interface SendResponse {
  user_message: Message;
  agent_message: Message;
}

export const conversationsApi = {
  async list(params?: { q?: string; pinned?: boolean }): Promise<Conversation[]> {
    return (await http.get<Conversation[]>("/conversations", { params: params || {} })).data;
  },
  async bulkDelete(ids: string[]): Promise<number> {
    return (await http.post<{ deleted: number }>("/conversations/bulk-delete", { ids })).data.deleted;
  },
  async create(payload: {
    primary_agent_id?: string;
    title?: string;
    first_message?: string;
    team_id?: string;
    project_id?: string;
    profile_id?: string;
  }): Promise<ConversationDetail> {
    return (await http.post<ConversationDetail>("/conversations", payload)).data;
  },
  async get(id: string): Promise<ConversationDetail> {
    return (await http.get<ConversationDetail>(`/conversations/${id}`)).data;
  },
  async update(id: string, payload: { title?: string; pinned?: boolean }): Promise<Conversation> {
    return (await http.patch<Conversation>(`/conversations/${id}`, payload)).data;
  },
  async setAgents(id: string, agentIds: string[]): Promise<Conversation> {
    return (await http.put<Conversation>(`/conversations/${id}/agents`, { agent_ids: agentIds })).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/conversations/${id}`);
  },
  async share(id: string): Promise<{ share_url: string; conversation_id: string }> {
    return (await http.post(`/conversations/${id}/share`)).data;
  },
  async unshare(id: string): Promise<void> {
    await http.patch(`/conversations/${id}`, { visibility: "private" });
  },
  async send(id: string, text: string, opts?: { profileId?: string; webSearch?: boolean; deepThink?: boolean; fileIds?: string[] }): Promise<SendResponse> {
    const { fileIds, ...restOpts } = opts || {};
    return (await http.post<SendResponse>(`/conversations/${id}/messages`, {
      text,
      ...restOpts,
      attached_file_ids: fileIds || [],
    })).data;
  },
  async cancel(id: string): Promise<void> {
    await http.post(`/conversations/${id}/cancel`);
  },
  async files(id: string): Promise<WorkspaceFile[]> {
    return (await http.get<WorkspaceFile[]>(`/conversations/${id}/files`)).data;
  },
  async fileContent(id: string, fileId: string): Promise<WorkspaceFile & { content: string }> {
    return (await http.get(`/conversations/${id}/files/${fileId}`)).data;
  },
  fileRawUrl(id: string, fileId: string): string {
    const token = tokenStore.access || "";
    return `${API_BASE}/conversations/${id}/files/${fileId}/raw?access_token=${encodeURIComponent(token)}`;
  },
  async patchFile(id: string, fileId: string, content: string): Promise<WorkspaceFile & { content: string }> {
    return (await http.patch(`/conversations/${id}/files/${fileId}`, { content })).data;
  },
  async fileVersions(id: string, fileId: string): Promise<WorkspaceFileVersion[]> {
    return (await http.get(`/conversations/${id}/files/${fileId}/versions`)).data;
  },
  async restoreVersion(id: string, fileId: string, versionNum: number): Promise<WorkspaceFile & { content: string }> {
    return (await http.post(`/conversations/${id}/files/${fileId}/restore/${versionNum}`)).data;
  },
  async confirm(id: string, requestId: string, choice: string): Promise<void> {
    await http.post(`/conversations/${id}/confirm`, { request_id: requestId, choice });
  },
  async upload(id: string, file: File): Promise<WorkspaceFile> {
    const form = new FormData();
    form.append("file", file);
    return (await http.post(`/conversations/${id}/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    })).data;
  },
  async extractItems(id: string): Promise<{ project_name: string; tasks: string[]; conversation_id: string; team_id: string | null }> {
    return (await http.post(`/conversations/${id}/extract-items`)).data;
  },
};
