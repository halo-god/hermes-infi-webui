import { http } from "./client";

export interface Subagent {
  id: string;
  parent_conversation_id: string;
  subagent_conversation_id: string;
  purpose: string;
  agent_id: string;
  profile_id: string | null;
  status: "starting" | "running" | "idle" | "waiting_input" | "done" | "error" | "stopped" | "timeout" | "interrupted";
  last_active_at: string | null;
  error_detail: string | null;
  unread_count: number;
  created_at: string;
}

export interface SubagentSpawn {
  purpose: string;
  initial_prompt: string;
  agent_id?: string;
  profile_id?: string;
}

export const subagentsApi = {
  async list(conversationId: string): Promise<Subagent[]> {
    return (await http.get<Subagent[]>(`/conversations/${conversationId}/subagents`)).data;
  },
  async spawn(conversationId: string, payload: SubagentSpawn): Promise<Subagent> {
    return (await http.post<Subagent>(`/conversations/${conversationId}/subagents`, payload)).data;
  },
  async get(conversationId: string, subagentId: string): Promise<Subagent> {
    return (await http.get<Subagent>(`/conversations/${conversationId}/subagents/${subagentId}`)).data;
  },
  async send(conversationId: string, subagentId: string, text: string): Promise<void> {
    await http.post(`/conversations/${conversationId}/subagents/${subagentId}/messages`, { text });
  },
  async markRead(conversationId: string, subagentId: string): Promise<void> {
    await http.post(`/conversations/${conversationId}/subagents/${subagentId}/read`);
  },
  async stop(conversationId: string, subagentId: string): Promise<void> {
    await http.post(`/conversations/${conversationId}/subagents/${subagentId}/stop`);
  },
};
