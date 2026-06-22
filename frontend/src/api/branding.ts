import { http } from "./client";
import type { BrandAssetOut, BrandingPublic } from "@/types";

export const brandingApi = {
  /** Public (no auth) — drives login page, boot screen, title, favicon, accent. */
  async getBranding(): Promise<BrandingPublic> {
    return (await http.get<BrandingPublic>("/branding")).data;
  },
  /** Admin: upload/replace the favicon or logo binary. */
  async uploadAsset(kind: "favicon" | "logo", file: File): Promise<BrandAssetOut> {
    const form = new FormData();
    form.append("kind", kind);
    form.append("file", file);
    return (await http.post<BrandAssetOut>("/admin/settings/asset", form)).data;
  },
  /** Admin: remove the favicon or logo binary. */
  async deleteAsset(kind: "favicon" | "logo"): Promise<void> {
    await http.delete(`/admin/settings/asset/${kind}`);
  },
};
