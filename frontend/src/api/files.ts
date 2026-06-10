import { http } from "./client";
import { tokenStore } from "./client";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export interface FileItem {
  id: string;
  name: string;
  conversation_id: string | null;
  conversation_title: string | null;
  size: number | null;
  created_at: string;
  source: string;  // "upload" or "ai" or "folder"
  kind?: string | null;
  folder_path?: string;
  is_folder?: boolean;
}

export const filesApi = {
  async listAll(): Promise<FileItem[]> {
    return (await http.get<FileItem[]>("/files")).data;
  },
  async listStandalone(folder = "/"): Promise<FileItem[]> {
    return (await http.get<FileItem[]>("/files/standalone", { params: { folder } })).data;
  },
  async upload(file: File, folder = "/"): Promise<FileItem> {
    const form = new FormData();
    form.append("file", file);
    return (await http.post<FileItem>("/files/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      params: { folder },
    })).data;
  },
  async createFolder(name: string, parent = "/"): Promise<{ id: string; name: string; kind: string; folder_path: string; is_folder: boolean }> {
    return (await http.post("/files/folder", null, { params: { name, parent } })).data;
  },
  async remove(fileId: string): Promise<void> {
    await http.delete(`/files/${fileId}`);
  },
  async content(fileId: string): Promise<{ id: string; name: string; kind: string; content: string | null; size: number | null }> {
    return (await http.get(`/files/${fileId}/content`)).data;
  },
  rawUrl(fileId: string): string {
    const token = tokenStore.access || "";
    return `${API_BASE}/files/${fileId}/raw?access_token=${encodeURIComponent(token)}`;
  },
};
