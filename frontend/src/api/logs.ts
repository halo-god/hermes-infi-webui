import { http } from "./client";

export interface LogEntry {
  timestamp: string;
  level: string;
  request_id: string;
  logger: string;
  message: string;
}

export const logsApi = {
  async getLogs(params?: { level?: string; keyword?: string; limit?: number }): Promise<{ entries: LogEntry[]; total: number }> {
    return (await http.get("/logs", { params })).data;
  },
};
