import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const authApiMock = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
}));
const tokenMock = vi.hoisted(() => ({
  access: null as string | null,
  refresh: null as string | null,
  _access: null as string | null,
  set: vi.fn(),
  clear: vi.fn(),
  restore: vi.fn(),
}));
const mediaMock = vi.hoisted(() => ({ ensure: vi.fn(), clear: vi.fn() }));

vi.mock("@/api/auth", () => ({ authApi: authApiMock }));
vi.mock("@/api/client", () => ({ tokenStore: tokenMock, mediaTicket: mediaMock }));

import { useAuthStore } from "@/stores/auth";
import type { User } from "@/types";

const adminUser = { id: "u1", email: "a@b.c", name: "A", role: "admin" } as unknown as User;
const normalUser = { id: "u2", email: "x@y.z", name: "X", role: "user" } as unknown as User;

function resetTokenState() {
  tokenMock.access = null;
  tokenMock.refresh = null;
  tokenMock._access = null;
  tokenMock.restore.mockReset();
  tokenMock.set.mockReset();
  tokenMock.clear.mockReset();
  mediaMock.ensure.mockReset();
  mediaMock.clear.mockReset();
  authApiMock.login.mockReset();
  authApiMock.logout.mockReset();
  authApiMock.me.mockReset();
  localStorage.clear();
}

describe("stores/auth", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    resetTokenState();
    vi.useFakeTimers();
  });

  afterEach(() => {
    useAuthStore().logout(); // stops the media-ticket interval
    vi.useRealTimers();
  });

  it("login stores tokens, sets user and starts the media ticket", async () => {
    authApiMock.login.mockResolvedValue({
      access_token: "acc",
      refresh_token: "ref",
      user: adminUser,
    });
    const auth = useAuthStore();
    const user = await auth.login({ method: "local", username: "a@b.c", password: "pw" });
    expect(user).toEqual(adminUser);
    expect(tokenMock.set).toHaveBeenCalledWith("acc", "ref");
    expect(auth.user).toEqual(adminUser);
    expect(auth.isAuthenticated).toBe(true);
    expect(auth.isAdmin).toBe(true);
    expect(mediaMock.ensure).toHaveBeenCalled();
  });

  it("isAdmin is false for plain users", () => {
    const auth = useAuthStore();
    auth.user = normalUser;
    expect(auth.isAdmin).toBe(false);
    auth.user = { ...normalUser, role: "super_admin" };
    expect(auth.isAdmin).toBe(true);
  });

  it("bootstrap with an access token calls me() and starts the ticket", async () => {
    tokenMock.access = "acc";
    authApiMock.me.mockResolvedValue(normalUser);
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.user).toEqual(normalUser);
    expect(auth.ready).toBe(true);
    expect(mediaMock.ensure).toHaveBeenCalled();
  });

  it("bootstrap is single-flight across concurrent callers", async () => {
    tokenMock.access = "acc";
    let resolveMe!: (u: User) => void;
    authApiMock.me.mockImplementation(() => new Promise((r) => { resolveMe = r; }));
    const auth = useAuthStore();
    const p1 = auth.bootstrap();
    const p2 = auth.bootstrap();
    expect(authApiMock.me).toHaveBeenCalledTimes(1);
    resolveMe(normalUser);
    await Promise.all([p1, p2]);
    expect(auth.user).toEqual(normalUser);
  });

  it("bootstrap without any token just marks ready", async () => {
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.ready).toBe(true);
    expect(auth.user).toBeNull();
    expect(authApiMock.me).not.toHaveBeenCalled();
  });

  it("bootstrap restores via refresh token when it succeeds", async () => {
    tokenMock.refresh = "ref";
    tokenMock.restore.mockResolvedValue(true);
    authApiMock.me.mockResolvedValue(normalUser);
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.user).toEqual(normalUser);
    expect(tokenMock.restore).toHaveBeenCalled();
  });

  it("bootstrap gives up when the refresh token fails", async () => {
    tokenMock.refresh = "ref";
    tokenMock.restore.mockResolvedValue(false);
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.ready).toBe(true);
    expect(auth.user).toBeNull();
    expect(tokenMock.clear).not.toHaveBeenCalled();
  });

  it("bootstrap uses an injected access token from localStorage", async () => {
    localStorage.setItem("hermes.access", "injected");
    authApiMock.me.mockResolvedValue(normalUser);
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.user).toEqual(normalUser);
    expect(tokenMock._access).toBe("injected");
  });

  it("bootstrap falls back to refresh when injected access is stale", async () => {
    localStorage.setItem("hermes.access", "stale");
    tokenMock.refresh = "ref";
    tokenMock.restore.mockResolvedValue(true);
    authApiMock.me.mockRejectedValueOnce(new Error("401"));
    authApiMock.me.mockResolvedValueOnce(normalUser);
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(tokenMock.restore).toHaveBeenCalled();
    expect(auth.user).toEqual(normalUser);
  });

  it("bootstrap clears everything when all restoration paths fail", async () => {
    localStorage.setItem("hermes.access", "stale");
    tokenMock.refresh = "ref";
    tokenMock.restore.mockResolvedValue(true);
    authApiMock.me.mockRejectedValue(new Error("401"));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.user).toBeNull();
    expect(auth.ready).toBe(true);
    expect(tokenMock.clear).toHaveBeenCalled();
  });

  it("logout calls the api and clears the session even on failure", async () => {
    authApiMock.logout.mockRejectedValue(new Error("down"));
    const auth = useAuthStore();
    auth.user = adminUser;
    tokenMock.refresh = "ref";
    await auth.logout();
    expect(authApiMock.logout).toHaveBeenCalledWith("ref");
    expect(tokenMock.clear).toHaveBeenCalled();
    expect(auth.user).toBeNull();
    expect(auth.isAuthenticated).toBe(false);
  });
});
