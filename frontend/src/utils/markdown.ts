/**
 * Markdown renderer powered by markdown-it + highlight.js + KaTeX + Mermaid.
 *
 * Drop-in replacement for the old 120-line custom renderer.
 * Same `renderMarkdown(src)` export — callers don't change.
 */
import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/core";
// Register only common languages (not the full 190+ bundle)
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import sql from "highlight.js/lib/languages/sql";
import css from "highlight.js/lib/languages/css";
import xml from "highlight.js/lib/languages/xml";
import java from "highlight.js/lib/languages/java";
import cpp from "highlight.js/lib/languages/cpp";
import go from "highlight.js/lib/languages/go";
import rust from "highlight.js/lib/languages/rust";
import yaml from "highlight.js/lib/languages/yaml";
import markdown from "highlight.js/lib/languages/markdown";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import plaintext from "highlight.js/lib/languages/plaintext";
import "highlight.js/styles/github-dark.css";
import katex from "@vscode/markdown-it-katex";
import "katex/dist/katex.min.css";

// Register languages
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("shell", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("css", css);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("java", java);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("c", cpp);
hljs.registerLanguage("go", go);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("dockerfile", dockerfile);
hljs.registerLanguage("plaintext", plaintext);

// ── markdown-it instance ──
const md: MarkdownIt = new MarkdownIt({
  html: false,        // security: no raw HTML
  linkify: true,
  typographer: true,
  breaks: false,      // don't convert every \n to <br> — markdown handles paragraphs normally
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value;
      } catch { /* fall through */ }
    }
    // Inline HTML escape (MarkdownIt.prototype.utils is undefined in ESM)
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },
});

// ── KaTeX (math formulas) ──
md.use(katex);

// ── URL sanitization (preserve security fix from old renderer) ──
const defaultRender =
  md.renderer.rules.link_open ||
  function (tokens: any[], idx: number, options: any, _env: any, self: any) {
    return self.renderToken(tokens, idx, options);
  };

