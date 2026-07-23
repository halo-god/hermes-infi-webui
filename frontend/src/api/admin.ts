import { http } from "./client";
import type {
  AdminStats,
  AuditEntry,
  DeptMapping,
  IdentityProvider,
  RolesMatrix,
  SystemSettings,
  User,
} from "@/types";

export interface UsageDimensionItem {
  key: string;
  tokens_in: number;
  tokens_out: number;
  count: number;
  cost: number;
}

export interface UsageDailyItem {
  date: string;
  tokens_in: number;
  tokens_out: number;
  count: number;
}

export interface UsageData {
  period: string;
  breakdown: string;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost: number;
  daily: UsageDailyItem[];
  by_dimension: UsageDimensionItem[];
}

export const adminApi = {
  async stats(): Promise<AdminStats> {
    return (await http.get<AdminStats>("/admin/stats")).data;
  },
  async roles(): Promise<RolesMatrix> {
    return (await http.get<RolesMatrix>("/admin/roles")).data;
  },
  async listUsers(q?: string): Promise<User[]> {
    return (await http.get<User[]>("/admin/users", { params: q ? { q } : {} })).data;
  },
  async createUser(payload: {
    email: string;
    name: string;
    password: string;
    role: string;
    department?: string;
  }): Promise<User> {
    return (await http.post<User>("/admin/users", payload)).data;
  },
  async updateUser(
    id: string,
    payload: { role?: string; status?: string; department?: string; is_active?: boolean },
  ): Promise<User> {
    return (await http.patch<User>(`/admin/users/${id}`, payload)).data;
  },
  async audit(params?: { action?: string; result?: string; limit?: number; date_from?: string; date_to?: string }): Promise<AuditEntry[]> {
    return (await http.get<AuditEntry[]>("/admin/audit", { params: params || {} })).data;
  },
  async getUsage(params?: { period?: string; breakdown?: string }): Promise<UsageData> {
    return (await http.get<UsageData>("/admin/usage", { params: params || {} })).data;
  },
  async getSettings(): Promise<SystemSettings> {
    return (await http.get<SystemSettings>("/admin/settings")).data;
  },
  async putSettings(data: SystemSettings["data"]): Promise<SystemSettings> {
    return (await http.put<SystemSettings>("/admin/settings", { data })).data;
  },
  // identity providers (P5)
  async identity(): Promise<IdentityProvider[]> {
    return (await http.get<IdentityProvider[]>("/admin/identity")).data;
  },
  async updateProvider(
    id: string,
    payload: { enabled?: boolean; config?: Record<string, unknown> },
  ): Promise<IdentityProvider> {
    return (await http.patch<IdentityProvider>(`/admin/identity/${id}`, payload)).data;
  },
  async mappings(pid: string, org?: string): Promise<DeptMapping[]> {
    return (await http.get<DeptMapping[]>(`/admin/identity/${pid}/mappings`, { params: org ? { org } : {} })).data;
  },
  async addMapping(pid: string, payload: Partial<DeptMapping>): Promise<DeptMapping> {
    return (await http.post<DeptMapping>(`/admin/identity/${pid}/mappings`, payload)).data;
  },
  async deleteMapping(id: string): Promise<void> {
    await http.delete(`/admin/identity/mappings/${id}`);
  },
  async testProvider(pid: string, org?: string): Promise<{ ok: boolean; message: string }> {
    return (await http.post(`/admin/identity/${pid}/test`, null, { params: org ? { org } : {} })).data;
  },
  async togglePermission(perm_id: string, role: string, granted: boolean): Promise<void> {
    await http.patch("/admin/roles/permissions", { perm_id, role, granted });
  },
};

