import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { authApi, type LoginPayload } from "@/api/auth";
import { mediaTicket, tokenStore } from "@/api/client";
import type { User } from "@/types";

// Keep a live media ticket so SSE/WS/raw-file URLs never carry the access token.
// Refreshed under the 5-min TTL so the synchronous raw-URL accessors stay valid.
let _ticketTimer: ReturnType<typeof setInterval> | null = null;
function startMediaTicket() {
  void mediaTicket.ensure();
  if (_ticketTimer) return;
  _ticketTimer = setInterval(() => void mediaTicket.ensure(), 120_000);
}
function stopMediaTicket() {
  if (_ticketTimer) { clearInterval(_ticketTimer); _ticketTimer = null; }
  mediaTicket.clear();
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const ready = ref(false); // initial session check completed

  const isAuthenticated = computed(() => !!user.value);
  const isAdmin = computed(
    () => user.value?.role === "super_admin" || user.value?.role === "admin",
  );

  async function login(payload: LoginPayload) {
    const res = await authApi.login(payload);
    tokenStore.set(res.access_token, res.refresh_token);
    user.value = res.user;
    startMediaTicket();
    return res.user;
  }

  /** Restore session on app boot (page refresh). */
  let _bootstrapPromise: Promise<void> | null = null;
  async function bootstrap() {
    // Single-flight: the router guard and App onMounted can both call this
    // during first paint; concurrent runs would race on me()/restore() and
    // could end up clearing the session one just restored.
    if (_bootstrapPromise) return _bootstrapPromise;
    _bootstrapPromise = _doBootstrap();
    return _bootstrapPromise;
  }

  async function _doBootstrap() {
    // Access token is in memory only — after page reload it's null.
    // Try to restore from refresh token first.
    let usedInjected = false;
    if (!tokenStore.access) {
      // E2E storageState injects the access token under ACCESS_KEY (the normal
      // product flow never writes it to localStorage — memory only). Older
      // builds DID persist it, so a stale value may linger here; treat it as
      // a hint and fall back to the refresh token if it fails validation.
      const injectedAccess = localStorage.getItem("hermes.access");
      if (injectedAccess) {
        tokenStore._access = injectedAccess;
        usedInjected = true;
      } else if (tokenStore.refresh) {
        const restored = await tokenStore.restore();
        if (!restored) {
          // Refresh token failed - user needs to login again
          ready.value = true;
          return;
        }
      } else {
        ready.value = true;
        return;
      }
    }
    try {
      user.value = await authApi.me();
      // Start media ticket AFTER successful auth - it needs valid access token
      startMediaTicket();
    } catch (e) {
      // Stale injected access (from an older build) failed — retry via the
      // refresh token before giving up; a valid refresh keeps the session.
      if (usedInjected && tokenStore.refresh) {
        const restored = await tokenStore.restore();
        if (restored) {
          try {
            user.value = await authApi.me();
            startMediaTicket();
            ready.value = true;
            return;
          } catch {
            /* fall through to clear */
          }
        }
      }
      console.error("[auth] bootstrap failed:", e);
      tokenStore.clear();
      user.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function logout() {
    try {
      await authApi.logout(tokenStore.refresh);
    } catch (e) {
      console.error("[auth] logout failed:", e);
    }
    stopMediaTicket();
    tokenStore.clear();
    user.value = null;
  }

  return { user, ready, isAuthenticated, isAdmin, login, bootstrap, logout };
});