md.renderer.rules.link_open = function (tokens: any[], idx: number, options: any, _env: any, self: any) {
  const href = tokens[idx].attrGet("href") || "";
  const safe = /^\s*(https?:\/\/|mailto:|#|\/)/i.test(href) ? href : "#";
  tokens[idx].attrSet("href", safe);
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener");
  return defaultRender(tokens, idx, options, _env, self);
};

// ── Collapsible blockquote ──
md.renderer.rules.blockquote_open = function () {
  return '<blockquote class="collapsible-quote">';
};
md.renderer.rules.blockquote_close = function () {
  return '</blockquote>';
};

// Intercept inline tokens inside blockquotes to capture first line
const defaultInline = md.renderer.rules.inline || function (tokens: any[], idx: number, options: any, _env: any, self: any) {
  return self.renderToken(tokens, idx, options);
};
md.renderer.rules.inline = function (tokens: any[], idx: number, options: any, _env: any, self: any) {
  const html = defaultInline(tokens, idx, options, _env, self);
  return html;
};

// Post-process: wrap long blockquotes with details/summary
function postProcessBlockquotes(html: string): string {
  return html.replace(/<blockquote class="collapsible-quote">([\s\S]*?)<\/blockquote>/g, (_match, inner) => {
    const textContent = inner.replace(/<[^>]+>/g, "").trim();
    const lines = textContent.split("\n").filter((l: string) => l.trim());
    const firstLine = (lines[0] || "").slice(0, 60);
    if (lines.length > 1 || textContent.length > 60) {
      return `<details class="quote-collapsible"><summary class="quote-summary">💬 ${md.utils.escapeHtml(firstLine)}${lines.length > 1 ? "…" : ""}</summary><blockquote class="expanded-quote">${inner}</blockquote></details>`;
    }
    return `<blockquote class="simple-quote">${inner}</blockquote>`;
  });
}

// ── Knowledge reference collapse ──
function postProcessKnowledgeRefs(html: string): string {
  return html.replace(/【知识库:\s*([^】]+)】\s*<br\s*\/?>/g, (_match, name) => {
    return `<div class="knowledge-ref-header">📚 知识库: ${md.utils.escapeHtml(name.trim())} <span class="knowledge-ref-hint">(已发送给AI)</span></div>`;
  });
}

// ── RAG citation badges ──
// Turn "[1]" style inline citations into superscript badges. Runs after
// markdown rendering so links are already HTML; the pattern excludes link
// syntax "[n](url)" and bracketed text like "[Important]". Code blocks are
// protected (a "[1]" inside code must stay literal).
function postProcessCiteRefs(html: string): string {
  const blocks: string[] = [];
  html = html.replace(/<pre[\s\S]*?<\/pre>/g, (m) => {
    blocks.push(m);
    return `\u0000CITE${blocks.length - 1}\u0000`;
  });
  html = html.replace(
    /\[(\d{1,3})\](?!\()/g,
    '<sup class="cite-ref" title="知识库引用">[$1]</sup>'
  );
  html = html.replace(/\u0000CITE(\d+)\u0000/g, (_m, i) => blocks[Number(i)]);
  return html;
}

// ── Single-paragraph list items ──
// A list becomes "loose" the moment any item contains a blank line, and
// markdown-it then wraps EVERY item's content in <p>. LLM output is full of
// trailing blank lines / multi-line items, so nearly every list ends up loose
// and gains paragraph-level spacing. When an item holds exactly one paragraph
// and no other block content, drop the <p> wrapper — independent of the
// list-level loose/tight flag. Multi-paragraph items keep their <p> so the
// paragraph break inside the item stays visible.
const defaultParagraphOpen =
  md.renderer.rules.paragraph_open ||
  function (tokens: any[], idx: number, options: any, _env: any, self: any) {
    return self.renderToken(tokens, idx, options);
  };
const defaultParagraphClose =
  md.renderer.rules.paragraph_close ||
  function (tokens: any[], idx: number, options: any, _env: any, self: any) {
    return self.renderToken(tokens, idx, options);
  };

function isLoneParagraphItem(tokens: any[], idx: number): boolean {
  // Find the enclosing list_item_open (tokens[idx] is a paragraph_open)
  let closes = 0;
  let itemOpen = -1;
  for (let i = idx - 1; i >= 0; i--) {
    const t = tokens[i];
    if (t.type === "list_item_close") closes++;
    else if (t.type === "list_item_open") {
      if (closes === 0) { itemOpen = i; break; }
      closes--;
    }
  }
  if (itemOpen === -1) return false;
  // Find this item's matching list_item_close
  let opens = 0;
  let itemEnd = tokens.length;
  for (let i = itemOpen + 1; i < tokens.length; i++) {
    if (tokens[i].type === "list_item_open") opens++;
    else if (tokens[i].type === "list_item_close") {
      if (opens === 0) { itemEnd = i; break; }
      opens--;
    }
  }
  // The item must contain exactly one paragraph and no other block content.
  // Counting paragraph_open tokens (rather than scanning up to `idx`) keeps
  // the check identical whether called from paragraph_open or _close.
  let paragraphCount = 0;
  for (let i = itemOpen + 1; i < itemEnd; i++) {
    const t = tokens[i];
    if (t.type === "paragraph_open") paragraphCount++;
    else if (
      t.type === "blockquote_open" || t.type === "list_open" || t.type === "code_block" ||
      t.type === "fence" || t.type === "table_open" || t.type === "hr" || t.type === "heading_open"
    ) {
      return false;
    }
    // inline / text / softbreak / paragraph_close tokens are fine
  }
  return paragraphCount === 1;
}

md.renderer.rules.paragraph_open = function (tokens, idx, options, env, self) {
  if (isLoneParagraphItem(tokens, idx)) return "";
  return defaultParagraphOpen(tokens, idx, options, env, self);
};
md.renderer.rules.paragraph_close = function (tokens, idx, options, env, self) {
  if (isLoneParagraphItem(tokens, idx)) return "";
  return defaultParagraphClose(tokens, idx, options, env, self);
};

// ── Code copy button + language label ──
const defaultFence =
  md.renderer.rules.fence ||
  function (tokens: any[], idx: number, options: any, _env: any, self: any) {
    return self.renderToken(tokens, idx, options);
  };

md.renderer.rules.fence = function (tokens: any[], idx: number, options: any, _env: any, self: any) {
  const token = tokens[idx];
  const info = token.info.trim();
  const lang = info.split(/\s+/)[0] || "";
  const langLabel = lang ? `<span class="code-lang">${md.utils.escapeHtml(lang)}</span>` : "";
  const copyBtn = `<button class="code-copy-btn" onclick="copyCode(this)" title="复制">📋</button>`;
  const codeHtml = defaultFence(tokens, idx, options, _env, self);
  // Wrap with header bar
  return `<div class="code-block-wrapper">${langLabel}${copyBtn}${codeHtml}</div>`;
};

// ── Mermaid (diagrams) — lazy init ──
let mermaidReady = false;
let mermaidId = 0;

async function ensureMermaid() {
  if (mermaidReady) return;
  try {
    const { default: mermaid } = await import("mermaid");
    const isDark = document.body.classList.contains("dark");
    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? "dark" : "default",
      // "strict" — diagrams render from chat content authored by other users/agents,
      // not just trusted app code; "loose" allows click-bound JS and raw HTML labels.
      securityLevel: "strict",
    });
    (window as Window & { __mermaid?: unknown }).__mermaid = mermaid;
    mermaidReady = true;
  } catch {
    // mermaid not available — skip diagram rendering
  }
}

/** Re-initialize mermaid when theme changes. Call from theme toggle. */
export function resetMermaidTheme() {
  mermaidReady = false;
}

