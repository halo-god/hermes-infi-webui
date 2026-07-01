import { describe, it, expect, beforeEach } from "vitest";

describe("markdown renderer", () => {
  let renderMarkdown: typeof import("@/utils/markdown").renderMarkdown;

  beforeEach(async () => {
    const mod = await import("@/utils/markdown");
    renderMarkdown = mod.renderMarkdown;
  });

  it("renders basic paragraphs", () => {
    const html = renderMarkdown("Hello world");
    expect(html).toContain("Hello world");
    expect(html).toContain("<p>");
  });

  it("renders bold text", () => {
    const html = renderMarkdown("**bold**");
    expect(html).toContain("<strong>bold</strong>");
  });

  it("renders inline code", () => {
    const html = renderMarkdown("`code`");
    expect(html).toContain("<code>");
  });

  it("renders code blocks with language class", () => {
    const html = renderMarkdown("```python\nprint('hi')\n```");
    expect(html).toContain("language-python");
    expect(html).toContain("code-block-wrapper");
  });

  it("renders code copy button", () => {
    const html = renderMarkdown("```js\nconsole.log()\n```");
    expect(html).toContain("code-copy-btn");
  });

  it("renders links with target=_blank", () => {
    const html = renderMarkdown("[link](https://example.com)");
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener"');
  });

  it("renders tables", () => {
    const html = renderMarkdown("| A | B |\n|---|---|\n| 1 | 2 |");
    expect(html).toContain("<table>");
    expect(html).toContain("<td>");
  });

  it("renders headings", () => {
    const html = renderMarkdown("# Title\n## Subtitle");
    expect(html).toContain("<h1>");
    expect(html).toContain("<h2>");
  });

  it("renders blockquotes", () => {
    const html = renderMarkdown("> quote");
    expect(html).toContain("<blockquote");
    expect(html).toContain("quote");
  });

  it("renders unordered lists", () => {
    const html = renderMarkdown("- item 1\n- item 2");
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>");
  });

  it("handles empty input", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(null as any)).toBe("");
  });

  it("tightens a loose numbered list (blank lines between items)", () => {
    const html = renderMarkdown("1. Step one\n\n2. Step two\n\n3. Step three");
    // A tightened list renders items without a nested <p> wrapper.
    expect(html).not.toMatch(/<li>\s*<p>/);
    expect(html).toContain("Step one");
    expect(html).toContain("Step two");
    expect(html).toContain("Step three");
  });

  it("tightens a loose bullet list the same way", () => {
    const html = renderMarkdown("- item one\n\n- item two\n\n- item three");
    expect(html).not.toMatch(/<li>\s*<p>/);
  });

  it("keeps a blank line separating a list from following prose", () => {
    const html = renderMarkdown("- item one\n- item two\n\nSome closing remark.");
    expect(html).toContain("<ul>");
    expect(html).toContain("Some closing remark.");
    // The prose paragraph must render outside the list, not swallowed into it.
    expect(html.indexOf("</ul>")).toBeLessThan(html.indexOf("Some closing remark."));
  });

  it("keeps a blank line inside a multi-paragraph list item", () => {
    const html = renderMarkdown("- item one\n\n  continuation paragraph\n- item two");
    // Continuation line isn't a list marker, so the preprocessor must not
    // touch this blank line — item one keeps two <p> children.
    expect(html).toMatch(/<li>\s*<p>item one<\/p>\s*<p>continuation paragraph<\/p>\s*<\/li>/);
  });

  it("does not merge a bullet list directly followed by an ordered list", () => {
    const html = renderMarkdown("- bullet item\n\n1. ordered item");
    expect(html).toContain("<ul>");
    expect(html).toContain("<ol>");
  });
});
