import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { brandingApi } from "@/api/branding";
import type { BrandingPublic } from "@/types";

/**
 * Default branding — mirrors the backend `DEFAULT_SETTINGS.branding` so the
 * first paint (before /branding resolves) looks correct and never flashes
 * empty/undefined. Overwritten once the public endpoint responds.
 */
const DEFAULT_BRANDING: BrandingPublic = {
  tenant_name: "Hermes Internal",
  display: "Hermes — 信使",
  short_name: "Hermes",
  login_tagline: "凡所欲遣，皆可托信使。",
  login_subtitle: "连接你的 Hermes 助手，开始协作。",
  accent: "#b8852a",
  favicon_url: null,
  logo_url: null,
};

/** Mix a hex color toward white (amt>0 lighter) or black (amt<0 darker). */
function shade(hex: string, amt: number): string {
  const m = /^#?([\da-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const clamp = (v: number) => Math.max(0, Math.min(255, v));
  const r = clamp(((n >> 16) & 255) + Math.round(255 * amt));
  const g = clamp(((n >> 8) & 255) + Math.round(255 * amt));
  const b = clamp((n & 255) + Math.round(255 * amt));
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

const FAVICON_LINK_ID = "site-favicon";

function applyFavicon(url: string | null) {
  let link = document.getElementById(FAVICON_LINK_ID) as HTMLLinkElement | null;
  if (!url) {
    if (link) link.remove();
    return;
  }
  if (!link) {
    link = document.createElement("link");
    link.id = FAVICON_LINK_ID;
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = url;
}

function applyAccent(accent: string) {
  const root = document.documentElement;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-deep", shade(accent, -0.18));
  // Lighter tints for soft backgrounds.
  root.style.setProperty("--accent-soft", shade(accent, 0.78));
  root.style.setProperty("--accent-tint", shade(accent, 0.9));
}

function applyTitle(display: string) {
  document.title = display;
}

export const useBrandingStore = defineStore("branding", () => {
  const branding = ref<BrandingPublic>({ ...DEFAULT_BRANDING });
  const loaded = ref(false);

  const tenantName = computed(() => branding.value.tenant_name);
  const display = computed(() => branding.value.display);
  const shortName = computed(() => branding.value.short_name);
  const tagline = computed(() => branding.value.login_tagline);
  const loginSubtitle = computed(() => branding.value.login_subtitle);
  const accent = computed(() => branding.value.accent);
  const faviconUrl = computed(() => branding.value.favicon_url);
  const logoUrl = computed(() => branding.value.logo_url);

  /** Derived Naive UI primary color triplet, reactive on accent. */
  const accentOverrides = computed(() => ({
    primaryColor: accent.value,
    primaryColorHover: shade(accent.value, 0.18),
    primaryColorPressed: shade(accent.value, -0.18),
    primaryColorSuppl: shade(accent.value, 0.18),
  }));

  function _applyAll(b: BrandingPublic) {
    applyTitle(b.display);
    applyAccent(b.accent);
    applyFavicon(b.favicon_url);
  }

  /** Fetch public branding and apply side-effects (title/favicon/accent). */
  async function fetchBranding(): Promise<void> {
    try {
      const b = await brandingApi.getBranding();
      branding.value = b;
      _applyAll(b);
    } catch (e) {
      console.error("[branding] fetch failed:", e);
      // Keep defaults applied.
      _applyAll(branding.value);
    } finally {
      loaded.value = true;
    }
  }

  // Apply defaults immediately so the very first paint is correct.
  _applyAll(branding.value);

  return {
    branding,
    loaded,
    tenantName,
    display,
    shortName,
    tagline,
    loginSubtitle,
    accent,
    faviconUrl,
    logoUrl,
    accentOverrides,
    fetchBranding,
  };
});
