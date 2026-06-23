import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export const ACCESS_KEY = "hermes.access";
export const REFRESH_KEY = "hermes.refresh";

/**
 * Token store with XSS protection:
 * - Access token: stored in memory only (JS variables are not accessible to XSS)
 * - Refresh token: stored in localStorage (needed for session persistence across page reloads)
 *
 * On page reload, call tokenStore.restore() to get a new access token using the refresh token.
 */
export const tokenStore = {
  _access: null as string | null,

  get access() {
    return this._access;
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(access: string, refresh: string) {
    this._access = access;
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    this._access = null;
    localStorage.removeItem(REFRESH_KEY);
  },

  /**
   * Restore access token from refresh token on page reload.
   * Returns true if restoration succeeded.
   * Uses a single-flight pattern to prevent concurrent refresh attempts.
   */
  _restoreInflight: null as Promise<boolean> | null,

  async restore(): Promise<boolean> {
    // Single-flight: prevent concurrent refresh attempts
    if (this._restoreInflight) {
      return this._restoreInflight;
    }

    this._restoreInflight = (async () => {
      const refresh = localStorage.getItem(REFRESH_KEY);
      if (!refresh) return false;
      try {
        const { data } = await axios.post(`${API_BASE}/auth/refresh`, {
          refresh_token: refresh,
        });
        this._access = data.access_token;
        localStorage.setItem(REFRESH_KEY, data.refresh_token);
        return true;
      } catch (e) {
        console.error("[auth] token refresh failed:", e);
        this.clear();
        return false;
      } finally {
        this._restoreInflight = null;
      }
    })();

    return this._restoreInflight;
  },
};

export const http = axios.create({ baseURL: API_BASE, timeout: 20000 });

/**
 * Media tickets for SSE / WebSocket / file-raw URLs.
 *
 * EventSource, WebSocket and <img>/<a> can't send an Authorization header, so
 * we never put the API access token in a URL. Instead we mint a short-lived,
 * user-scoped, opaque ticket and embed THAT — a leaked URL then exposes only a
 * few minutes of media access, never the API-capable token. `ensure()` is
 * single-flight and refreshes before expiry; `current()` is the sync accessor
 * for raw URLs (primed/refreshed by the auth store).
 */
let _ticket: string | null = null;
let _ticketExp = 0;
let _ticketInflight: Promise<string> | null = null;

export const mediaTicket = {
  current(): string {
    return _ticket ?? "";
  },
  clear() {
    _ticket = null;
    _ticketExp = 0;
    _ticketInflight = null;
  },
  async ensure(): Promise<string> {
    // Refresh well before the 5-min TTL so the cached value stays valid for the
    // synchronous raw-URL accessors between auth-store refresh ticks.
    if (_ticket && _ticketExp - Date.now() > 120_000) return _ticket;
    if (!_ticketInflight) {
      _ticketInflight = (async () => {
        try {
          const { data } = await http.post<{ ticket: string; expires_in: number }>(
            "/auth/stream-ticket",
          );
          _ticket = data.ticket;
          _ticketExp = Date.now() + data.expires_in * 1000;
          return _ticket;
        } catch (e) {
          console.error("[media] failed to mint ticket:", e);
          throw e; // Re-throw so SSE can retry
        } finally {
          _ticketInflight = null;
        }
      })();
    }
    return _ticketInflight;
  },
};

// Attach access token.
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const t = tokenStore.access;
  if (t) config.headers.set("Authorization", `Bearer ${t}`);
  return config;
});

// Refresh-on-401 with a single in-flight refresh promise.
let refreshing: Promise<string | null> | null = null;

async function doRefresh(): Promise<string | null> {
  const refresh = tokenStore.refresh;
  if (!refresh) return null;
  try {
    const { data } = await axios.post(`${API_BASE}/auth/refresh`, {
      refresh_token: refresh,
    });
    tokenStore.set(data.access_token, data.refresh_token);
    return data.access_token as string;
  } catch (e) {
    console.error("[auth] token refresh failed:", e);
    tokenStore.clear();
    return null;
  }
}

// Global error handler — shows toast for user-facing errors.
let _showError: ((msg: string) => void) | null = null;

function getShowError() {
  if (!_showError) {
    // Use window-level event to communicate with notification store
    // This avoids circular dependency issues
    _showError = (msg: string) => {
      window.dispatchEvent(new CustomEvent("hermes:api-error", { detail: msg }));
    };
  }
  return _showError;
}

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError) => {
    const original = error.config as AxiosRequestConfig & { _retried?: boolean };
    const status = error.response?.status;
    const isAuthCall = original?.url?.includes("/auth/");
    // Skip error toast for SSE/streaming endpoints (they handle errors via events)
    const isStreamCall = original?.url?.includes("/stream") || original?.url?.includes("/ws");

    if (status === 401 && original && !original._retried && !isAuthCall) {
      original._retried = true;
      refreshing = refreshing ?? doRefresh();
      const newToken = await refreshing;
      refreshing = null;
      if (newToken) {
        // Use AxiosHeaders.set() — direct property assignment doesn't work
        // because config.headers is an AxiosHeaders instance, not a plain object.
        const headers = original.headers ?? ({} as Record<string, string>);
        if (typeof (headers as { set?: unknown }).set === "function") {
          (headers as { set: (k: string, v: string) => void }).set("Authorization", `Bearer ${newToken}`);
        } else {
          (headers as Record<string, string>).Authorization = `Bearer ${newToken}`;
          original.headers = headers;
        }
        return http(original);
      }
      // Refresh failed → bounce to login.
      window.dispatchEvent(new CustomEvent("hermes:logout"));
    }

    // Show user-friendly error toast for non-retryable errors
    if (!isAuthCall && !isStreamCall && status && status >= 400) {
      const showError = getShowError();
      const detail = (error.response?.data as Record<string, unknown>)?.detail;
      const msg = typeof detail === "string" ? detail : `请求失败 (${status})`;
      showError(msg);
    }

    return Promise.reject(error);
  },
);
