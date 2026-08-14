import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const brandingApiMock = vi.hoisted(() => ({ getBranding: vi.fn() }));
vi.mock("@/api/branding", () => ({ brandingApi: brandingApiMock }));

import { useBrandingStore } from "@/stores/branding";
import type { BrandingPublic } from "@/types";

const custom: BrandingPublic = {
  tenant_name: "Acme",
  display: "Acme 信使",
  short_name: "Acme",
  login_tagline: "你好",
  login_subtitle: "欢迎",
  accent: "#123456",
  favicon_url: "/favicon.ico",
  logo_url: "/logo.png",
};

describe("stores/branding", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    brandingApiMock.getBranding.mockReset();
  });

  it("starts with the default branding applied", () => {
    const b = useBrandingStore();
    expect(b.branding.tenant_name).toBe("Hermes Internal");
    expect(b.display).toBe("Hermes — 信使");
    expect(b.loaded).toBe(false);
    expect(document.title).toBe("Hermes — 信使");
  });

  it("exposes derived getters", () => {
    const b = useBrandingStore();
    expect(b.shortName).toBe("Hermes");
    expect(b.tagline).toContain("凡所欲遣");
    expect(b.loginSubtitle).toContain("连接你的 Hermes");
    expect(b.accent).toBe("#b8852a");
    expect(b.faviconUrl).toBeNull();
    expect(b.logoUrl).toBeNull();
  });

  it("accentOverrides derives lighter/pressed shades reactively", () => {
    const b = useBrandingStore();
    const before = b.accentOverrides;
    expect(before.primaryColor).toBe("#b8852a");
    expect(before.primaryColorHover).not.toBe(before.primaryColor);
    expect(before.primaryColorHover).toMatch(/^#[\da-f]{6}$/);
    b.branding = { ...b.branding, accent: "#000000" };
    expect(b.accentOverrides.primaryColor).toBe("#000000");
    expect(b.accentOverrides.primaryColorHover).toMatch(/^#[0-5][\da-f]{5}$/);
  });

  it("fetchBranding applies the fetched branding as CSS vars + title + favicon", async () => {
    brandingApiMock.getBranding.mockResolvedValue(custom);
    const b = useBrandingStore();
    await b.fetchBranding();
    expect(b.branding).toEqual(custom);
    expect(b.loaded).toBe(true);
    expect(document.title).toBe("Acme 信使");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#123456");
    expect(document.documentElement.style.getPropertyValue("--accent-deep")).toMatch(/^#/);
    const link = document.getElementById("site-favicon") as HTMLLinkElement | null;
    expect(link?.href).toContain("/favicon.ico");
  });

  it("fetchBranding failure keeps the defaults applied", async () => {
    brandingApiMock.getBranding.mockRejectedValue(new Error("down"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const b = useBrandingStore();
    await b.fetchBranding();
    expect(consoleSpy).toHaveBeenCalled();
    expect(b.branding.tenant_name).toBe("Hermes Internal");
    expect(b.loaded).toBe(true);
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#b8852a");
    consoleSpy.mockRestore();
  });

  it("fetchBranding with null favicon removes the favicon link", async () => {
    brandingApiMock.getBranding.mockResolvedValue({ ...custom, favicon_url: null });
    const b = useBrandingStore();
    await b.fetchBranding();
    expect(document.getElementById("site-favicon")).toBeNull();
  });
});
