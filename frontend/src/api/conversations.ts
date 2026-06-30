import { http } from "./client";
import { mediaTicket } from "./client";
import type {
  Conversation,
  ConversationDetail,
  ConversationFolder,
  GroupMember,
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
  async list(params?: { q?: string; pinned?: boolean; limit?: number; offset?: number }): Promise<Conversation[]> {
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
  async update(id: string, payload: { title?: string; pinned?: boolean; channel_mode?: string; folder_id?: string | null }): Promise<Conversation> {
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
  async send(id: string, text: string, opts?: { profileId?: string; fileIds?: string[]; skipAgent?: boolean; taskId?: string }): Promise<SendResponse> {
    const { fileIds, skipAgent, profileId, taskId, ...restOpts } = opts || {};
    return (await http.post<SendResponse>(`/conversations/${id}/messages`, {
      text,
      ...restOpts,
      profile_id: profileId || null,
      attached_file_ids: fileIds || [],
      skip_agent: skipAgent || false,
      task_id: taskId || null,
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
    return `${API_BASE}/conversations/${id}/files/${fileId}/raw?ticket=${encodeURIComponent(mediaTicket.current())}`;
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
  async detectTasks(id: string): Promise<{ transcript: string; prompt: string; agent_id: string }> {
    return (await http.post(`/conversations/${id}/detect-tasks`)).data;
  },
  async consolidateMessage(
    conversationId: string,
    messageId: string,
    data: { target: "project_doc" | "team_knowledge"; name: string; project_id?: string; team_id?: string },
  ): Promise<{ id: string; name: string; target: string }> {
    return (
      await http.post(`/conversations/${conversationId}/messages/${messageId}/consolidate`, data)
    ).data;
  },
  async getMessages(id: string, params?: { limit?: number; before?: string }): Promise<Message[]> {
    return (await http.get<Message[]>(`/conversations/${id}/messages`, { params: params || {} })).data;
  },
  async fork(id: string, beforeMessageId: string): Promise<ConversationDetail> {
    return (await http.post<ConversationDetail>(`/conversations/${id}/fork?before_message_id=${beforeMessageId}`)).data;
  },
  async forkSession(id: string): Promise<ConversationDetail> {
    return (await http.post<ConversationDetail>(`/conversations/${id}/session/fork`)).data;
  },
  async setSessionMode(id: string, mode: string): Promise<void> {
    await http.put(`/conversations/${id}/session/mode`, { mode });
  },
  async setSessionModel(id: string, modelId: string): Promise<void> {
    await http.put(`/conversations/${id}/session/model`, { model_id: modelId });
  },

  // ── Conversation folders (grouping) ──
  async listFolders(): Promise<ConversationFolder[]> {
    return (await http.get<ConversationFolder[]>("/conversations/folders")).data;
  },
  async createFolder(name: string): Promise<ConversationFolder> {
    return (await http.post<ConversationFolder>("/conversations/folders", { name })).data;
  },
  async updateFolder(id: string, payload: { name?: string; sort_order?: number; pinned?: boolean }): Promise<ConversationFolder> {
    return (await http.patch<ConversationFolder>(`/conversations/folders/${id}`, payload)).data;
  },
  async deleteFolder(id: string): Promise<void> {
    await http.delete(`/conversations/folders/${id}`);
  },
  async reorderFolders(items: { id: string; sort_order: number }[]): Promise<void> {
    await http.put("/conversations/folders/reorder", { items });
  },

  // ── Group chat ──
  async createGroup(title: string, memberUserIds: string[], memberAgentIds: string[], teamId?: string): Promise<Conversation & { members: GroupMember[] }> {
    return (await http.post(`/conversations/group`, {
      title,
      member_user_ids: memberUserIds,
      member_agent_ids: memberAgentIds,
      team_id: teamId || null,
    })).data;
  },
  async listGroups(): Promise<Conversation[]> {
    return (await http.get(`/conversations/groups`)).data;
  },
  async getMembers(id: string): Promise<GroupMember[]> {
    return (await http.get(`/conversations/${id}/members`)).data;
  },
  async addMember(id: string, userId?: string, agentId?: string): Promise<void> {
    await http.post(`/conversations/${id}/members`, {
      user_id: userId || null,
      agent_id: agentId || null,
    });
  },
  async removeMember(id: string, memberId: string): Promise<void> {
    await http.delete(`/conversations/${id}/members/${memberId}`);
  },
  async sendWithMentions(id: string, text: string, mentions: string[], fileIds?: string[], profileId?: string): Promise<SendResponse> {
    return (await http.post<SendResponse>(`/conversations/${id}/messages`, {
      text,
      mentions,
      attached_file_ids: fileIds || [],
      profile_id: profileId || null,
    })).data;
  },

  // ── Read state · message edit / recall / reactions ──
  async read(id: string): Promise<{ ok: boolean; last_read_at: string | null }> {
    return (await http.post(`/conversations/${id}/read`)).data;
  },
  async editMessage(id: string, messageId: string, text: string): Promise<Message> {
    return (await http.patch(`/conversations/${id}/messages/${messageId}`, { text })).data;
  },
  async recallMessage(id: string, messageId: string): Promise<Message> {
    return (await http.delete(`/conversations/${id}/messages/${messageId}`)).data;
  },
  async toggleReaction(id: string, messageId: string, emoji: string): Promise<Message> {
    return (await http.post(`/conversations/${id}/messages/${messageId}/reactions`, { emoji })).data;
  },
};
