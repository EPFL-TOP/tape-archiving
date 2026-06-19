"""Browsable single-file HTML catalog generated from per-archive manifests.

Reads <output_dir>/manifests/*.json, builds a unified file tree where every
leaf carries its sha256, original size, compressed size, and the archive that
contains it. Outputs one HTML file with a clickable tree.

This is the artifact biologists open to find a file and learn which tape
archive to restore.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path


def render_catalog(output_dir: Path, html_path: Path) -> None:
    output_dir = Path(output_dir)
    manifests = _load_manifests(output_dir / "manifests")
    if not manifests:
        raise FileNotFoundError(f"no manifests found under {output_dir / 'manifests'}")

    tree = _build_tree(manifests)
    archives_summary = _archives_summary(manifests)
    source_root = manifests[0].get("source_root", "")

    tree_json = json.dumps(tree, separators=(",", ":")).replace("</", "<\\/")
    archives_json = json.dumps(archives_summary, separators=(",", ":")).replace("</", "<\\/")

    doc = _HTML
    doc = doc.replace("__TREE_DATA_JSON__", tree_json)
    doc = doc.replace("__ARCHIVES_JSON__", archives_json)
    doc = doc.replace("__SOURCE_ROOT__", html.escape(source_root))
    doc = doc.replace("__GENERATED_AT__", datetime.now(tz=timezone.utc).isoformat())
    doc = doc.replace("__TOTAL_SOURCE_BYTES__", str(tree["size_subtree"]))
    doc = doc.replace("__TOTAL_FILES__", str(tree["file_count_subtree"]))
    doc = doc.replace("__TOTAL_ARCHIVES__", str(len(manifests)))
    html_path.write_text(doc)


def _load_manifests(manifests_dir: Path) -> list[dict]:
    if not manifests_dir.is_dir():
        return []
    out = []
    for p in sorted(manifests_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def _archives_summary(manifests: list[dict]) -> list[dict]:
    return [
        {
            "name": m["archive"].removesuffix(".tar"),
            "tar": m["archive"],
            "sha256": m.get("archive_sha256", ""),
            "size_bytes": m.get("archive_size_bytes", 0),
            "file_count": m.get("file_count", 0),
            "source_bytes": m.get("total_uncompressed_bytes", 0),
            "compressed_bytes": m.get("total_compressed_bytes", 0),
        }
        for m in manifests
    ]


def _build_tree(manifests: list[dict]) -> dict:
    root = _new_node(".", ".")
    for m in manifests:
        archive_name = m["archive"].removesuffix(".tar")
        for f in m["files"]:
            parts = f["path"].split("/")
            node = root
            for i, part in enumerate(parts[:-1]):
                child = node["_kids"].get(part)
                if child is None:
                    child = _new_node(part, "/".join(parts[: i + 1]))
                    node["_kids"][part] = child
                node = child
            node["files"].append({
                "name": parts[-1],
                "size": f["size_bytes"],
                "sha256": f["sha256"],
                "compressed_size": f["compressed_bytes"],
                "archive": archive_name,
            })
    _finalize(root)
    return root


def _new_node(name: str, path: str) -> dict:
    return {
        "name": name,
        "path": path,
        "size_subtree": 0,
        "file_count_subtree": 0,
        "subdir_count": 0,
        "file_count_direct": 0,
        "size_direct": 0,
        "_kids": {},
        "children": [],
        "files": [],
    }


def _finalize(node: dict) -> tuple[int, int]:
    """Compute sums; convert _kids dict to sorted children list."""
    size = sum(f["size"] for f in node["files"])
    count = len(node["files"])
    node["file_count_direct"] = count
    node["size_direct"] = size
    for c in sorted(node["_kids"].values(), key=lambda c: c["name"]):
        sub_size, sub_count = _finalize(c)
        size += sub_size
        count += sub_count
        node["children"].append(c)
    node["subdir_count"] = len(node["children"])
    node["size_subtree"] = size
    node["file_count_subtree"] = count
    del node["_kids"]
    return size, count


_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tape-archive catalog</title>
<style>
:root {
  --fg: #1d2127; --muted: #6b7280; --bg: #fafafa; --card: #fff;
  --border: #e3e5e8; --accent: #0b6; --code: #f0f1f3;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 16px;
       line-height: 1.4; font-size: 14px; }
h1, h2 { margin-top: 0; }
header, section, #tree-panel, #archives-panel {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 18px; margin-bottom: 12px;
}
code { background: var(--code); padding: 1px 5px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: 0.92em; word-break: break-all; }
.hint { color: var(--muted); font-size: 0.88em; }

main { display: grid; grid-template-columns: 1fr 380px; gap: 12px;
       align-items: start; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }

#tree { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.92em; max-height: 80vh; overflow-y: auto; }
#tree details { margin-left: 14px; }
#tree > details { margin-left: 0; }
#tree summary { cursor: pointer; padding: 2px 4px; list-style: none;
                border-radius: 4px; }
#tree summary:hover { background: #f5f6f7; }
#tree summary::-webkit-details-marker { display: none; }
#tree summary::before { content: '▸'; display: inline-block; width: 1em;
                        color: var(--muted); transition: transform .1s; }
#tree details[open] > summary::before { transform: rotate(90deg); }
#tree .name { font-weight: 600; }
#tree .meta { color: var(--muted); }
#tree ul { list-style: none; padding-left: 14px; margin: 0;
           border-left: 1px dashed var(--border); }
#tree ul.files { padding-left: 22px; border-left: none; }
#tree ul.files li { padding: 2px 0; color: #444; display: flex;
                    gap: 8px; flex-wrap: wrap; align-items: baseline; }
#tree .file-name { font-weight: normal; color: var(--fg); }
#tree .sha { color: #6b7280; font-size: 0.85em; }
#tree .arch-link { background: var(--accent); color: white;
                   padding: 1px 8px; border-radius: 4px; font-size: 0.82em;
                   cursor: pointer; user-select: none; }
#tree .arch-link:hover { filter: brightness(1.1); }

#filter { width: 100%; padding: 8px 10px; margin-bottom: 10px;
          border: 1px solid var(--border); border-radius: 6px; font-size: 14px; }

#archives-list { font-size: 0.88em; }
.archive-row { border: 1px solid var(--border); border-radius: 6px;
               padding: 6px 10px; margin-bottom: 6px; }
.archive-row .arch-name { font-family: ui-monospace, Menlo, monospace;
                          font-weight: 600; word-break: break-all; }
.archive-row .arch-meta { color: var(--muted); margin-top: 2px; }
.archive-row code.sha { display: block; margin-top: 2px;
                        color: #6b7280; font-size: 0.82em; word-break: break-all; }
.archive-row.highlight { background: #fff8eb; border-color: #e0c878; }
</style>
</head><body>
<header>
  <h1>tape-archive catalog</h1>
  <p>Source: <code>__SOURCE_ROOT__</code></p>
  <p>Generated: <code>__GENERATED_AT__</code></p>
  <p>__TOTAL_FILES__ files · __TOTAL_SOURCE_BYTES__ source bytes · __TOTAL_ARCHIVES__ archives</p>
  <p class="hint">Click a folder to expand. Each file shows its SHA-256 (of the original, uncompressed bytes) and the archive that holds it. Click an archive badge to highlight that archive in the right panel.</p>
</header>

<main>
  <div id="tree-panel">
    <h2>Files</h2>
    <input id="filter" type="text" placeholder="Filter by filename or path… (case-insensitive)">
    <div id="tree"></div>
  </div>

  <aside id="archives-panel">
    <h2>Archives</h2>
    <div id="archives-list"></div>
  </aside>
</main>

<script>
const TREE_DATA = __TREE_DATA_JSON__;
const ARCHIVES = __ARCHIVES_JSON__;

function fmtSize(b) {
  let s = b;
  for (const u of ['B','KB','MB','GB','TB']) {
    if (s < 1024) return s.toFixed(s < 10 ? 1 : 0) + ' ' + u;
    s /= 1024;
  }
  return s.toFixed(1) + ' PB';
}
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

let filterText = '';

function nodeMatches(node) {
  if (!filterText) return true;
  const f = filterText.toLowerCase();
  if (node.path.toLowerCase().includes(f)) return true;
  for (const file of node.files || []) {
    if (file.name.toLowerCase().includes(f) || file.sha256.toLowerCase().includes(f)) return true;
  }
  for (const c of node.children || []) {
    if (nodeMatches(c)) return true;
  }
  return false;
}

function renderTree() {
  const container = document.getElementById('tree');
  container.innerHTML = '';
  container.appendChild(renderNode(TREE_DATA, true));
}

function renderNode(node, isRoot) {
  if (!nodeMatches(node)) {
    return document.createDocumentFragment();
  }
  const det = document.createElement('details');
  if (isRoot || filterText) det.open = true;
  const sum = document.createElement('summary');
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = (isRoot ? '.' : node.name) + '/';
  sum.appendChild(name);
  const meta = document.createElement('span');
  meta.className = 'meta';
  meta.textContent = ` [${fmtSize(node.size_subtree)}, ${node.file_count_subtree.toLocaleString()} files]`;
  sum.appendChild(meta);
  det.appendChild(sum);

  if (node.children && node.children.length) {
    const ul = document.createElement('ul');
    for (const c of node.children) {
      const li = document.createElement('li');
      li.appendChild(renderNode(c, false));
      ul.appendChild(li);
    }
    det.appendChild(ul);
  }
  if (node.files && node.files.length) {
    const ul = document.createElement('ul');
    ul.className = 'files';
    for (const f of node.files) {
      if (filterText && !f.name.toLowerCase().includes(filterText) &&
          !node.path.toLowerCase().includes(filterText) &&
          !f.sha256.toLowerCase().includes(filterText)) continue;
      const li = document.createElement('li');
      li.innerHTML =
        '<span class="file-name">' + escapeHtml(f.name) + '</span>' +
        '<span class="meta">[' + fmtSize(f.size) + ']</span>' +
        '<span class="sha" title="SHA-256 of original (uncompressed) bytes">' + f.sha256.slice(0, 16) + '…</span>' +
        '<span class="arch-link" data-archive="' + escapeHtml(f.archive) + '">' + escapeHtml(f.archive) + '</span>';
      ul.appendChild(li);
    }
    det.appendChild(ul);
  }
  return det;
}

function renderArchives() {
  const list = document.getElementById('archives-list');
  list.innerHTML = '';
  const sorted = [...ARCHIVES].sort((a, b) => b.size_bytes - a.size_bytes);
  for (const a of sorted) {
    const div = document.createElement('div');
    div.className = 'archive-row';
    div.id = 'arch-' + a.name;
    div.innerHTML =
      '<div class="arch-name">' + escapeHtml(a.tar) + '</div>' +
      '<div class="arch-meta">' + fmtSize(a.size_bytes) + ' on tape · ' +
        fmtSize(a.source_bytes) + ' source · ' +
        a.file_count.toLocaleString() + ' files · ' +
        'ratio ' + (a.size_bytes / Math.max(a.source_bytes, 1) * 100).toFixed(1) + '%' +
      '</div>' +
      '<code class="sha">sha256: ' + escapeHtml(a.sha256) + '</code>';
    list.appendChild(div);
  }
}

function setupArchiveClickHandlers() {
  document.getElementById('tree').addEventListener('click', e => {
    const link = e.target.closest('.arch-link');
    if (!link) return;
    e.preventDefault();
    const name = link.dataset.archive;
    document.querySelectorAll('.archive-row.highlight').forEach(el => el.classList.remove('highlight'));
    const target = document.getElementById('arch-' + name);
    if (target) {
      target.classList.add('highlight');
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
}

function init() {
  renderTree();
  renderArchives();
  setupArchiveClickHandlers();
  const f = document.getElementById('filter');
  let t;
  f.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      filterText = f.value.trim();
      renderTree();
    }, 200);
  });
}
document.addEventListener('DOMContentLoaded', init);
</script>
</body></html>
"""
