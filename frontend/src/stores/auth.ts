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
  async function bootstrap() {
    // Access token is in memory only — after page reload it's null.
    // Try to restore from refresh token first.
    if (!tokenStore.access) {
      if (!tokenStore.refresh) {
        ready.value = true;
        return;
      }
      const restored = await tokenStore.restore();
      if (!restored) {
        ready.value = true;
        return;
      }
    }
    try {
      user.value = await authApi.me();
      startMediaTicket();
    } catch (e) {
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
