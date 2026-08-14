import { describe, expect, it } from "vitest";
import { colorDiff, computeLineDiff } from "@/utils/diff";
import { extractDetail, fmtBytes, fmtDate, fmtDay, fmtNum } from "@/utils/format";

describe("utils/diff", () => {
  it("colorDiff wraps + lines as diff-add", () => {
    const out = colorDiff("+added");
    expect(out).toContain('<span class="diff-add">+added</span>');
  });

  it("colorDiff wraps - lines as diff-del", () => {
    const out = colorDiff("-removed");
    expect(out).toContain('<span class="diff-del">-removed</span>');
  });

  it("colorDiff leaves context lines plain", () => {
    const out = colorDiff("plain");
    expect(out).toBe("<span>plain</span>");
  });

  it("colorDiff does not treat +++/--- headers as additions", () => {
    const out = colorDiff("+++ b/x.md\n--- a/x.md");
    expect(out).not.toContain("diff-add");
    expect(out).not.toContain("diff-del");
  });

  it("colorDiff escapes HTML entities", () => {
    const out = colorDiff('<script>alert("x")</script>');
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("computeLineDiff returns unchanged text as context lines", () => {
    expect(computeLineDiff("a\nb", "a\nb")).toBe(" a\n b");
  });

  it("computeLineDiff marks fully different texts", () => {
    expect(computeLineDiff("old", "new")).toBe("-old\n+new");
  });

  it("computeLineDiff appends-only produces + lines", () => {
    expect(computeLineDiff("a", "a\nb")).toBe(" a\n+b");
  });

  it("computeLineDiff delete-only produces - lines", () => {
    expect(computeLineDiff("a\nb", "a")).toBe(" a\n-b");
  });

  it("computeLineDiff captures middle edit with context", () => {
    const out = computeLineDiff("keep1\nold\nkeep2", "keep1\nnew\nkeep2");
    expect(out).toBe(" keep1\n-old\n+new\n keep2");
  });

  it("computeLineDiff handles empty inputs (empty text = one empty line)", () => {
    expect(computeLineDiff("", "")).toBe(" ");
    expect(computeLineDiff("", "x")).toBe("-\n+x");
    expect(computeLineDiff("x", "")).toBe("-x\n+");
  });
});

describe("utils/format", () => {
  it("fmtDate formats a timestamp", () => {
    expect(fmtDate(new Date(2024, 0, 15, 9, 30))).toMatch(/2024/);
  });

  it("fmtDate returns empty for nullish", () => {
    expect(fmtDate(null)).toBe("");
    expect(fmtDate(undefined)).toBe("");
  });

  it("fmtDay returns date-only", () => {
    expect(fmtDay(new Date(2024, 0, 15, 9, 30))).toMatch(/2024/);
    expect(fmtDay(undefined)).toBe("");
  });

  it("fmtNum adds separators and defaults", () => {
    expect(fmtNum(1234)).toBe("1,234");
    expect(fmtNum(null)).toBe("0");
    expect(fmtNum(undefined)).toBe("0");
  });

  it("fmtBytes renders human-readable sizes", () => {
    expect(fmtBytes(0)).toBe("0 B");
    expect(fmtBytes(null)).toBe("0 B");
    expect(fmtBytes(1023)).toBe("1023 B");
    expect(fmtBytes(1024)).toBe("1.0 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fmtBytes(2 * 1024 * 1024 * 1024)).toBe("2.0 GB");
  });

  it("extractDetail unwraps axios detail", () => {
    const err = { response: { data: { detail: "校验失败" } } };
    expect(extractDetail(err)).toBe("校验失败");
  });

  it("extractDetail falls back to Error message and strings", () => {
    expect(extractDetail(new Error("boom"))).toBe("boom");
    expect(extractDetail("raw")).toBe("raw");
    expect(extractDetail({})).toBe("未知错误");
    expect(extractDetail(null)).toBe("未知错误");
  });
});
