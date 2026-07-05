/** Line-level diff + unified-diff colorizer, shared by ChatView's file diffs
 * and the skill-evolution proposal review UI. */

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Colorizes unified-diff-style text (lines prefixed with +/-) for v-html. */
export function colorDiff(text: string): string {
  return text.split("\n").map((line) => {
    const esc = escapeHtml(line);
    if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="diff-add">${esc}</span>`;
    if (line.startsWith("-") && !line.startsWith("---")) return `<span class="diff-del">${esc}</span>`;
    return `<span>${esc}</span>`;
  }).join("\n");
}

/** Line-based LCS diff between two texts, rendered as unified-diff-style
 * text ("+"/"-"/" " prefixes) so it can feed straight into colorDiff(). */
export function computeLineDiff(oldText: string, newText: string): string {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  const n = a.length, m = b.length;
  // dp[i][j] = length of the LCS of a[i:] and b[j:]
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: string[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push(` ${a[i]}`);
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push(`-${a[i]}`);
      i++;
    } else {
      out.push(`+${b[j]}`);
      j++;
    }
  }
  while (i < n) { out.push(`-${a[i]}`); i++; }
  while (j < m) { out.push(`+${b[j]}`); j++; }
  return out.join("\n");
}
