import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const httpMock = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/api/client", () => ({ http: httpMock }));

import { usePresence } from "@/composables/usePresence";

describe("composables/usePresence", () => {
  beforeEach(() => {
    httpMock.post.mockReset();
    httpMock.post.mockResolvedValue({ data: { statuses: {} } });
    vi.useFakeTimers();
  });

  afterEach(() => {
    // The module-level heartbeat timer survives across tests — always stop it.
    usePresence().stopHeartbeat();
    vi.useRealTimers();
  });

  it("startHeartbeat sends an initial heartbeat then pings on interval", () => {
    const p = usePresence();
    p.startHeartbeat();
    expect(httpMock.post).toHaveBeenCalledWith("/presence/heartbeat");
    expect(httpMock.post).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_000);
    expect(httpMock.post).toHaveBeenCalledTimes(2);
  });

  it("startHeartbeat is idempotent while running", () => {
    const p = usePresence();
    p.startHeartbeat();
    p.startHeartbeat();
    expect(httpMock.post).toHaveBeenCalledTimes(1);
  });

  it("stopHeartbeat clears the timer", () => {
    const p = usePresence();
    p.startHeartbeat();
    p.stopHeartbeat();
    vi.advanceTimersByTime(90_000);
    expect(httpMock.post).toHaveBeenCalledTimes(1);
  });

  it("sendHeartbeat swallows failures", async () => {
    httpMock.post.mockRejectedValue(new Error("down"));
    const p = usePresence();
    p.startHeartbeat();
    await vi.waitFor(() => expect(httpMock.post).toHaveBeenCalled());
  });

  it("queryPresence returns empty for no user ids", async () => {
    const p = usePresence();
    await expect(p.queryPresence([])).resolves.toEqual({});
    expect(httpMock.post).not.toHaveBeenCalled();
  });

  it("queryPresence posts and merges statuses", async () => {
    httpMock.post.mockResolvedValue({ data: { statuses: { u1: "online" } } });
    const p = usePresence();
    const res = await p.queryPresence(["u1"]);
    expect(httpMock.post).toHaveBeenCalledWith("/presence/query", { user_ids: ["u1"] });
    expect(res).toEqual({ u1: "online" });
    expect(p.statuses.value).toEqual({ u1: "online" });
  });

  it("queryPresence returns empty on failure", async () => {
    httpMock.post.mockRejectedValue(new Error("down"));
    const p = usePresence();
    await expect(p.queryPresence(["u1"])).resolves.toEqual({});
  });

  it("getStatus falls back to offline", async () => {
    const p = usePresence();
    expect(p.getStatus("ghost")).toBe("offline");
    await p.queryPresence(["ghost"]);
    expect(p.getStatus("ghost")).toBe("offline");
    httpMock.post.mockResolvedValue({ data: { statuses: { u2: "online" } } });
    await p.queryPresence(["u2"]);
    expect(p.getStatus("u2")).toBe("online");
  });
});
