"""Single-file HTML preview of an archive plan.

Renders the source tree and the archive list in one self-contained .html file
(no external CSS/JS dependencies). Open it in any browser; scp it to a
webserver; drop it on GitHub Pages — all work the same way.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .scan import walk_tree


def render_plan_html(plan: dict, output_path: Path) -> None:
    source_root = Path(plan["source_root"])
    tree, _ = walk_tree(source_root)

    # Build a map: source-relative path -> archive name (and "files_only" flag)
    path_to_archive: dict[str, dict] = {}
    for arch in plan["archives"]:
        for m in arch["members"]:
            if isinstance(m, dict):
                key = m["path"]
                mode = m.get("mode", "subtree")
            else:
                key = m
                mode = "subtree"
            path_to_archive[key] = {"archive": arch["name"], "mode": mode}

    body_parts: list[str] = []
    body_parts.append(_render_header(plan))
    body_parts.append(_render_archives_table(plan))
    body_parts.append(_render_tree_section(tree, path_to_archive))

    html_doc = _HTML_TEMPLATE.format(
        title=html.escape(f"Plan: {plan['source_root']}"),
        css=_CSS,
        body="\n".join(body_parts),
        json_data=html.escape(json.dumps(plan, indent=2)),
    )
    output_path.write_text(html_doc, encoding="utf-8")


def _render_header(plan: dict) -> str:
    return f"""
<header>
  <h1>Archive plan preview</h1>
  <dl>
    <dt>Source</dt><dd><code>{html.escape(plan['source_root'])}</code></dd>
    <dt>Strategy</dt><dd><code>{html.escape(plan['strategy'])}</code></dd>
    <dt>Generated</dt><dd><code>{html.escape(plan['generated_at'])}</code></dd>
    <dt>Total size</dt><dd>{_human(plan['total_size_bytes'])}</dd>
    <dt>Total files</dt><dd>{plan['total_file_count']:,}</dd>
    <dt>Archives</dt><dd>{plan['total_archives']}</dd>
  </dl>
</header>
"""


def _render_archives_table(plan: dict) -> str:
    rows = []
    for i, arch in enumerate(sorted(plan["archives"], key=lambda a: -a["est_size_bytes"])):
        members_html = "<ul>" + "".join(
            "<li><code>" + html.escape(_member_str(m)) + "</code></li>"
            for m in arch["members"]
        ) + "</ul>"
        notes = arch.get("notes", "")
        warn_class = " class='warn'" if notes else ""
        notes_html = f"<div class='notes'>{html.escape(notes)}</div>" if notes else ""
        rows.append(f"""
<tr{warn_class}>
  <td>{i + 1}</td>
  <td><code>{html.escape(arch['name'])}</code></td>
  <td class='size'>{_human(arch['est_size_bytes'])}</td>
  <td class='num'>{arch.get('est_file_count', 0):,}</td>
  <td>{members_html}{notes_html}</td>
</tr>
""")
    return f"""
<section>
  <h2>Archives ({plan['total_archives']})</h2>
  <table class='archives'>
    <thead><tr><th>#</th><th>Name</th><th>Est. size</th><th>Files</th><th>Members</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</section>
"""


def _member_str(m) -> str:
    if isinstance(m, dict):
        return f"{m['path']} [{m.get('mode', 'subtree')}]"
    return m


def _render_tree_section(tree: dict, path_to_archive: dict) -> str:
    lines: list[str] = ["<section>", "<h2>Source tree</h2>",
                        "<p class='hint'>Click folders to expand. The archive name appears next to any directory that maps to one.</p>",
                        "<div class='tree'>"]
    _render_node(tree, path_to_archive, lines, is_root=True)
    lines.append("</div></section>")
    return "\n".join(lines)


def _render_node(node: dict, path_to_archive: dict, lines: list, *, is_root: bool) -> None:
    name = node["name"] if node["name"] != "." else Path(node["path"]).name or "."
    size = _human(node["size_subtree"])
    nfiles = node["file_count_subtree"]
    archive_info = path_to_archive.get(node["path"])
    badge = ""
    if archive_info:
        klass = "archive-badge files-only" if archive_info["mode"] == "files_only" else "archive-badge"
        suffix = " (files only)" if archive_info["mode"] == "files_only" else ""
        badge = f"<span class='{klass}'>→ {html.escape(archive_info['archive'])}{suffix}</span>"
    children = node.get("children", [])
    summary = (
        f"<summary>"
        f"<span class='name'>{html.escape(name)}/</span> "
        f"<span class='meta'>[{size}, {nfiles:,} files"
        + (f"; {node['file_count_direct']} loose" if node.get('subdir_count', 0) > 0 and node.get('file_count_direct', 0) > 0 else "")
        + f"]</span> {badge}</summary>"
    )
    is_open = " open" if is_root else ""
    lines.append(f"<details{is_open}>{summary}")
    if children:
        lines.append("<ul>")
        for c in children:
            lines.append("<li>")
            _render_node(c, path_to_archive, lines, is_root=False)
            lines.append("</li>")
        lines.append("</ul>")
    lines.append("</details>")


def _human(b: float) -> str:
    b = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


_CSS = """
:root {
  --fg: #1d2127;
  --muted: #6b7280;
  --bg: #fafafa;
  --card: #ffffff;
  --border: #e3e5e8;
  --accent: #0b6;
  --warn: #c93;
  --code: #f0f1f3;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px;
       line-height: 1.45; }
h1, h2 { margin-top: 0; }
header, section { background: var(--card); border: 1px solid var(--border);
                  border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; }
header dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px;
            margin: 0; }
header dt { color: var(--muted); }
header dd { margin: 0; }
code { background: var(--code); padding: 1px 5px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: 0.92em; }
table.archives { width: 100%; border-collapse: collapse; }
table.archives th, table.archives td { text-align: left; padding: 8px 12px;
                                       border-bottom: 1px solid var(--border); vertical-align: top; }
table.archives th { background: #f5f6f7; font-weight: 600; }
table.archives td.size, table.archives td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
table.archives tr.warn { background: #fff8eb; }
table.archives ul { margin: 4px 0; padding-left: 18px; }
.notes { color: var(--warn); font-size: 0.9em; margin-top: 4px; }
.tree { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.92em; }
.tree details { margin-left: 14px; }
.tree > details { margin-left: 0; }
.tree summary { cursor: pointer; padding: 2px 0; list-style: none; }
.tree summary::-webkit-details-marker { display: none; }
.tree summary::before { content: '▸'; display: inline-block; width: 1em;
                        color: var(--muted); transition: transform .1s; }
.tree details[open] > summary::before { transform: rotate(90deg); }
.tree .name { font-weight: 600; }
.tree .meta { color: var(--muted); }
.tree ul { list-style: none; padding-left: 14px; margin: 0;
           border-left: 1px dashed var(--border); }
.tree li { padding: 0; }
.archive-badge { background: var(--accent); color: white; padding: 1px 8px;
                 border-radius: 4px; font-size: 0.85em; margin-left: 8px;
                 white-space: nowrap; }
.archive-badge.files-only { background: var(--warn); }
.hint { color: var(--muted); font-size: 0.9em; }
"""


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head><body>
{body}
<details><summary>Raw plan JSON</summary><pre><code>{json_data}</code></pre></details>
</body></html>
"""
