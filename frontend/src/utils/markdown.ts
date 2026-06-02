/**
 * Markdown renderer powered by markdown-it + highlight.js + KaTeX + Mermaid.
 *
 * Drop-in replacement for the old 120-line custom renderer.
 * Same `renderMarkdown(src)` export — callers don't change.
 */
import MarkdownIt from "markdown-it";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark.css";
import katex from "@vscode/markdown-it-katex";
import "katex/dist/katex.min.css";

// ── markdown-it instance ──
const md: MarkdownIt = new MarkdownIt({
  html: false,        // security: no raw HTML
  linkify: true,
  typographer: true,
  breaks: true,       // \n → <br> (chat messages expect this)
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value;
      } catch { /* fall through */ }
    }
    return MarkdownIt.prototype.utils.escapeHtml(str);
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

md.renderer.rules.link_open = function (tokens: any[], idx: number, options: any, env: any, self: any) {
  const href = tokens[idx].attrGet("href") || "";
  const safe = /^\s*(https?:\/\/|mailto:|#|\/)/i.test(href) ? href : "#";
  tokens[idx].attrSet("href", safe);
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener");
  return defaultRender(tokens, idx, options, env, self);
};

// ── Code copy button + language label ──
const defaultFence =
  md.renderer.rules.fence ||
  function (tokens: any[], idx: number, options: any, _env: any, self: any) {
    return self.renderToken(tokens, idx, options);
  };

md.renderer.rules.fence = function (tokens: any[], idx: number, options: any, env: any, self: any) {
  const token = tokens[idx];
  const info = token.info.trim();
  const lang = info.split(/\s+/)[0] || "";
  const langLabel = lang ? `<span class="code-lang">${md.utils.escapeHtml(lang)}</span>` : "";
  const copyBtn = `<button class="code-copy-btn" onclick="copyCode(this)" title="复制">📋</button>`;
  const codeHtml = defaultFence(tokens, idx, options, env, self);
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
    mermaid.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "loose",
    });
    (window as any).__mermaid = mermaid;
    mermaidReady = true;
  } catch {
    // mermaid not available — skip diagram rendering
  }
}

async function renderMermaidBlocks(html: string): Promise<string> {
  await ensureMermaid();
  if (!mermaidReady) return html;

  const mermaid = (window as any).__mermaid;
  // Replace <code class="language-mermaid"> with rendered SVG
  const regex = /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g;
  const matches = [...html.matchAll(regex)];

  for (const match of matches) {
    const code = md.utils.unescapeAll(match[1]);
    const id = `mermaid-${++mermaidId}`;
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

// ── Main export ──
export function renderMarkdown(src: string): string {
  return md.render(src || "");
}

/**
 * Async version with Mermaid support.
 * Use this in components that need diagrams.
 */
export async function renderMarkdownAsync(src: string): Promise<string> {
  const html = md.render(src || "");
  return renderMermaidBlocks(html);
}

/**
 * Copy code button handler — attach to window for inline onclick.
 */
if (typeof window !== "undefined") {
  (window as any).copyCode = function (btn: HTMLButtonElement) {
    const wrapper = btn.closest(".code-block-wrapper");
    if (!wrapper) return;
    const code = wrapper.querySelector("code");
    if (!code) return;
    navigator.clipboard.writeText(code.textContent || "").then(() => {
      btn.textContent = "✅";
      setTimeout(() => (btn.textContent = "📋"), 1500);
    });
  };
}
