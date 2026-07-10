import { http } from "./client";
import type { LoginMethod, LoginResponse, ProviderInfo, User } from "@/types";

export interface LoginPayload {
  method: LoginMethod;
  username?: string;
  password?: string;
  remember_device?: boolean;
}

export const authApi = {
  async login(payload: LoginPayload): Promise<LoginResponse> {
    const { data } = await http.post<LoginResponse>("/auth/login", payload);
    return data;
  },
  async me(): Promise<User> {
    const { data } = await http.get<User>("/auth/me");
    return data;
  },
  async providers(): Promise<ProviderInfo[]> {
    const { data } = await http.get<ProviderInfo[]>("/auth/providers");
    return data;
  },
  async logout(refresh_token: string | null): Promise<void> {
    await http.post("/auth/logout", { refresh_token });
  },
  async wecomOrgs(): Promise<{ orgs: { id: string; name: string }[] }> {
    const { data } = await http.get<{ orgs: { id: string; name: string }[] }>("/auth/wecom/orgs");
    return data;
  },
  async wecomAuthorize(org?: string): Promise<{ authorize_url: string }> {
    const { data } = await http.get<{ authorize_url: string }>("/auth/wecom/authorize", { params: org ? { org } : {} });
    return data;
  },
  async wecomExchange(code: string): Promise<{ access_token: string; refresh_token: string }> {
    const { data } = await http.post<{ access_token: string; refresh_token: string }>("/auth/wecom/exchange", { code });
    return data;
  },
  async changePassword(current_password: string, new_password: string): Promise<{ access_token: string; refresh_token: string }> {
    const { data } = await http.post<{ access_token: string; refresh_token: string }>("/auth/change-password", { current_password, new_password });
    return data;
  },
};
