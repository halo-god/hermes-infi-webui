import { beforeEach, describe, expect, it } from "vitest";

// usePromptModal holds module-level reactive state — reset it between tests
// by settling any pending prompt.
import { usePromptModal } from "@/composables/usePromptModal";

describe("usePromptModal", () => {
  beforeEach(() => {
    const { state, settle } = usePromptModal();
    if (state.open) settle(null);
  });

  it("initial state is closed", () => {
    const { state } = usePromptModal();
    expect(state.open).toBe(false);
  });

  it("promptModal opens with options and resolves the typed value", async () => {
    const { promptModal, state } = usePromptModal();
    const p = promptModal({ title: "命名", initial: "默认名" });
    expect(state.open).toBe(true);
    expect(state.opts.title).toBe("命名");
    // settle(v) is what PromptModalHost calls on confirm
    usePromptModal().settle("新名字");
    await expect(p).resolves.toBe("新名字");
    expect(state.open).toBe(false);
  });

  it("promptModal resolves null on cancel", async () => {
    const { promptModal, settle } = usePromptModal();
    const p = promptModal({ title: "取消测试" });
    settle(null);
    await expect(p).resolves.toBeNull();
  });

  it("confirmModal resolves true/false", async () => {
    const { confirmModal, settle } = usePromptModal();
    const ok = confirmModal({ title: "确认", message: "真的要删吗" });
    settle("ok");
    await expect(ok).resolves.toBe(true);

    const cancel = confirmModal({ title: "确认", message: "x" });
    settle(null);
    await expect(cancel).resolves.toBe(false);
  });

  it("confirm mode keeps the confirmText default", async () => {
    const { confirmModal, state } = usePromptModal();
    confirmModal({ title: "确认" });
    expect(state.opts.mode).toBe("confirm");
    expect(state.opts.confirmText).toBe("确定");
  });

  it("defaults: prompt mode + confirmText 确定", async () => {
    const { promptModal, state } = usePromptModal();
    promptModal({ title: "T" });
    expect(state.opts.mode).toBe("prompt");
    expect(state.opts.confirmText).toBe("确定");
  });

  it("concurrent prompt: latest call wins", async () => {
    const { promptModal, settle } = usePromptModal();
    const first = promptModal({ title: "第一" });
    const second = promptModal({ title: "第二" });
    settle("结果");
    await expect(second).resolves.toBe("结果");
    // first never settles — settled by the same resolve (last-wins semantics)
    expect(usePromptModal().state.open).toBe(false);
    void first;
  });
});
