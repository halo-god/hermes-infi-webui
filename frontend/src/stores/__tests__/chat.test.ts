import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import type { Conversation, ConversationDetail, ConversationFolder, Message, WorkspaceFile } from "@/types";
import type { Profile } from "@/api/agents";

const convApiMock = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  get: vi.fn(),
  update: vi.fn(),
  setAgents: vi.fn(),
  remove: vi.fn(),
  send: vi.fn(),
  cancel: vi.fn(),
  confirm: vi.fn(),
  read: vi.fn(),
  files: vi.fn(),
  upload: vi.fn(),
  getMessages: vi.fn(),
  listFolders: vi.fn(),
  createFolder: vi.fn(),
  updateFolder: vi.fn(),
  reorderFolders: vi.fn(),
  deleteFolder: vi.fn(),
}));
const profilesApiMock = vi.hoisted(() => ({ list: vi.fn() }));
const teamsApiMock = vi.hoisted(() => ({ list: vi.fn() }));
const clientMock = vi.hoisted(() => ({
  mediaTicket: { ensure: vi.fn() },
  tokenStore: {
    access: null as string | null,
    refresh: null as string | null,
    _access: null as string | null,
    set: vi.fn(),
    clear: vi.fn(),
    restore: vi.fn(),
  },
}));
const nsMock = vi.hoisted(() => ({ push: vi.fn(), toast: vi.fn() }));
const streamStub = vi.hoisted(() => ({
  connected: { value: false },
  error: { value: null },
  openSSE: vi.fn(),
  openWS: vi.fn(),
  close: vi.fn(),
  send: vi.fn(),
  on: vi.fn(),
  offAll: vi.fn(),
  emit: vi.fn(),
}));
const registerMock = vi.hoisted(() => vi.fn());

vi.mock("@/api/conversations", () => ({ conversationsApi: convApiMock }));
vi.mock("@/api/agents", () => ({ profilesApi: profilesApiMock }));
vi.mock("@/api/teams", () => ({ teamsApi: teamsApiMock }));
vi.mock("@/api/client", () => clientMock);
vi.mock("@/stores/notifications", () => ({ useNotificationStore: () => nsMock }));
vi.mock("@/composables/useStream", () => ({ useStream: () => streamStub }));
vi.mock("@/stores/chatStream", () => ({ registerStreamHandlers: registerMock }));

import { useChatStore } from "@/stores/chat";

const hermesProfile = { id: "p-hermes", name: "Hermes", handle: "hermes", default_agent_id: "hermes" } as Profile;

function makeDetail(over: Partial<ConversationDetail> = {}): ConversationDetail {
  return {
    id: "c1",
    title: "T",
    type: "personal",
    messages: [],
    active_agent_ids: ["hermes"],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...over,
  } as ConversationDetail;
}

function makeMessage(over: Partial<Message> = {}): Message {
  return {
    id: "m1",
    conversation_id: "c1",
    owner_id: null,
    role: "agent",
    agent_id: "hermes",
    profile_id: null,
    content: { text: "hi" },
    status: "complete",
    created_at: new Date().toISOString(),
    ...over,
  } as Message;
}

function resetMocks() {
  Object.values(convApiMock).forEach((f) => (f as ReturnType<typeof vi.fn>).mockReset());
  Object.values(profilesApiMock).forEach((f) => (f as ReturnType<typeof vi.fn>).mockReset());
  Object.values(teamsApiMock).forEach((f) => (f as ReturnType<typeof vi.fn>).mockReset());
  Object.values(streamStub).forEach((m) => {
    if (typeof m === "object" && m && "value" in m) m.value = null;
    else if (typeof m === "function") m.mockReset();
  });
  registerMock.mockReset();
  registerMock.mockReturnValue(() => {});
  clientMock.mediaTicket.ensure.mockReset();
  clientMock.mediaTicket.ensure.mockResolvedValue("ticket");
  clientMock.tokenStore.access = null;
  clientMock.tokenStore.refresh = null;
  clientMock.tokenStore.restore.mockReset();
  clientMock.tokenStore.restore.mockResolvedValue(true);
  nsMock.push.mockReset();
  nsMock.toast.mockReset();
  convApiMock.list.mockResolvedValue([]);
  convApiMock.files.mockResolvedValue([]);
  convApiMock.listFolders.mockResolvedValue([]);
  streamStub.openSSE.mockResolvedValue(undefined);
  streamStub.openWS.mockResolvedValue(undefined);
}

