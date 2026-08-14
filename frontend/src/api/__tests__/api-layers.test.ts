import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the axios instance — api modules only touch http.get/post/patch/put/delete
const httpMock = {
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
};
vi.mock("@/api/client", () => ({
  http: httpMock,
  mediaTicket: {
    ensure: vi.fn().mockResolvedValue("ticket-123"),
    current: vi.fn().mockReturnValue("ticket-123"),
    invalidate: vi.fn(),
  },
  tokenStore: {
    get: vi.fn().mockReturnValue("tok"),
    set: vi.fn(),
    clear: vi.fn(),
  },
}));

beforeEach(() => {
  Object.values(httpMock).forEach((fn) => fn.mockReset());
  httpMock.get.mockResolvedValue({ data: {} });
  httpMock.post.mockResolvedValue({ data: {} });
  httpMock.patch.mockResolvedValue({ data: {} });
  httpMock.put.mockResolvedValue({ data: {} });
  httpMock.delete.mockResolvedValue({ data: {} });
});

describe("api/agents", () => {
  it("profilesApi.list calls GET /profiles", async () => {
    const { profilesApi } = await import("@/api/agents");
    await profilesApi.list();
    expect(httpMock.get).toHaveBeenCalledWith("/profiles");
  });

  it("profilesApi.create posts to /profiles", async () => {
    const { profilesApi } = await import("@/api/agents");
    await profilesApi.create({ name: "A", handle: "a" } as never);
    expect(httpMock.post).toHaveBeenCalledWith("/profiles", { name: "A", handle: "a" });
  });

  it("profilesApi.update patches the profile", async () => {
    const { profilesApi } = await import("@/api/agents");
    await profilesApi.update("p1", { name: "B" } as never);
    expect(httpMock.patch).toHaveBeenCalledWith("/profiles/p1", { name: "B" });
  });

  it("profilesApi.remove deletes the profile", async () => {
    const { profilesApi } = await import("@/api/agents");
    await profilesApi.remove("p1");
    expect(httpMock.delete).toHaveBeenCalledWith("/profiles/p1");
  });
});

describe("api/conversations", () => {
  it("list fetches conversations", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    await conversationsApi.list();
    expect(httpMock.get).toHaveBeenCalled();
  });

  it("create posts a conversation", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    await conversationsApi.create({ title: "T", primary_agent_id: "hermes" } as never);
    expect(httpMock.post).toHaveBeenCalledWith("/conversations", expect.objectContaining({ title: "T" }));
  });

  it("sendMessage posts to the conversation messages", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    await conversationsApi.send("c1", "你好");
    expect(httpMock.post).toHaveBeenCalledWith(
      "/conversations/c1/messages",
      expect.objectContaining({ text: "你好" }),
    );
  });

  it("fileRawUrl embeds the media ticket", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    const url = conversationsApi.fileRawUrl("c1", "f1");
    expect(url).toContain("/conversations/c1/files/f1/raw?ticket=ticket-123");
  });

  it("delete removes a conversation", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    await conversationsApi.remove("c1");
    expect(httpMock.delete).toHaveBeenCalledWith("/conversations/c1");
  });

  it("reactions post to the message", async () => {
    const { conversationsApi } = await import("@/api/conversations");
    await conversationsApi.toggleReaction("c1", "m1", "👍");
    expect(httpMock.post).toHaveBeenCalledWith(
      "/conversations/c1/messages/m1/reactions",
      expect.objectContaining({ emoji: "👍" }),
    );
  });
});

describe("api/teams", () => {
  it("list fetches teams", async () => {
    const { teamsApi } = await import("@/api/teams");
    await teamsApi.list();
    expect(httpMock.get).toHaveBeenCalledWith("/teams");
  });

  it("create posts a team", async () => {
    const { teamsApi } = await import("@/api/teams");
    await teamsApi.create({ name: "团队" } as never);
    expect(httpMock.post).toHaveBeenCalledWith("/teams", { name: "团队" });
  });

  it("knowledge list fetches with team id", async () => {
    const { teamsApi } = await import("@/api/teams");
    await teamsApi.listKnowledge?.("t1");
    expect(httpMock.get).toHaveBeenCalledWith("/teams/t1/knowledge", expect.anything());
  });

  it("addMember posts to team members", async () => {
    const { teamsApi } = await import("@/api/teams");
    await teamsApi.addMember("t1", "u1", "member");
    expect(httpMock.post).toHaveBeenCalledWith(
      "/teams/t1/members",
      expect.objectContaining({ email: "u1" }),
    );
  });
});

describe("api/files", () => {
  it("listAll fetches files", async () => {
    const { filesApi } = await import("@/api/files");
    await filesApi.listAll();
    expect(httpMock.get).toHaveBeenCalledWith("/files");
  });

  it("listStandalone passes folder param", async () => {
    const { filesApi } = await import("@/api/files");
    await filesApi.listStandalone("/docs");
    expect(httpMock.get).toHaveBeenCalledWith("/files/standalone", {
      params: { folder: "/docs" },
    });
  });

  it("remove deletes a file", async () => {
    const { filesApi } = await import("@/api/files");
    await filesApi.remove("f1");
    expect(httpMock.delete).toHaveBeenCalledWith("/files/f1");
  });
});

describe("api/scheduled", () => {
  it("list fetches tasks", async () => {
    const { scheduledApi } = await import("@/api/scheduled");
    await scheduledApi.list();
    expect(httpMock.get).toHaveBeenCalledWith("/scheduled");
  });

  it("create posts a task", async () => {
    const { scheduledApi } = await import("@/api/scheduled");
    await scheduledApi.create({ name: "任务", prompt: "x", cron: "0 9 * * *" } as never);
    expect(httpMock.post).toHaveBeenCalledWith("/scheduled", expect.objectContaining({ name: "任务" }));
  });

  it("toggle posts to the task toggle endpoint", async () => {
    const { scheduledApi } = await import("@/api/scheduled");
    await scheduledApi.toggle("t1", false);
    expect(httpMock.post).toHaveBeenCalledWith(
      "/scheduled/t1/toggle", null, expect.objectContaining({ params: { enabled: false } }),
    );
  });
});

describe("api/auth", () => {
  it("login posts credentials", async () => {
    const { authApi } = await import("@/api/auth");
    await authApi.login({ method: "local", username: "u", password: "p" });
    expect(httpMock.post).toHaveBeenCalledWith("/auth/login", expect.objectContaining({ username: "u" }));
  });

  it("logout posts refresh token", async () => {
    const { authApi } = await import("@/api/auth");
    await authApi.logout("rt");
    expect(httpMock.post).toHaveBeenCalledWith("/auth/logout", expect.anything());
  });
});

describe("api/analytics + branding", () => {
  it("analytics usage fetches", async () => {
    const { analyticsApi } = await import("@/api/analytics");
    await analyticsApi.usage();
    expect(httpMock.get).toHaveBeenCalledWith("/analytics/usage");
  });

  it("branding config fetches public config", async () => {
    const { brandingApi } = await import("@/api/branding");
    await brandingApi.getBranding();
    expect(httpMock.get).toHaveBeenCalledWith("/branding");
  });
});

describe("api/admin + memory", () => {
  it("admin users list fetches", async () => {
    const { adminApi } = await import("@/api/admin");
    await adminApi.listUsers();
    expect(httpMock.get).toHaveBeenCalledWith("/admin/users", expect.anything());
  });

  it("memory skills list fetches", async () => {
    const { memoryApi } = await import("@/api/memory");
    await memoryApi.listSkills();
    expect(httpMock.get).toHaveBeenCalledWith("/memory/skills");
  });
});
