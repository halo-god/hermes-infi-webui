import { http } from "./client";

export interface FileItem {
  id: string;
  name: string;
  conversation_id: string;
  conversation_title: string;
  size: number | null;
  created_at: string;
}

export const filesApi = {
  async listAll(): Promise<FileItem[]> {
    return (await http.get<FileItem[]>("/files")).data;
  },
};