describe("stores/chat", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    resetMocks();
  });

  describe("loading lists", () => {
    it("loadTeams populates teams and clears on failure", async () => {
      const store = useChatStore();
      teamsApiMock.list.mockResolvedValue([{ id: "t1", name: "团队" }]);
      await store.loadTeams();
      expect(store.teams).toEqual([{ id: "t1", name: "团队" }]);
      teamsApiMock.list.mockRejectedValue(new Error("down"));
      await store.loadTeams();
      expect(store.teams).toEqual([]);
    });

    it("loadProfiles populates and syncs active profiles", async () => {
      const store = useChatStore();
      profilesApiMock.list.mockResolvedValue([hermesProfile]);
      await store.loadProfiles();
      expect(store.profiles).toEqual([hermesProfile]);
      expect(store.profileByAgentId("hermes")?.id).toBe("p-hermes");
    });

    it("loadConversations sets hasMore by page size", async () => {
      const store = useChatStore();
      convApiMock.list.mockResolvedValue(Array.from({ length: 100 }, (_, i) => ({ id: `c${i}` })));
      await store.loadConversations();
      expect(store.hasMoreConversations).toBe(true);
      convApiMock.list.mockResolvedValue([{ id: "c1" }]);
      await store.loadConversations();
      expect(store.hasMoreConversations).toBe(false);
    });

    it("loadMoreConversations appends and dedupes by id", async () => {
      const store = useChatStore();
      store.conversations = [{ id: "c1" } as Conversation];
      store.hasMoreConversations = true;
      convApiMock.list.mockResolvedValue([{ id: "c1" }, { id: "c2" }]);
      await store.loadMoreConversations();
      expect(store.conversations.map((c) => c.id)).toEqual(["c1", "c2"]);
    });

    it("loadMoreConversations is guarded by loading flag", async () => {
      const store = useChatStore();
      store.loadingMoreConvos = true;
      await store.loadMoreConversations();
      expect(convApiMock.list).not.toHaveBeenCalled();
    });

    it("loadFolders populates personal and group folders", async () => {
      const store = useChatStore();
      convApiMock.listFolders.mockResolvedValueOnce([{ id: "f1" }]);
      convApiMock.listFolders.mockResolvedValueOnce([{ id: "g1" }]);
      await store.loadFolders();
      expect(convApiMock.listFolders).toHaveBeenCalledWith("personal");
      expect(convApiMock.listFolders).toHaveBeenCalledWith("group");
      expect(store.folders).toEqual([{ id: "f1" }]);
      expect(store.groupFolders).toEqual([{ id: "g1" }]);
    });
  });

  describe("folders", () => {
    it("createFolder pushes into the matching list", async () => {
      const store = useChatStore();
      convApiMock.createFolder.mockResolvedValue({ id: "f1", name: "工作" });
      await store.createFolder("工作", "personal");
      expect(store.folders).toEqual([expect.objectContaining({ id: "f1" })]);
      convApiMock.createFolder.mockResolvedValue({ id: "g1", name: "群" });
      await store.createFolder("群", "group");
      expect(store.groupFolders).toEqual([expect.objectContaining({ id: "g1" })]);
    });

    it("renameFolder updates both lists", async () => {
      const store = useChatStore();
      store.folders = [{ id: "f1", name: "旧" } as ConversationFolder];
      convApiMock.updateFolder.mockResolvedValue({ id: "f1", name: "新" });
      await store.renameFolder("f1", "新");
      expect(store.folders[0].name).toBe("新");
    });

    it("toggleFolderPinned is optimistic and reverts on failure", async () => {
      const store = useChatStore();
      store.folders = [{ id: "f1", name: "x", pinned: false } as ConversationFolder];
      convApiMock.updateFolder.mockResolvedValue({ id: "f1", pinned: true });
      await store.toggleFolderPinned("f1", true);
      expect(store.folders[0].pinned).toBe(true);
      convApiMock.updateFolder.mockRejectedValue(new Error("down"));
      await expect(store.toggleFolderPinned("f1", true)).rejects.toThrow("down");
      expect(store.folders[0].pinned).toBe(false);
    });

    it("reorderFolders reloads on failure", async () => {
      const store = useChatStore();
      store.folders = [{ id: "f1", name: "x", sort_order: 0 } as ConversationFolder];
      convApiMock.reorderFolders.mockRejectedValue(new Error("down"));
      convApiMock.listFolders.mockResolvedValue([{ id: "f1", name: "x", sort_order: 5 }]);
      await expect(store.reorderFolders([{ id: "f1", sort_order: 1 }])).rejects.toThrow("down");
      expect(store.folders[0].sort_order).toBe(5);
    });

    it("deleteFolder clears the folder_id on conversations", async () => {
      const store = useChatStore();
      store.folders = [{ id: "f1" } as ConversationFolder];
      store.conversations = [{ id: "c1", folder_id: "f1" } as Conversation];
      convApiMock.deleteFolder.mockResolvedValue(undefined);
      await store.deleteFolder("f1");
      expect(store.folders).toHaveLength(0);
      expect(store.conversations[0].folder_id).toBeNull();
    });

    it("moveConversationToFolder patches the local row", async () => {
      const store = useChatStore();
      store.conversations = [{ id: "c1", folder_id: null } as Conversation];
      convApiMock.update.mockResolvedValue({ id: "c1", folder_id: "f9" });
      await store.moveConversationToFolder("c1", "f9");
      expect(store.conversations[0].folder_id).toBe("f9");
      expect(convApiMock.update).toHaveBeenCalledWith("c1", { folder_id: "f9" });
    });
  });

  describe("conversation lifecycle", () => {
    it("newConversation creates and activates", async () => {
      const store = useChatStore();
      convApiMock.create.mockResolvedValue(makeDetail({ id: "c-new", active_agent_ids: ["hermes"] }));
      const id = await store.newConversation("hermes", "p1");
      expect(id).toBe("c-new");
      expect(store.activeId).toBe("c-new");
      expect(convApiMock.create).toHaveBeenCalledWith({ primary_agent_id: "hermes", profile_id: "p1" });
    });

    it("deleteConversation clears state when active", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.conversations = [{ id: "c1" } as Conversation];
      store.messages = [makeMessage({})];
      convApiMock.remove.mockResolvedValue(undefined);
      await store.deleteConversation("c1");
      expect(store.activeId).toBeNull();
      expect(store.messages).toHaveLength(0);
      expect(store.conversations).toHaveLength(0);
    });

    it("landing resets the session", () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.messages = [makeMessage({})];
      store.activeAgents = ["hermes", "gpt"];
      store.landing();
      expect(store.activeId).toBeNull();
      expect(store.messages).toHaveLength(0);
      expect(store.activeAgents).toEqual(["hermes"]);
    });

    it("openConversation maps persisted content fields and fetches files", async () => {
      const store = useChatStore();
      const detail = makeDetail({
        messages: [
          makeMessage({ content: { text: "x", tool_calls: [{ title: "t1", status: "done" }], thinking: "think", plan: [{ title: "p" }], usage: { context_size: 500, context_used: 100 } } }),
        ],
      });
      convApiMock.get.mockResolvedValue(detail);
      await store.openConversation("c1");
      expect(store.messages[0].steps).toEqual([{ title: "t1", status: "done" }]);
      expect(store.messages[0].thinking).toBe("think");
      expect(store.messages[0].plan).toEqual([{ title: "p" }]);
      expect(store.contextSize).toBe(500);
      expect(store.contextTokens).toBe(100);
      expect(convApiMock.files).toHaveBeenCalledWith("c1");
    });

    it("openConversation restores pending clarifies and replays SSE for streaming turns", async () => {
      const store = useChatStore();
      const streaming = makeMessage({
        id: "m-s",
        status: "streaming",
        content: {
          text: "",
          clarifies: [{ id: "cl1", question: "继续？", options: ["是"], status: "pending" }],
        },
      });
      convApiMock.get.mockResolvedValue(makeDetail({ messages: [streaming] }));
      await store.openConversation("c1");
      expect(store.pendingConfirmations).toEqual([expect.objectContaining({ id: "cl1", question: "继续？" })]);
      expect(streamStub.openSSE).toHaveBeenCalled();
      expect(store.isActivelyStreaming("c1")).toBe(true);
    });

    it("openConversation opens a group WS for group conversations", async () => {
      const store = useChatStore();
      convApiMock.get.mockResolvedValue(makeDetail({ type: "group" }));
      convApiMock.read.mockResolvedValue(undefined);
      await store.openConversation("c1");
      expect(streamStub.openWS).toHaveBeenCalled();
      expect(store.groupStreamId).toBe("c1");
      expect(convApiMock.read).toHaveBeenCalledWith("c1");
    });

    it("loadMoreMessages skips tmp messages and prepends older ones", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.messages = [makeMessage({ id: "tmp-1", role: "user" }), makeMessage({ id: "m2" })];
      convApiMock.getMessages.mockResolvedValue([makeMessage({ id: "old-1" })]);
      await store.loadMoreMessages();
      expect(store.messages.map((m) => m.id)).toEqual(["old-1", "tmp-1", "m2"]);
      expect(convApiMock.getMessages).toHaveBeenCalledWith("c1", { limit: 50, before: "m2" });
    });

    it("loadMoreMessages stops when no real messages exist", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.messages = [makeMessage({ id: "tmp-1", role: "user" })];
      await store.loadMoreMessages();
      expect(store.hasMoreMessages).toBe(false);
      expect(convApiMock.getMessages).not.toHaveBeenCalled();
    });
  });

  describe("agents", () => {
    it("toggleAgent refuses to remove hermes", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.activeAgents = ["hermes"];
      await store.toggleAgent("hermes");
      expect(convApiMock.setAgents).not.toHaveBeenCalled();
    });

    it("toggleAgent adds an agent and keeps hermes first", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.activeAgents = ["hermes"];
      convApiMock.setAgents.mockResolvedValue({ id: "c1", active_agent_ids: ["hermes", "gpt"] });
      await store.toggleAgent("gpt");
      expect(convApiMock.setAgents).toHaveBeenCalledWith("c1", ["hermes", "gpt"]);
      expect(store.activeAgents).toEqual(["hermes", "gpt"]);
    });

    it("toggleProfile maps to the profile's default agent", async () => {
      const store = useChatStore();
      store.profiles = [{ ...hermesProfile, id: "p2", default_agent_id: "gpt" }];
      store.activeId = "c1";
      store.activeAgents = ["hermes"];
      convApiMock.setAgents.mockResolvedValue({ id: "c1", active_agent_ids: ["hermes"] });
      await store.toggleProfile("p2");
      expect(convApiMock.setAgents).toHaveBeenCalledWith("c1", ["hermes", "gpt"]);
    });
  });

  describe("send — personal conversation", () => {
    beforeEach(() => {
      const store = useChatStore();
      store.activeId = "c1";
      store.conversations = [{ id: "c1", type: "personal" } as Conversation];
      store.messages = [];
    });

    it("sends optimistically and reconciles with the server response", async () => {
      const store = useChatStore();
      clientMock.tokenStore.access = "acc";
      convApiMock.send.mockResolvedValue({
        user_message: makeMessage({ id: "u-real", role: "user", content: { text: "hi" } }),
        agent_message: makeMessage({ id: "a-real", role: "agent" }),
      });
      const ok = await store.send("hi");
      expect(ok).toBe(true);
      expect(streamStub.openSSE).toHaveBeenCalled();
      expect(store.messages.map((m) => m.id)).toEqual(["u-real", "a-real"]);
      expect(store.isActivelyStreaming("c1")).toBe(true);
    });

    it("marks the bubbles error and returns false when the POST fails", async () => {
      const store = useChatStore();
      clientMock.tokenStore.access = "acc";
      convApiMock.send.mockRejectedValue(new Error("network"));
      const ok = await store.send("hi");
      expect(ok).toBe(false);
      expect(store.streamingConvoId).toBeNull();
      const errs = store.messages.filter((m) => m.status === "error");
      expect(errs.length).toBeGreaterThanOrEqual(1);
      expect(store.messages[0].content?.error).toContain("发送失败");
    });

    it("aborts without sending when the token cannot be restored", async () => {
      const store = useChatStore();
      clientMock.tokenStore.restore.mockResolvedValue(false);
      const ok = await store.send("hi");
      expect(ok).toBe(false);
      expect(convApiMock.send).not.toHaveBeenCalled();
    });
  });

  describe("send — group conversation", () => {
    function groupStore(): ReturnType<typeof useChatStore> {
      const store = useChatStore();
      store.activeId = "c1";
      store.conversations = [{ id: "c1", type: "group", channel_mode: "always" } as Conversation];
      store.messages = [];
      return store;
    }

    it("sends over the WS with an optimistic bubble when delivered", async () => {
      const store = groupStore();
      streamStub.send.mockReturnValue(true);
      const ok = await store.send("大家好", "hermes", { mentions: ["__all_agents__"] });
      expect(ok).toBe(true);
      expect(streamStub.openWS).toHaveBeenCalled();
      expect(store.messages[0]).toMatchObject({ role: "user", content: { text: "大家好" } });
      expect(store.streamingConvoId).toBe("c1");
      expect(streamStub.send).toHaveBeenCalledWith(
        expect.objectContaining({ action: "send", mentions: ["__all_agents__"] }),
      );
    });

    it("rolls back the bubble and toasts when the socket is not ready", async () => {
      const store = groupStore();
      streamStub.send.mockReturnValue(false);
      const ok = await store.send("大家好", "hermes", { mentions: ["__all_agents__"] });
      expect(ok).toBe(false);
      expect(store.messages[0].status).toBe("error");
      expect(store.messages[0].content?.error).toContain("未发送");
      expect(nsMock.toast).toHaveBeenCalled();
    });
  });

  describe("send — roundtable", () => {
    it("routes to sendRoundtable when multiple agents are active", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.activeAgents = ["hermes", "gpt"];
      store.conversations = [{ id: "c1", type: "personal" } as Conversation];
      streamStub.openWS.mockResolvedValue(undefined);
      streamStub.send.mockReturnValue(true);
      const ok = await store.send("多视角");
      expect(ok).toBe(true);
      expect(streamStub.openWS).toHaveBeenCalled();
      expect(streamStub.send).toHaveBeenCalledWith(
        expect.objectContaining({ action: "send", attached_file_ids: [] }),
      );
    });

    it("rolls back when the WS send fails", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.activeAgents = ["hermes", "gpt"];
      store.conversations = [{ id: "c1", type: "personal" } as Conversation];
      streamStub.openWS.mockResolvedValue(undefined);
      streamStub.send.mockReturnValue(false);
      const ok = await store.send("多视角");
      expect(ok).toBe(false);
      expect(store.messages[0].status).toBe("error");
      expect(nsMock.toast).toHaveBeenCalledWith("连接未就绪，消息未发送", "warn");
    });
  });

  describe("send — attachments", () => {
    it("uploads staged files and waits for conversions", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.conversations = [{ id: "c1", type: "personal" } as Conversation];
      clientMock.tokenStore.access = "acc";
      const file = new File(["x"], "a.md");
      convApiMock.upload.mockResolvedValue({ id: "f1", name: "a.md", processing_status: "processing" } as WorkspaceFile);
      convApiMock.files.mockResolvedValue([{ id: "f1", processing_status: "ready" } as WorkspaceFile]);
      convApiMock.send.mockResolvedValue({
        user_message: makeMessage({ id: "u1", role: "user" }),
        agent_message: makeMessage({ id: "a1", role: "agent" }),
      });
      await store.send("看下附件", "hermes", { stagedFiles: [file] });
      expect(convApiMock.upload).toHaveBeenCalledWith("c1", file);
      expect(convApiMock.send).toHaveBeenCalledWith("c1", "看下附件", expect.objectContaining({ fileIds: ["f1"] }));
    });

    it("aborts the send when the upload throws", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.conversations = [{ id: "c1", type: "personal" } as Conversation];
      convApiMock.upload.mockRejectedValue(new Error("disk"));
      const ok = await store.send("x", "hermes", { stagedFiles: [new File(["y"], "b.md")] });
      expect(ok).toBe(false);
      expect(convApiMock.send).not.toHaveBeenCalled();
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ title: "文件上传失败" }));
    });
  });

  describe("turn control", () => {
    it("cancel marks streaming messages cancelled and closes the stream", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.messages = [makeMessage({ id: "m-s", status: "streaming" })];
      convApiMock.cancel.mockResolvedValue(undefined);
      await store.cancel();
      expect(store.messages[0].status).toBe("cancelled");
      expect(store.streamingConvoId).toBeNull();
      expect(store.pendingConfirmations).toEqual([]);
      expect(nsMock.toast).toHaveBeenCalledWith("已停止生成", "info");
      expect(convApiMock.cancel).toHaveBeenCalledWith("c1");
    });

    it("respondConfirmation filters locally and confirms via api", async () => {
      const store = useChatStore();
      store.activeId = "c1";
      store.pendingConfirmations = [{ id: "r1", conversation_id: "c1", message_id: "m1", question: "Q", options: [] }];
      convApiMock.confirm.mockResolvedValue(undefined);
      await store.respondConfirmation("r1", "是");
      expect(store.pendingConfirmations).toHaveLength(0);
      expect(convApiMock.confirm).toHaveBeenCalledWith("c1", "r1", "是");
    });
  });

  describe("group helpers", () => {
    it("markRead clears the unread badge", async () => {
      const store = useChatStore();
      store.conversations = [{ id: "c1", unread: 3, has_mention: true } as Conversation];
      convApiMock.read.mockResolvedValue(undefined);
      await store.markRead("c1");
      expect(store.conversations[0]).toMatchObject({ unread: 0, has_mention: false });
    });

    it("sendTyping only sends on the open group stream", async () => {
      const store = useChatStore();
      streamStub.send.mockReturnValue(true);
      store.groupStreamId = "c1";
      store.activeId = "c1";
      store.sendTyping("张三");
      expect(streamStub.send).toHaveBeenCalledWith({ action: "typing", name: "张三" });
      store.groupStreamId = null;
      store.sendTyping("李四");
      expect(streamStub.send).toHaveBeenCalledTimes(1);
    });
  });
});
