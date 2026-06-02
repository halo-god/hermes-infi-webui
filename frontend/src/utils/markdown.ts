/** Compact, dependency-free Markdown → HTML renderer (HTML-escaped input).
 * Handles headings, bold/italic/strike/code, links, lists, task lists,
 * blockquotes, fenced code, tables, and horizontal rules — enough for chat
 * messages and workspace previews. */

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inline(s: string): string {
  let t = esc(s);
  t = t.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_m, text, url) => {
    const safe = /^\s*(https?:\/\/|mailto:|#|\/)/i.test(url) ? url : '#';
    return `<a href="${safe}" target="_blank" rel="noopener">${text}</a>`;
  });
  return t;
}

export function renderMarkdown(src: string): string {
  const lines = (src || "").split(/\r?\n/);
  const out: string[] = [];
  let i = 0;

  const flushTable = (start: number): number => {
    const rows: string[] = [];
    let j = start;
    while (j < lines.length && /\|/.test(lines[j]) && lines[j].trim() !== "") {
      rows.push(lines[j]);
      j++;
    }
    if (rows.length >= 2 && /^\s*\|?\s*:?-+/.test(rows[1])) {
      const cells = (r: string) =>
        r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const head = cells(rows[0]);
      out.push("<table><thead><tr>");
      head.forEach((h) => out.push(`<th>${inline(h)}</th>`));
      out.push("</tr></thead><tbody>");
      for (let k = 2; k < rows.length; k++) {
        out.push("<tr>");
        cells(rows[k]).forEach((c) => out.push(`<td>${inline(c)}</td>`));
        out.push("</tr>");
      }
      out.push("</tbody></table>");
      return j;
    }
    return start; // not a table
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") { i++; continue; }

    // fenced code
    if (/^```/.test(line.trim())) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }

    // hr
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr/>"); i++; continue; }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) { const lv = h[1].length; out.push(`<h${lv}>${inline(h[2])}</h${lv}>`); i++; continue; }

    // table
    if (/\|/.test(line)) {
      const next = flushTable(i);
      if (next !== i) { i = next; continue; }
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`);
      continue;
    }

    // lists (unordered / ordered / tasks)
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      out.push(ordered ? "<ol>" : "<ul>");
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, "");
        const task = /^\[([ xX])\]\s+/.exec(item);
        if (task) {
          const checked = task[1].toLowerCase() === "x";
          item = item.replace(/^\[([ xX])\]\s+/, "");
          out.push(
            `<li class="task"><span class="chk${checked ? " on" : ""}"></span>${inline(item)}</li>`,
          );
        } else {
          out.push(`<li>${inline(item)}</li>`);
        }
        i++;
      }
      out.push(ordered ? "</ol>" : "</ul>");
      continue;
    }

    // paragraph
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,6}\s|```|\s*>|\s*([-*+]|\d+\.)\s)/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    out.push(`<p>${inline(buf.join(" "))}</p>`);
  }

  return out.join("\n");
}
