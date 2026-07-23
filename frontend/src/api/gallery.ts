import { http } from "./client";

export interface GalleryItem {
  id: string;
  type: "profile" | "sop" | "knowledge" | "tool";
  item_id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  category: string | null;
  published_by_name: string | null;
  published_at: string | null;
  download_count: number;
  snapshot: Record<string, unknown>;
}

export interface GalleryPublish {
  type: "profile" | "sop" | "knowledge" | "tool";
  item_id: string;
  name: string;
  description?: string;
  icon?: string;
  color?: string;
  category?: string | null;
  snapshot?: Record<string, unknown>;
}

export const galleryApi = {
  async list(type?: string): Promise<GalleryItem[]> {
    return (await http.get<GalleryItem[]>("/gallery", { params: type ? { type } : {} })).data;
  },
  async publish(data: GalleryPublish): Promise<GalleryItem> {
    return (await http.post<GalleryItem>("/gallery", data)).data;
  },
  async remove(id: string): Promise<void> {
    await http.delete(`/gallery/${id}`);
  },
  async copy(id: string): Promise<{ new_id?: string; type: string; name: string; message?: string; snapshot?: Record<string, unknown> }> {
    return (await http.post(`/gallery/${id}/copy`)).data;
  },
};
