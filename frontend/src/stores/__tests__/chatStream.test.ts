import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import type { Ref } from "vue";
import type { Message, StreamEvent } from "@/types";

const nsMock = vi.hoisted(() => ({ push: vi.fn(), toast: vi.fn() }));
const filesApiMock = vi.hoisted(() => ({ files: vi.fn() }));

vi.mock("@/stores/notifications", () => ({ useNotificationStore: () => nsMock }));
vi.mock("@/api/conversations", () => ({ conversationsApi: filesApiMock }));
// brandPrefix() is guarded by getActivePinia() try/catch — leave the real
// store unmocked so it falls back to "" without a pinia instance.

import { registerStreamHandlers, availableCommands } from "@/stores/chatStream";
import { setLocale } from "@/i18n";

type AnyEv = StreamEvent & Record<string, unknown>;

interface StreamStub {
  handlers: Map<string, ((ev: AnyEv) => void)[]>;
  on: ReturnType<typeof vi.fn>;
  offAll: ReturnType<typeof vi.fn>;
  emit: (ev: AnyEv) => void;
}

function makeStream(): StreamStub {
  const handlers = new Map<string, ((ev: AnyEv) => void)[]>();
  return {
    handlers,
    on: vi.fn((type: string, fn: (ev: AnyEv) => void) => {
      const list = handlers.get(type) ?? [];
      list.push(fn);
      handlers.set(type, list);
      return () => {
        const arr = handlers.get(type);
        if (arr) {
          const i = arr.indexOf(fn);
          if (i !== -1) arr.splice(i, 1);
        }
      };
    }),
    offAll: vi.fn(() => handlers.clear()),
    emit: (ev) => {
      (handlers.get(ev.type) ?? []).forEach((fn) => fn(ev));
    },
  };
}

interface DepsBag {
  activeId: Ref<string | null>;
  messages: Ref<Message[]>;
  conversations: Ref<{ id: string; title: string }[]>;
  pendingConfirmations: Ref<{ id: string; question?: string }[]>;
  contextTokens: Ref<number>;
  contextSize: Ref<number>;
  files: Ref<{ id: string; processing_status?: string }[]>;
  typingUsers: Ref<{ user_id: string; name: string }[]>;
  find: (id: string) => Message | undefined;
  refreshAfterTurn: ReturnType<typeof vi.fn>;
  triggerMessages: ReturnType<typeof vi.fn>;
  pushMessage: (m: Message) => void;
  spliceMessage: (idx: number, count: number, ...items: Message[]) => void;
}

function makeDeps(): DepsBag {
  const activeId = ref<string | null>("c1");
  const messages = ref<Message[]>([]);
  const conversations = ref<{ id: string; title: string }[]>([]);
  const pendingConfirmations = ref<{ id: string; question?: string }[]>([]);
  const contextTokens = ref(0);
  const contextSize = ref(0);
  const files = ref<{ id: string; processing_status?: string }[]>([]);
  const typingUsers = ref<{ user_id: string; name: string }[]>([]);
  return {
    activeId,
    messages,
    conversations,
    pendingConfirmations,
    contextTokens,
    contextSize,
    files,
    typingUsers,
    find: (id) => messages.value.find((x) => x.id === id),
    refreshAfterTurn: vi.fn(),
    triggerMessages: vi.fn(),
    pushMessage: (m) => messages.value.push(m),
    spliceMessage: (idx, count, ...items) => messages.value.splice(idx, count, ...items),
  };
}

function baseMessage(over: Partial<Message>): Message {
  return {
    id: "m1",
    conversation_id: "c1",
    owner_id: null,
    role: "agent",
    agent_id: "hermes",
    profile_id: null,
    content: { text: "" },
    status: "complete",
    created_at: new Date().toISOString(),
    ...over,
  } as Message;
}

