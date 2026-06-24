import { http } from "./client";
import type { ScheduledTask } from "@/types";

export const scheduledApi = {
  async list(): Promise<ScheduledTask[]> {
    return (await http.get<ScheduledTask[]>("/scheduled")).data;
  },
  async create(payload: { name: string; agent_id: string; prompt: string; cron: string; enabled?: boolean }): Promise<ScheduledTask> {
    return (await http.post<ScheduledTask>("/scheduled", payload)).data;
  },
  async update(id: string, payload: Partial<{ name: string; agent_id: string; prompt: string; cron: string; enabled: boolean }>): Promise<ScheduledTask> {
    return (await http.patch<ScheduledTask>(`/scheduled/${id}`, payload)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/scheduled/${id}`);
  },
  async toggle(id: string, enabled: boolean): Promise<ScheduledTask> {
    return (await http.post<ScheduledTask>(`/scheduled/${id}/toggle`, null, { params: { enabled } })).data;
  },
};
