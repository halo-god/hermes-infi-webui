import { http } from "./client";

export interface FileItem {
  id: string;
  name: string;
  conversation_id: string;
  conversation_title: string;
  size: number | null;
  created_at: string;
  source: string;  // "upload" or "ai"
}

export const filesApi = {
  async listAll(): Promise<FileItem[]> {
    return (await http.get<FileItem[]>("/files")).data;
  },
  async listStandalone(): Promise<FileItem[]> {
    return (await http.get<FileItem[]>("/files/standalone")).data;
  },
  async upload(file: File): Promise<FileItem> {
    const form = new FormData();
    form.append("file", file);
    return (await http.post<FileItem>("/files/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })).data;
  },
  async remove(fileId: string): Promise<void> {
    await http.delete(`/files/${fileId}`);
  },
  async content(fileId: string): Promise<{ id: string; name: string; kind: string; content: string | null; size: number | null }> {
    return (await http.get(`/files/${fileId}/content`)).data;
  },
};