describe("stores/chatStream handlers", () => {
  let stream: StreamStub;
  let deps: DepsBag;

  beforeEach(() => {
    vi.useFakeTimers();
    setLocale("zh-CN"); // jsdom defaults to en-US; t() must resolve zh-CN keys
    nsMock.push.mockReset();
    filesApiMock.files.mockReset();
    filesApiMock.files.mockResolvedValue([]);
    stream = makeStream();
    deps = makeDeps();
    availableCommands.value = [];
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function register() {
    registerStreamHandlers(stream as never, deps as never);
  }

  it("clears previous handlers before registering", () => {
    register();
    expect(stream.offAll).toHaveBeenCalled();
  });

  describe("message events", () => {
    it("drops echo messages already in the list", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "message", message: baseMessage({}) });
      expect(deps.messages.value).toHaveLength(1);
    });

    it("reconciles the sender's optimistic tmp- bubble", () => {
      deps.messages.value = [baseMessage({ id: "tmp-1", role: "user", content: { text: "hi" } })];
      register();
      stream.emit({
        type: "message",
        message: baseMessage({ id: "real-1", role: "user", content: { text: "hi" } }),
      });
      expect(deps.messages.value).toHaveLength(1);
      expect(deps.messages.value[0].id).toBe("real-1");
    });

    it("pushes new incoming messages", () => {
      register();
      stream.emit({ type: "message", message: baseMessage({ id: "m-new" }) });
      expect(deps.messages.value.map((m) => m.id)).toEqual(["m-new"]);
    });

    it("drops events from other conversations (scoped)", () => {
      deps.messages.value = [baseMessage({ status: "streaming" })];
      register();
      stream.emit({ type: "token", message_id: "m1", delta: "x", conversation_id: "c2" });
      expect(deps.messages.value[0].content?.text).toBe("");
    });
  });

  it("message_update applies the patch and triggers reactivity", () => {
    deps.messages.value = [baseMessage({ content: { text: "a", extra: 1 } as never })];
    register();
    stream.emit({
      type: "message_update",
      message_id: "m1",
      patch: { content: { text: "b" } as never, edited_at: "t1", deleted_at: null, reactions: { "👍": ["u1"] } },
    });
    const m = deps.messages.value[0];
    expect(m.content).toMatchObject({ text: "b", extra: 1 });
    expect(m.edited_at).toBe("t1");
    expect(m.reactions).toEqual({ "👍": ["u1"] });
    expect(deps.triggerMessages).toHaveBeenCalled();
  });

  describe("typing", () => {
    it("adds a typing user and expires it after 4s", () => {
      register();
      stream.emit({ type: "typing", user_id: "u1", name: "张三" });
      expect(deps.typingUsers.value).toEqual([{ user_id: "u1", name: "张三" }]);
      vi.advanceTimersByTime(4100);
      expect(deps.typingUsers.value).toHaveLength(0);
    });

    it("refreshes the timer and name for an existing user", () => {
      deps.typingUsers.value = [{ user_id: "u1", name: "旧" }];
      register();
      stream.emit({ type: "typing", user_id: "u1", name: "新" });
      expect(deps.typingUsers.value[0].name).toBe("新");
      vi.advanceTimersByTime(4100);
      expect(deps.typingUsers.value).toHaveLength(0);
    });

    it("ignores typing without user_id", () => {
      register();
      stream.emit({ type: "typing", user_id: "" });
      expect(deps.typingUsers.value).toHaveLength(0);
    });
  });

  it("members_changed dispatches a window event", () => {
    const spy = vi.spyOn(window, "dispatchEvent");
    register();
    stream.emit({ type: "members_changed", conversation_id: "c1" });
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ type: "hermes:members-changed" }));
    spy.mockRestore();
  });

  describe("subagent_nudge", () => {
    it("notifies success for done nudges", () => {
      register();
      stream.emit({ type: "subagent_nudge", subagent_id: "s1", status: "done" });
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ title: "后台任务已完成" }));
    });

    it("notifies warn for failed nudges", () => {
      register();
      stream.emit({ type: "subagent_nudge", subagent_id: "s1", status: "error" });
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ kind: "warn", title: "后台任务失败" }));
    });
  });

  it("token appends to a streaming message", () => {
    deps.messages.value = [baseMessage({ status: "streaming", content: { text: "ab" } })];
    register();
    stream.emit({ type: "token", message_id: "m1", delta: "cd" });
    expect(deps.messages.value[0].content?.text).toBe("abcd");
  });

  describe("roundtable events", () => {
    it("rt_start creates a roundtable bubble sorted by slot", () => {
      register();
      stream.emit({
        type: "rt_start",
        message_id: "rt1",
        agents: [
          { agent_id: "b", profile_id: null, slot: 1, label: "B", color: "blue", stance: "for" },
          { agent_id: "a", profile_id: null, slot: 0, label: "A", color: "red", stance: "against" },
        ],
      });
      const m = deps.messages.value![0];
      expect(m.role).toBe("roundtable");
      expect(m.status).toBe("streaming");
      expect(m.content?.replies?.map((r) => r.agent_id)).toEqual(["a", "b"]);
      expect(m.content?.merged).toEqual({ text: "", status: "pending" });
    });

    it("rt_token appends to the matching slot", () => {
      deps.messages.value = [
        baseMessage({
          id: "rt1",
          role: "roundtable",
          content: {
            text: "",
            replies: [
              { agent_id: "a", profile_id: null, text: "x", status: "streaming" },
              { agent_id: "b", profile_id: null, text: "", status: "streaming" },
            ],
          },
        }),
      ];
      register();
      stream.emit({ type: "rt_token", message_id: "rt1", slot: 1, delta: "y" });
      expect(deps.messages.value[0].content?.replies?.[1].text).toBe("y");
    });

    it("rt_reply_done marks the reply complete", () => {
      deps.messages.value = [
        baseMessage({
          id: "rt1",
          role: "roundtable",
          content: { text: "", replies: [{ agent_id: "a", profile_id: null, text: "x", status: "streaming" }] },
        }),
      ];
      register();
      stream.emit({ type: "rt_reply_done", message_id: "rt1", slot: 0, status: "complete" });
      expect(deps.messages.value[0].content?.replies?.[0].status).toBe("complete");
    });

    it("merge_start/merge_token drive the merged summary", () => {
      deps.messages.value = [
        baseMessage({ id: "rt1", role: "roundtable", content: { text: "", replies: [], merged: { text: "", status: "pending" } } }),
      ];
      register();
      stream.emit({ type: "merge_start", message_id: "rt1" });
      expect(deps.messages.value[0].content?.merged?.status).toBe("streaming");
      stream.emit({ type: "merge_token", message_id: "rt1", delta: "结论" });
      expect(deps.messages.value[0].content?.merged?.text).toBe("结论");
    });
  });

  describe("chain events", () => {
    it("chain_start creates a chain bubble with pending steps", () => {
      register();
      stream.emit({
        type: "chain_start",
        message_id: "ch1",
        agents: [
          { agent_id: "a", profile_id: null, slot: 0, label: "A", color: "red" },
          { agent_id: "b", profile_id: null, slot: 1, label: "B", color: "blue" },
        ],
      });
      const m = deps.messages.value![0];
      expect(m.role).toBe("chain");
      expect(m.content?.steps).toHaveLength(2);
      expect(m.content?.steps?.[0]).toMatchObject({ agent_id: "a", status: "pending" });
    });

    it("chain_step_token streams into the step and flips pending→streaming", () => {
      deps.messages.value = [
        baseMessage({
          id: "ch1",
          role: "chain",
          content: { text: "", steps: [{ agent_id: "a", profile_id: null, text: "", status: "pending" }] },
        }),
      ];
      register();
      stream.emit({ type: "chain_step_token", message_id: "ch1", slot: 0, delta: "w" });
      const step = deps.messages.value[0].content?.steps?.[0];
      expect(step?.text).toBe("w");
      expect(step?.status).toBe("streaming");
    });

    it("chain_step_done marks the step complete", () => {
      deps.messages.value = [
        baseMessage({
          id: "ch1",
          role: "chain",
          content: { text: "", steps: [{ agent_id: "a", profile_id: null, text: "w", status: "streaming" }] },
        }),
      ];
      register();
      stream.emit({ type: "chain_step_done", message_id: "ch1", slot: 0, status: "complete" });
      expect(deps.messages.value[0].content?.steps?.[0].status).toBe("complete");
    });
  });

  describe("tool_call", () => {
    it("adds a new step", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "tool_call", message_id: "m1", title: "搜索", status: "running", tool_kind: "web_search" });
      expect(deps.messages.value[0].steps).toEqual([
        expect.objectContaining({ title: "搜索", status: "running", tool_kind: "web_search" }),
      ]);
    });

    it("updates an existing step by title", () => {
      deps.messages.value = [baseMessage({ steps: [{ title: "搜索", status: "running" }] })];
      register();
      stream.emit({ type: "tool_call", message_id: "m1", title: "搜索", status: "complete", raw_input: { q: 1 } });
      expect(deps.messages.value[0].steps?.[0]).toMatchObject({ status: "complete", raw_input: { q: 1 } });
      expect(deps.messages.value[0].steps).toHaveLength(1);
    });
  });

  it("thought appends to a streaming message", () => {
    deps.messages.value = [baseMessage({ status: "streaming", thinking: "思考" })];
    register();
    stream.emit({ type: "thought", message_id: "m1", delta: "中" });
    expect(deps.messages.value[0].thinking).toBe("思考中");
  });

  it("thought keeps accumulating after status leaves streaming (bug #2)", () => {
    // The done/cancel event flips status the moment it lands; a thought delta
    // that arrives after that (SSE ordering under cancellation) must still be
    // accumulated — the old status===\"streaming\" guard dropped it, losing the
    // reasoning trail mid-turn.
    deps.messages.value = [baseMessage({ status: "complete", thinking: "已推理" })];
    register();
    stream.emit({ type: "thought", message_id: "m1", delta: "并继续" });
    expect(deps.messages.value[0].thinking).toBe("已推理并继续");
  });

  it("plan sets the plan entries", () => {
    deps.messages.value = [baseMessage({})];
    register();
    stream.emit({
      type: "plan",
      message_id: "m1",
      entries: [{ content: "c", status: "pending", priority: "high" }],
    });
    expect(deps.messages.value[0].plan).toEqual([expect.objectContaining({ content: "c" })]);
  });

  describe("usage", () => {
    it("persists usage into the message and content", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "usage", message_id: "m1", input_tokens: 10, output_tokens: 20, context_size: 1000, context_used: 300 });
      expect(deps.messages.value[0].usage).toMatchObject({ input_tokens: 10, output_tokens: 20 });
      expect(deps.messages.value[0].content?.usage).toMatchObject({ context_size: 1000 });
      expect(deps.contextSize.value).toBe(1000);
      expect(deps.contextTokens.value).toBe(300);
    });

    it("falls back to input+output when no context_size", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "usage", message_id: "m1", input_tokens: 10, output_tokens: 20 });
      expect(deps.contextTokens.value).toBe(30);
    });
  });

  it("session_info renames a freshly-created conversation", () => {
    deps.conversations.value = [{ id: "c1", title: "新会话" }];
    register();
    stream.emit({ type: "session_info", title: "周报" });
    expect(deps.conversations.value[0].title).toBe("周报");
  });

  it("session_info updates any title (backend guards placeholder-only writes)", () => {
    // The DB-side guard (_update_conv_title_guarded) already refuses to
    // overwrite user-renamed conversations — the frontend applies whatever
    // the runner sends (the old localized-新会话 guard never matched for
    // en-locale users).
    deps.conversations.value = [{ id: "c1", title: "已截断的首条消息占位" }];
    register();
    stream.emit({ type: "session_info", title: "销售数据分析" });
    expect(deps.conversations.value[0].title).toBe("销售数据分析");
  });

  describe("file events", () => {
    it("patches the processing_status on the workspace row", () => {
      deps.files.value = [{ id: "f1", processing_status: "processing" }];
      register();
      stream.emit({ type: "file", message_id: "m1", file_id: "f1", name: "a.docx", kind: "docx", version: 1, status: "ready" });
      expect(deps.files.value[0].processing_status).toBe("ready");
    });

    it("attaches files to the message and refetches after debounce", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "file", message_id: "m1", file_id: "f1", name: "a.md", kind: "md", version: 1 });
      expect(deps.messages.value[0].content?.files).toEqual([expect.objectContaining({ id: "f1", name: "a.md" })]);
      vi.advanceTimersByTime(400);
      expect(filesApiMock.files).toHaveBeenCalledWith("c1");
      expect(deps.triggerMessages).toHaveBeenCalled();
    });

    it("guards null file ids (dead links)", () => {
      deps.messages.value = [baseMessage({})];
      register();
      stream.emit({ type: "file", message_id: "m1", file_id: "", name: "x", kind: "md", version: 1 });
      expect(deps.messages.value[0].content?.files ?? []).toHaveLength(0);
    });

    it("roundtable files attach to the specific reply card", () => {
      deps.messages.value = [
        baseMessage({
          id: "rt1",
          role: "roundtable",
          content: { text: "", replies: [{ agent_id: "a", profile_id: null, text: "", status: "streaming" }] },
        }),
      ];
      register();
      stream.emit({ type: "file", message_id: "rt1", file_id: "f1", name: "a.xlsx", kind: "xlsx", version: 1, slot: 0 });
      expect(deps.messages.value[0].content?.replies?.[0].files).toEqual([expect.objectContaining({ id: "f1" })]);
    });

    it("skips the refetch when no conversation is active", () => {
      deps.activeId.value = null;
      register();
      stream.emit({ type: "file", message_id: "m1", file_id: "f1", name: "a", kind: "md", version: 1 });
      vi.advanceTimersByTime(400);
      expect(filesApiMock.files).not.toHaveBeenCalled();
    });
  });

  describe("confirmation flow", () => {
    it("dedupes confirmation_request and notifies", () => {
      const req = { id: "r1", conversation_id: "c1", message_id: "m1", question: "继续？", options: ["是", "否"] };
      register();
      stream.emit({ type: "confirmation_request", message_id: "m1", request: req });
      stream.emit({ type: "confirmation_request", message_id: "m1", request: req });
      expect(deps.pendingConfirmations.value).toHaveLength(1);
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ kind: "warn" }));
    });

    it("confirmation_response removes the pending request", () => {
      deps.pendingConfirmations.value = [{ id: "r1", question: "继续？" }];
      register();
      stream.emit({ type: "confirmation_response", message_id: "m1", request_id: "r1", choice: "是" });
      expect(deps.pendingConfirmations.value).toHaveLength(0);
    });

    it("clarify_auto pushes an info notification", () => {
      register();
      stream.emit({ type: "clarify_auto", message_id: "m1", question: "Q?", choice: "A" });
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ kind: "info" }));
    });
  });

  it("iteration_warning marks the message capped", () => {
    deps.messages.value = [baseMessage({})];
    register();
    stream.emit({ type: "iteration_warning", message_id: "m1", tool_calls: 10, limit: 5 });
    expect(deps.messages.value[0].iter_capped).toEqual({ tool_calls: 10, limit: 5 });
  });

  it("tool_blocked marks the message risk_blocked", () => {
    deps.messages.value = [baseMessage({})];
    register();
    stream.emit({ type: "tool_blocked", message_id: "m1", tool: "bash", title: "删除文件" });
    expect(deps.messages.value[0].risk_blocked).toEqual({ tool: "bash", title: "删除文件" });
  });

  it("commands_update refreshes the available commands", () => {
    register();
    stream.emit({ type: "commands_update", message_id: "m1", commands: [{ name: "summary" }] });
    expect(availableCommands.value).toEqual([{ name: "summary" }]);
  });

  describe("done", () => {
    it("finalizes the message and schedules a refresh", () => {
      deps.messages.value = [baseMessage({ status: "streaming" })];
      register();
      stream.emit({ type: "done", message_id: "m1", status: "complete", text: "最终" });
      const m = deps.messages.value[0];
      expect(m.status).toBe("complete");
      expect(m.content?.text).toBe("最终");
      vi.advanceTimersByTime(600);
      expect(deps.refreshAfterTurn).toHaveBeenCalled();
    });

    it("closes a streaming merged summary", () => {
      deps.messages.value = [
        baseMessage({ id: "rt1", role: "roundtable", content: { text: "", replies: [], merged: { text: "x", status: "streaming" } } }),
      ];
      register();
      stream.emit({ type: "done", message_id: "rt1", status: "complete" });
      expect(deps.messages.value[0].content?.merged?.status).toBe("complete");
    });

    it("does not notify when the turn was cancelled", () => {
      deps.activeId.value = null;
      deps.messages.value = [baseMessage({ status: "streaming" })];
      register();
      stream.emit({ type: "done", message_id: "m1", status: "cancelled" });
      expect(nsMock.push).not.toHaveBeenCalled();
    });

    it("notifies when the conversation is not visible", () => {
      deps.activeId.value = null;
      deps.messages.value = [baseMessage({ status: "streaming" })];
      register();
      stream.emit({ type: "done", message_id: "m1", status: "complete" });
      expect(nsMock.push).toHaveBeenCalledWith(expect.objectContaining({ title: expect.any(String), kind: "success" }));
    });
  });

  it("error marks the message and schedules a refresh", () => {
    deps.messages.value = [baseMessage({ status: "streaming" })];
    register();
    stream.emit({ type: "error", message_id: "m1", detail: "boom" });
    expect(deps.messages.value[0].status).toBe("error");
    vi.advanceTimersByTime(600);
    expect(deps.refreshAfterTurn).toHaveBeenCalled();
  });

  it("the disposer cancels pending refresh timers and clears handlers", () => {
    deps.messages.value = [baseMessage({ status: "streaming" })];
    // Register, schedule a refresh via done, then dispose — exactly what
    // chat.ts's setupStreamHandlers does before every re-registration.
    const disposer1 = registerStreamHandlers(stream as never, deps as never);
    stream.emit({ type: "done", message_id: "m1", status: "complete" });
    disposer1();
    const disposer2 = registerStreamHandlers(stream as never, deps as never);
    vi.advanceTimersByTime(600);
    expect(deps.refreshAfterTurn).not.toHaveBeenCalled();
    disposer2();
    expect(stream.offAll).toHaveBeenCalled();
  });
});
