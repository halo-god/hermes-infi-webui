import { http } from "./client";
import type { Feedback } from "@/types";

export const feedbackApi = {
  async list(params?: { status?: string; category?: string; limit?: number }): Promise<Feedback[]> {
    return (await http.get<Feedback[]>("/feedback", { params })).data;
  },
  async create(payload: { title: string; content: string; category?: string }): Promise<Feedback> {
    return (await http.post<Feedback>("/feedback", payload)).data;
  },
  async get(id: number): Promise<Feedback> {
    return (await http.get<Feedback>(`/feedback/${id}`)).data;
  },
  async update(id: number, payload: { status?: string; priority?: string; reply?: string }): Promise<Feedback> {
    return (await http.patch<Feedback>(`/feedback/${id}`, payload)).data;
  },
};
