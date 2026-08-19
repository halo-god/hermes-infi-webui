"""Generate docs/配置目录.md from the Settings class (CI-verified freshness).

Borrows the "generated + verified docs" loop from agent frameworks like
deepseek-harness: the catalog is rendered from the live pydantic-settings
schema, and CI re-runs the generator plus `git diff --exit-code` so the doc
can never drift from app/config.py.

Determinism rules:
- Reads ONLY class-level metadata (Settings.model_fields). Importing
  app.config does construct the module-level singleton (reads .env, mints
  random dev secrets), but no instance VALUE is ever rendered — secret-ish
  fields show "随机生成 / 须覆盖" placeholders.
- Field order = declaration order (model_fields preserves it), so the output
  is byte-stable across runs on the same source.

Usage:
    python scripts/gen_config_catalog.py            # writes ../docs/配置目录.md
    python scripts/gen_config_catalog.py --stdout   # print, don't write
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import Settings  # noqa: E402

CONFIG_PY = _BACKEND_DIR / "app" / "config.py"
OUTPUT = _REPO_ROOT / "docs" / "配置目录.md"

SECRET_FIELDS = {
    "secret_key",
    "first_admin_password",
    "database_url",
    "redis_url",
    "minio_secret_key",
}
SECRET_SUFFIXES = ("_api_key",)


def _is_secret(name: str) -> bool:
    return name in SECRET_FIELDS or name.endswith(SECRET_SUFFIXES)


def _group_of_source() -> dict[str, str]:
    """Map field name → its `# ── group ──` section.

    Comments are invisible to the AST, so group membership is recovered by
    line number: walk the raw source lines, remember the most recent
    `# ── xxx ──` comment, and attribute each annotated assignment (via its
    AST lineno) to that group.
    """
    source_lines = CONFIG_PY.read_text(encoding="utf-8").splitlines()
    line_group: dict[int, str] = {}
    current = "其他"
    for idx, line in enumerate(source_lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#") and "──" in stripped:
            current = stripped.lstrip("#").replace("─", "").strip() or current
        line_group[idx] = current

    tree = ast.parse("\n".join(source_lines))
    cls = next(
        c for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name == "Settings"
    )
    groups: dict[str, str] = {}
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            groups[stmt.target.id] = line_group.get(stmt.lineno, "其他")
    return groups


def _render_default(name: str, info) -> str:
    if _is_secret(name):
        if info.default_factory is not None:
            return "⚠ 随机生成（生产必须显式设置）"
        if info.default in (None, ""):
            return "⚠ 须配置（无默认值）"
        return "⚠ 含默认值（生产必须覆盖）"
    if info.default_factory is not None:
        return f"(default_factory: {getattr(info.default_factory, '__name__', 'factory')})"
    if info.default is None:
        return "（无，须设置）"
    if isinstance(info.default, bool):
        return str(info.default)
    if isinstance(info.default, (int, float)):
        return str(info.default)
    if isinstance(info.default, (list, tuple)):
        return repr(list(info.default))
    return f"`{info.default}`" if info.default != "" else "`（空）`"


def _render_type(info) -> str:
    ann = info.annotation
    if ann is None:
        return "-"
    if isinstance(ann, type):
        return ann.__name__
    return str(ann).replace("typing.", "").replace("pydantic.types.", "")


def generate() -> str:
    groups = _group_of_source()
    fields = Settings.model_fields  # class-level introspection, declaration order

    ordered: list[tuple[str, list[str]]] = []
    by_group: dict[str, list[str]] = {}
    for name in fields:  # declaration order
        by_group.setdefault(groups.get(name, "其他"), []).append(name)
    # Preserve the group order as first-seen in declaration order.
    for g in by_group:
        ordered.append((g, by_group[g]))

    lines = [
        "# 配置目录（环境变量参考）",
        "",
        "> ⚠️ 本文件由 `backend/scripts/gen_config_catalog.py` 自动生成，请勿手改。",
        "> CI 会重新生成并 diff 校验——改 `app/config.py` 后请运行脚本同步。",
        "> 注意：部分开关（如 RAG/摘要/技能演化）可被数据库 `system_settings` 运行时覆盖，",
        "> 以管理后台「系统设置」为最终运行值。",
        "",
    ]
    for group, names in ordered:
        lines.append(f"## {group}")
        lines.append("")
        lines.append("| 环境变量 | 类型 | 默认值 | 说明 |")
        lines.append("|---|---|---|---|")
        for name in names:
            info = fields[name]
            desc = (info.description or "").replace("\n", " ").replace("|", "\\|")
            default = _render_default(name, info)
            lines.append(
                f"| `{name.upper()}` | {_render_type(info)} | {default} | {desc} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args()
    text = generate()
    if args.stdout:
        print(text)
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