async function renderMermaidBlocks(html: string): Promise<string> {
  await ensureMermaid();
  if (!mermaidReady) return html;

  const mermaid = (window as Window & { __mermaid?: { render: (id: string, code: string) => Promise<{ svg: string }> } }).__mermaid;
  // Replace <code class="language-mermaid"> with rendered SVG
  const regex = /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g;
  const matches = [...html.matchAll(regex)];

  for (const match of matches) {
    const code = md.utils.unescapeAll(match[1]);
    const id = `mermaid-${++mermaidId}`;
    if (!mermaid) {
      html = html.replace(match[0], `<div class="mermaid-error"><pre>${md.utils.escapeHtml(code)}</pre></div>`);
      continue;
    }
    try {
      const { svg } = await mermaid.render(id, code);
      html = html.replace(match[0], `<div class="mermaid-wrapper">${svg}</div>`);
    } catch {
      // Render error — leave as code block
      html = html.replace(
        match[0],
        `<div class="mermaid-error"><pre>${md.utils.escapeHtml(code)}</pre></div>`
      );
    }
  }
  return html;
}

// ── Pre-processing: collapse excessive newlines ──
function collapseNewlines(text: string): string {
  // Replace 3+ consecutive newlines with 2 (preserves paragraph breaks)
  return text.replace(/\n{3,}/g, "\n\n");
}

// ── Pre-processing: strip trailing spaces (hard line breaks) ──
// CommonMark turns two trailing spaces into a <br> hard break even with
// `breaks: false`. LLM output frequently carries trailing spaces from
// copy-paste/alignment, which then renders as unwanted extra line breaks.
// Strip trailing whitespace outside fenced code blocks (where trailing
// spaces are semantically meaningful).
function stripTrailingSpaces(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let inFence = false;
  for (const line of lines) {
    if (/^\s*(?:`{3,}|~{3,})/.test(line)) inFence = !inFence;
    out.push(inFence ? line : line.trimEnd());
  }
  return out.join("\n");
}

// ── Pre-processing: tighten loose lists ──
// LLMs habitually put a blank line between list items. CommonMark then treats
// the list as "loose" and wraps each item's text in its own <p>, which reads
// as far too much vertical gap for what's meant to be a tight list. Collapse
// a blank line ONLY when it sits between two list-marker lines at the same
// indentation depth and marker family (bullet vs ordered) — this leaves
// blank lines that separate a list from surrounding prose, blank lines
// inside a multi-paragraph list item (next line isn't a marker), and nested
// sublists (indentation differs) untouched.
const LIST_MARKER_RE = /^(\s*)([-*+]|\d+[.)])\s+/;

function tightenLooseLists(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === "" && out.length > 0 && i + 1 < lines.length) {
      const prev = out[out.length - 1].match(LIST_MARKER_RE);
      const next = lines[i + 1].match(LIST_MARKER_RE);
      if (
        prev && next &&
        prev[1].length === next[1].length &&
        (/^[-*+]$/.test(prev[2]) === /^[-*+]$/.test(next[2]))
      ) {
        continue; // drop this blank line
      }
    }
    out.push(line);
  }
  return out.join("\n");
}

function preprocess(src: string): string {
  return tightenLooseLists(stripTrailingSpaces(collapseNewlines(src || "")));
}

// ── Main export ──
export interface RenderOptions {
  /** Convert "[1]" style citations to superscript badges. Only enable when
   * the message actually carries RAG citations (rag_refs) — otherwise
   * ordinary "[数字]" text gets mislabelled as knowledge references. */
  citeRefs?: boolean;
}

export function renderMarkdown(src: string, opts: RenderOptions = {}): string {
  const collapsed = preprocess(src);
  let html = md.render(collapsed);
  html = postProcessBlockquotes(html);
  html = postProcessKnowledgeRefs(html);
  if (opts.citeRefs) html = postProcessCiteRefs(html);
  return html;
}

/**
 * Async version with Mermaid support.
 * Use this in components that need diagrams.
 */
export async function renderMarkdownAsync(src: string, opts: RenderOptions = {}): Promise<string> {
  const collapsed = preprocess(src);
  let html = md.render(collapsed);
  html = postProcessBlockquotes(html);
  html = postProcessKnowledgeRefs(html);
  if (opts.citeRefs) html = postProcessCiteRefs(html);
  return renderMermaidBlocks(html);
}

/**
 * Copy code button handler — attach to window for inline onclick.
 */
if (typeof window !== "undefined") {
  (window as Window & { copyCode?: (btn: HTMLButtonElement) => void }).copyCode = function (btn: HTMLButtonElement) {
    const wrapper = btn.closest(".code-block-wrapper");
    if (!wrapper) return;
    const code = wrapper.querySelector("code");
    if (!code) return;
    const text = code.textContent || "";
    const doCopy = () => {
      if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
      }
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      return Promise.resolve();
    };
    doCopy().then(() => {
      btn.textContent = "✅";
      setTimeout(() => (btn.textContent = "📋"), 1500);
    });
  };
}
