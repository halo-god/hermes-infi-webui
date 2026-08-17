/**
 * Promise-based replacement for window.prompt / window.confirm.
 *
 * Rendered once by PromptModalHost (mounted in AppLayout); callers get the
 * typed value (or null on cancel) without wiring per-component modal state.
 */
import { reactive } from "vue";

export interface PromptOptions {
  title: string;
  /** confirm 模式的正文（显示在标题下方）。 */
  message?: string;
  /** prompt 模式：输入框上方的说明文字。 */
  label?: string;
  initial?: string;
  placeholder?: string;
  confirmText?: string;
  mode?: "prompt" | "confirm";
  /** Return an error message to block submission, or null to accept. */
  validate?: (v: string) => string | null;
}

const state = reactive<{
  open: boolean;
  opts: PromptOptions;
  resolve: ((v: string | null) => void) | null;
}>({
  open: false,
  opts: { title: "" },
  resolve: null,
});

export function usePromptModal() {
  function promptModal(opts: PromptOptions): Promise<string | null> {
    return new Promise((resolve) => {
      state.opts = { mode: "prompt", confirmText: "确定", ...opts };
      state.resolve = resolve;
      state.open = true;
    });
  }
  function confirmModal(opts: { title: string; message?: string }): Promise<boolean> {
    return promptModal({ ...opts, mode: "confirm" }).then((v) => v !== null);
  }
  function settle(v: string | null) {
    state.open = false;
    const r = state.resolve;
    state.resolve = null;
    r?.(v);
  }
  return { state, promptModal, confirmModal, settle };
}
