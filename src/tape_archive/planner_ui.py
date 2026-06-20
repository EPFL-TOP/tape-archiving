"""Interactive HTML planner: browse the tree, click to mark archive roots,
download a YAML plan that the rest of the pipeline can ingest.

Single self-contained HTML file. No external CSS/JS. Open in any browser,
mount on GitHub Pages, scp anywhere — all the same.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from .scan import walk_tree


def render_planner(source_root: Path, output_path: Path) -> None:
    source_root = Path(source_root).resolve()
    tree, _ = walk_tree(source_root, with_files=True)
    tree_json = json.dumps(tree, separators=(",", ":"))
    # Prevent `</script>` inside filenames from terminating the embedded script.
    tree_json = tree_json.replace("</", "<\\/")

    doc = _HTML
    doc = doc.replace("__TREE_DATA_JSON__", tree_json)
    doc = doc.replace("__SOURCE_ROOT__", html.escape(str(source_root)))
    doc = doc.replace("__GENERATED_AT__", datetime.now(timezone.utc).isoformat())
    output_path.write_text(doc, encoding="utf-8")


_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tape-archive planner</title>
<style>
:root {
  --fg: #1d2127; --muted: #6b7280; --bg: #fafafa; --card: #fff;
  --border: #e3e5e8; --accent: #0b6; --accent-bg: #e7f6ee; --warn: #c93;
  --code: #f0f1f3;
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
header .actions { display: flex; gap: 8px; margin-top: 8px; }
button { background: var(--accent); color: #fff; border: 0; border-radius: 6px;
         padding: 6px 12px; cursor: pointer; font-size: 14px; }
button.secondary { background: #6b7280; }
button:hover { filter: brightness(1.1); }
.presets { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.presets button { background: #e3e5e8; color: var(--fg); }
code { background: var(--code); padding: 1px 5px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: 0.92em; }
.hint { color: var(--muted); font-size: 0.88em; }

main { display: grid; grid-template-columns: 1fr 360px; gap: 12px;
       align-items: start; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }

#tree { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.92em; max-height: 78vh; overflow-y: auto; }
#tree details { margin-left: 14px; }
#tree > details { margin-left: 0; }
#tree summary { cursor: pointer; padding: 2px 4px; list-style: none;
                border-radius: 4px; }
#tree summary:hover { background: #f5f6f7; }
#tree summary::-webkit-details-marker { display: none; }
#tree summary::before { content: '▸'; display: inline-block; width: 1em;
                        color: var(--muted); transition: transform .1s; }
#tree details[open] > summary::before { transform: rotate(90deg); }
#tree summary input[type="checkbox"] { margin: 0 6px 0 2px; cursor: pointer;
                                       vertical-align: middle; }
#tree .name { font-weight: 600; }
#tree .meta { color: var(--muted); }
#tree ul { list-style: none; padding-left: 14px; margin: 0;
           border-left: 1px dashed var(--border); }
#tree li { padding: 0; }
#tree ul.files { padding-left: 22px; border-left: none; }
#tree ul.files li { padding: 1px 0; color: #444; }
#tree ul.files .file-name { font-weight: normal; }
#tree .badge { background: var(--accent); color: #fff; padding: 1px 8px;
               border-radius: 4px; font-size: 0.85em; margin-left: 8px; }
#tree .badge.inherited { background: #b8c4d0; color: #1d2127; }
#tree summary.selected { background: var(--accent-bg); }

#archives-summary { font-size: 0.95em; margin-bottom: 10px; color: var(--muted); }
#archives-summary strong { color: var(--fg); }
.archive-entry { border: 1px solid var(--border); border-radius: 6px;
                 padding: 8px 10px; margin-bottom: 8px; }
.archive-entry input.archive-name { width: 100%; padding: 4px 6px;
                                    border: 1px solid var(--border);
                                    border-radius: 4px; font-family: ui-monospace, Menlo, monospace;
                                    font-size: 0.9em; margin-bottom: 4px; }
.archive-entry .archive-meta { font-size: 0.85em; color: var(--muted); }
.archive-entry .archive-root { font-size: 0.85em; margin-top: 4px; }
.archive-entry .archive-excludes { font-size: 0.82em; color: var(--warn); margin-top: 2px; }
.archive-entry button.remove { float: right; background: #e3e5e8;
                               color: var(--fg); padding: 2px 8px; font-size: 0.85em; }
</style>
</head><body>
<header>
  <h1>tape-archive planner</h1>
  <p>Source: <code id="src-path"></code></p>
  <p id="src-stats"></p>
  <div class="actions">
    <button id="download-btn">⬇ Download plan.yaml</button>
    <button id="reset-btn" class="secondary">Clear all selections</button>
  </div>
</header>

<section class="presets">
  <span>Quick start:</span>
  <button data-depth="1">Mark all at depth 1</button>
  <button data-depth="2">Mark all at depth 2</button>
  <button data-depth="3">Mark all at depth 3</button>
  <span class="hint" style="margin-left: 12px;">…then refine by clicking individual folders.</span>
</section>

<main>
  <div id="tree-panel">
    <h2>Source tree</h2>
    <p class="hint">☐ marks a folder as an archive root. Folder name expands. Files are shown for context only (cannot be archived individually).</p>
    <div id="tree"></div>
  </div>

  <aside id="archives-panel">
    <h2>Selected archives</h2>
    <div id="archives-summary"></div>
    <div id="archives-list"></div>
  </aside>
</main>

<script>
const TREE_DATA = __TREE_DATA_JSON__;
const SOURCE_ROOT = "__SOURCE_ROOT__";
const GENERATED_AT = "__GENERATED_AT__";
const LS_KEY = "tape-archive-planner:" + SOURCE_ROOT;

const state = {
  selected: new Set(),   // paths marked as archive roots
  names: {},             // path -> custom archive name
};

function loadState() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    state.selected = new Set(data.selected || []);
    state.names = data.names || {};
  } catch (e) { console.warn("loadState failed", e); }
}
function saveState() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      selected: [...state.selected], names: state.names
    }));
  } catch (e) { console.warn("saveState failed", e); }
}

function formatSize(b) {
  let s = b;
  for (const u of ['B','KB','MB','GB','TB']) {
    if (s < 1024) return s.toFixed(s < 10 ? 1 : 0) + ' ' + u;
    s /= 1024;
  }
  return s.toFixed(1) + ' PB';
}
function autoName(path) {
  return path.replace(/\//g, '__').replace(/\s+/g, '_').replace(/[^a-zA-Z0-9._\-]/g, '_');
}
function findNode(path, root) {
  if ((root || (root = TREE_DATA)).path === path) return root;
  for (const c of root.children || []) {
    const f = findNode(path, c);
    if (f) return f;
  }
  return null;
}

// "Owner archive" for a given path: the nearest selected ancestor (or self).
function ownerArchive(path) {
  if (state.selected.has(path)) return path;
  const parts = path.split('/');
  for (let i = parts.length - 1; i > 0; i--) {
    const p = parts.slice(0, i).join('/');
    if (state.selected.has(p)) return p;
  }
  return null;
}

// Compute size of a subtree, excluding any descendant archive roots.
function subtreeSizeExcluding(node, exclSet) {
  let size = node.size_direct || 0;
  let count = node.file_count_direct || 0;
  for (const c of (node.children || [])) {
    if (exclSet.has(c.path)) continue;
    const sub = subtreeSizeExcluding(c, exclSet);
    size += sub.size; count += sub.count;
  }
  return { size, count };
}

function computeArchives() {
  const roots = [...state.selected].sort();
  const out = [];
  for (const root of roots) {
    // Direct sub-archive roots = roots that start with root + '/' and have no
    // other root strictly between them and root.
    const descendants = roots.filter(r => r !== root && r.startsWith(root + '/'));
    const directExcludes = descendants.filter(d =>
      !descendants.some(other => other !== d && d.startsWith(other + '/'))
    );
    const node = findNode(root);
    if (!node) continue;
    const { size, count } = subtreeSizeExcluding(node, new Set(directExcludes));
    out.push({
      name: state.names[root] || autoName(root),
      root: root,
      members: [root],
      excludes: directExcludes,
      est_size_bytes: size,
      est_file_count: count,
    });
  }
  return out;
}

// ---------- rendering (lazy) ----------
// We render the tree on demand: only the currently-visible level is in the
// DOM. When a <details> is opened the first time, we render its direct
// children/files. State changes (checkbox toggles, presets) update badges
// and checkboxes in place — we never rebuild the tree.

function syncSelectionUi() {
  // Update badges + checkboxes on every <details> currently in the DOM.
  document.querySelectorAll('#tree details').forEach(updateNodeUi);
}

function updateNodeUi(det) {
  const node = det._node;
  if (!node) return;
  const sum = det.querySelector(':scope > summary');
  if (!sum) return;
  // Keep checkbox in sync with state (for presets/reset etc.).
  const cb = sum.querySelector('input[type="checkbox"]');
  if (cb) cb.checked = state.selected.has(node.path);
  // Recompute badge.
  sum.querySelectorAll('.badge').forEach(b => b.remove());
  sum.classList.remove('selected');
  const owner = ownerArchive(node.path);
  if (owner) {
    const isThis = owner === node.path;
    const b = document.createElement('span');
    b.className = 'badge' + (isThis ? '' : ' inherited');
    b.textContent = '→ ' + (state.names[owner] || autoName(owner));
    sum.appendChild(b);
    if (isThis) sum.classList.add('selected');
  }
}

function onStateChanged() {
  saveState();
  syncSelectionUi();
  renderArchives();
}

function renderTree() {
  const container = document.getElementById('tree');
  container.innerHTML = '';
  container.appendChild(renderNode(TREE_DATA, true));
}

function renderNode(node, isRoot) {
  const det = document.createElement('details');
  det._node = node;
  if (isRoot) det.open = true;

  const sum = document.createElement('summary');
  if (!isRoot) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = state.selected.has(node.path);
    cb.title = 'Mark this folder as an archive root';
    cb.addEventListener('click', e => e.stopPropagation());
    cb.addEventListener('change', () => {
      if (cb.checked) state.selected.add(node.path);
      else state.selected.delete(node.path);
      onStateChanged();
    });
    sum.appendChild(cb);
  }
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = (isRoot ? '.' : node.name) + '/';
  sum.appendChild(name);
  const meta = document.createElement('span');
  meta.className = 'meta';
  const loose = (node.subdir_count > 0 && node.file_count_direct > 0)
    ? `; ${node.file_count_direct} loose` : '';
  meta.textContent = ` [${formatSize(node.size_subtree)}, ${node.file_count_subtree.toLocaleString()} files${loose}]`;
  sum.appendChild(meta);

  // Initial badge (in case state already marks this node).
  const owner = ownerArchive(node.path);
  if (owner) {
    const isThis = owner === node.path;
    const b = document.createElement('span');
    b.className = 'badge' + (isThis ? '' : ' inherited');
    b.textContent = '→ ' + (state.names[owner] || autoName(owner));
    sum.appendChild(b);
    if (isThis) sum.classList.add('selected');
  }
  det.appendChild(sum);

  // Lazy: children/files are only built when this <details> opens.
  let childrenRendered = false;
  function renderChildren() {
    if (childrenRendered) return;
    childrenRendered = true;
    const childDirs = node.children || [];
    if (childDirs.length) {
      const ul = document.createElement('ul');
      for (const c of childDirs) {
        const li = document.createElement('li');
        li.appendChild(renderNode(c, false));
        ul.appendChild(li);
      }
      det.appendChild(ul);
    }
    const files = node.files || [];
    if (files.length) {
      const ul = document.createElement('ul');
      ul.className = 'files';
      for (const f of files) {
        const li = document.createElement('li');
        li.innerHTML = `<span class="file-name">${escapeHtml(f.name)}</span> <span class="meta">[${formatSize(f.size)}]</span>`;
        ul.appendChild(li);
      }
      det.appendChild(ul);
    }
  }
  // Root is open=true by default → render its children immediately.
  if (isRoot) {
    renderChildren();
  } else {
    det.addEventListener('toggle', () => {
      if (det.open) renderChildren();
    });
  }
  return det;
}

function renderArchives() {
  const list = document.getElementById('archives-list');
  const sum = document.getElementById('archives-summary');
  list.innerHTML = '';

  const archives = computeArchives();
  if (archives.length === 0) {
    sum.innerHTML = '<span class="hint">No archives selected. Click ☐ next to a folder, or use a preset above.</span>';
    return;
  }
  const totSize = archives.reduce((a, b) => a + b.est_size_bytes, 0);
  const totFiles = archives.reduce((a, b) => a + b.est_file_count, 0);
  sum.innerHTML = `<strong>${archives.length}</strong> archives, total <strong>${formatSize(totSize)}</strong>, <strong>${totFiles.toLocaleString()}</strong> files`;

  // Sort: largest first
  archives.sort((a, b) => b.est_size_bytes - a.est_size_bytes);
  for (const a of archives) {
    const div = document.createElement('div');
    div.className = 'archive-entry';
    const rm = document.createElement('button');
    rm.className = 'remove'; rm.textContent = '×'; rm.title = 'Remove this archive';
    rm.addEventListener('click', () => {
      state.selected.delete(a.root);
      delete state.names[a.root];
      onStateChanged();
    });
    div.appendChild(rm);

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'archive-name';
    inp.value = a.name;
    inp.addEventListener('change', () => {
      state.names[a.root] = inp.value;
      onStateChanged();
    });
    div.appendChild(inp);

    const m = document.createElement('div');
    m.className = 'archive-meta';
    m.textContent = `${formatSize(a.est_size_bytes)} · ${a.est_file_count.toLocaleString()} files`;
    div.appendChild(m);

    const r = document.createElement('div');
    r.className = 'archive-root';
    r.innerHTML = 'root: <code>' + escapeHtml(a.root) + '</code>';
    div.appendChild(r);

    if (a.excludes.length) {
      const e = document.createElement('div');
      e.className = 'archive-excludes';
      e.innerHTML = 'excludes: ' + a.excludes.map(x => '<code>' + escapeHtml(x) + '</code>').join(', ');
      div.appendChild(e);
    }
    list.appendChild(div);
  }
}

// ---------- presets ----------
function markDepth(targetDepth) {
  function walk(node, depth) {
    if (depth === targetDepth && node.path !== '.') {
      state.selected.add(node.path);
      return;
    }
    for (const c of (node.children || [])) walk(c, depth + 1);
  }
  walk(TREE_DATA, 0);
  onStateChanged();
}

// ---------- YAML download ----------
function yamlString(s) {
  if (s === '' || /^[\d\-+]/.test(s) || /[:#\[\]{}|>*&!%@?,"`']/.test(s) || /\s/.test(s)) {
    return "'" + s.replace(/'/g, "''") + "'";
  }
  return s;
}
function serializeYaml(plan) {
  let out = '';
  out += 'source_root: ' + yamlString(plan.source_root) + '\n';
  out += "generated_at: '" + plan.generated_at + "'\n";
  out += 'strategy: ui\n';
  out += 'total_size_bytes: ' + plan.total_size_bytes + '\n';
  out += 'total_file_count: ' + plan.total_file_count + '\n';
  out += 'total_archives: ' + plan.total_archives + '\n';
  out += 'archives:\n';
  for (const a of plan.archives) {
    out += '  - name: ' + yamlString(a.name) + '\n';
    out += '    members:\n';
    for (const m of a.members) out += '      - ' + yamlString(m) + '\n';
    if (a.excludes && a.excludes.length) {
      out += '    excludes:\n';
      for (const e of a.excludes) out += '      - ' + yamlString(e) + '\n';
    }
    out += '    est_size_bytes: ' + a.est_size_bytes + '\n';
    out += '    est_file_count: ' + a.est_file_count + '\n';
  }
  return out;
}
function downloadYaml() {
  const archives = computeArchives();
  if (archives.length === 0) {
    alert('No archives selected.');
    return;
  }
  const plan = {
    source_root: SOURCE_ROOT,
    generated_at: new Date().toISOString(),
    total_size_bytes: archives.reduce((a, b) => a + b.est_size_bytes, 0),
    total_file_count: archives.reduce((a, b) => a + b.est_file_count, 0),
    total_archives: archives.length,
    archives: archives,
  };
  const yaml = serializeYaml(plan);
  const blob = new Blob([yaml], { type: 'text/yaml' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'plan.yaml';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function init() {
  loadState();
  document.getElementById('src-path').textContent = SOURCE_ROOT;
  document.getElementById('src-stats').textContent =
    `Total: ${formatSize(TREE_DATA.size_subtree)}, ${TREE_DATA.file_count_subtree.toLocaleString()} files`;
  document.getElementById('download-btn').addEventListener('click', downloadYaml);
  document.getElementById('reset-btn').addEventListener('click', () => {
    if (!confirm('Clear all selections?')) return;
    state.selected.clear(); state.names = {};
    onStateChanged();
  });
  document.querySelectorAll('.presets button[data-depth]').forEach(b => {
    b.addEventListener('click', () => markDepth(parseInt(b.dataset.depth, 10)));
  });
  // Initial render: tree (lazy — only root + level-1 children eager) + archives.
  renderTree();
  renderArchives();
}
document.addEventListener('DOMContentLoaded', init);
</script>
</body></html>
"""
