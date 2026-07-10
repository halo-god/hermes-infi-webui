import { http, mediaTicket } from "./client";
import type { Project, ProjectActivity, ProjectDetail, ProjectDoc, Task, WorkspaceFileVersion } from "@/types";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export const projectsApi = {
  async listByTeam(teamId: string): Promise<Project[]> {
    return (await http.get<Project[]>(`/teams/${teamId}/projects`)).data;
  },
  async create(
    teamId: string,
    data: {
      name: string;
      handle?: string;
      color?: string;
      icon?: string;
      summary?: string;
      sections?: string[];
      pinned_agents?: string[];
      deadline?: string;
    },
  ): Promise<Project> {
    return (await http.post<Project>(`/teams/${teamId}/projects`, data)).data;
  },
  async get(id: string): Promise<Project & ProjectDetail> {
    return (await http.get<Project & ProjectDetail>(`/projects/${id}`)).data;
  },
  async update(id: string, data: Partial<Project>): Promise<Project> {
    return (await http.patch<Project>(`/projects/${id}`, data)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/projects/${id}`);
  },
  async setMembers(id: string, userIds: string[]): Promise<Project> {
    return (await http.put<Project>(`/projects/${id}/members`, { user_ids: userIds })).data;
  },
  async tasks(projectId: string): Promise<Task[]> {
    return (await http.get<Task[]>(`/projects/${projectId}/tasks`)).data;
  },
  async createTask(
    projectId: string,
    data: { title: string; owner_id?: string; agent_id?: string },
  ): Promise<Task> {
    return (await http.post<Task>(`/projects/${projectId}/tasks`, data)).data;
  },
  async updateTask(taskId: string, data: Partial<Task>): Promise<Task> {
    return (await http.patch<Task>(`/tasks/${taskId}`, data)).data;
  },
  async moveTaskStatus(taskId: string, status: string): Promise<Task> {
    return (await http.patch<Task>(`/tasks/${taskId}/status`, { status })).data;
  },
  async executeTask(taskId: string, profileId: string): Promise<{ status: string; task_id: string; profile: string }> {
    return (await http.post(`/tasks/${taskId}/execute`, { profile_id: profileId })).data;
  },
  async tasksFromConversation(projectId: string, messageId: string): Promise<Task[]> {
    return (
      await http.post<Task[]>(`/projects/${projectId}/tasks/from-conversation`, {
        message_id: messageId,
      })
    ).data;
  },
  async activity(projectId: string): Promise<ProjectActivity[]> {
    return (await http.get<ProjectActivity[]>(`/projects/${projectId}/activity`)).data;
  },
  async deleteTask(taskId: string): Promise<void> {
    await http.delete(`/tasks/${taskId}`);
  },
  async reorderTasks(projectId: string, items: { id: string; order_idx: number; status?: string }[]): Promise<void> {
    await http.put(`/projects/${projectId}/tasks/reorder`, { items });
  },
  async docs(projectId: string): Promise<ProjectDoc[]> {
    return (await http.get<ProjectDoc[]>(`/projects/${projectId}/docs`)).data;
  },
  async addDoc(projectId: string, data: { name: string; kind?: string; size_bytes?: number }): Promise<ProjectDoc> {
    return (await http.post<ProjectDoc>(`/projects/${projectId}/docs`, data)).data;
  },
  async deleteDoc(docId: string): Promise<void> {
    await http.delete(`/projects/docs/${docId}`);
  },
  async uploadDoc(projectId: string, file: File): Promise<ProjectDoc> {
    const form = new FormData();
    form.append("file", file);
    return (await http.post<ProjectDoc>(`/projects/${projectId}/docs/upload`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    })).data;
  },
  async docContent(docId: string): Promise<string> {
    const r = await http.get<{ content: string | null }>(`/projects/docs/${docId}`);
    return r.data.content || "";
  },
  docRawUrl(docId: string): string {
    return `${API_BASE}/projects/docs/${docId}/raw?ticket=${encodeURIComponent(mediaTicket.current())}`;
  },
  async updateDocContent(docId: string, content: string): Promise<string> {
    const r = await http.patch<{ content: string | null }>(`/projects/docs/${docId}`, { content });
    return r.data.content || "";
  },
  async docVersions(docId: string): Promise<WorkspaceFileVersion[]> {
    return (await http.get(`/projects/docs/${docId}/versions`)).data;
  },
  async restoreDocVersion(docId: string, versionNum: number): Promise<string> {
    const r = await http.post<{ content: string | null }>(`/projects/docs/${docId}/restore/${versionNum}`);
    return r.data.content || "";
  },
};
