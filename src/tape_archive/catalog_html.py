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


def render_catalog(output_dir: Path, html_path: Path, *, index_url: str = "../index.html") -> None:
    output_dir = Path(output_dir)
    manifests = _load_manifests(output_dir / "manifests")
    if not manifests:
        raise FileNotFoundError(f"no manifests found under {output_dir / 'manifests'}")

    archives_data = _archives_data(manifests)
    total_bytes, total_files = _aggregate_totals(archives_data)
    source_root = manifests[0].get("source_root", "")
    shipped = _load_json(output_dir / "shipped.json")    # may be None
    notes = _load_json(output_dir / "notes.json")        # may be None

    def _js(payload) -> str:
        return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    doc = _HTML
    doc = doc.replace("__ARCHIVES_DATA_JSON__", _js(archives_data))
    doc = doc.replace("__SHIPPED_JSON__", _js(shipped or {}))
    doc = doc.replace("__NOTES_JSON__", _js(notes or {}))
    doc = doc.replace("__SOURCE_ROOT__", html.escape(source_root))
    doc = doc.replace("__GENERATED_AT__", datetime.now(tz=timezone.utc).isoformat())
    doc = doc.replace("__TOTAL_SOURCE_BYTES__", str(total_bytes))
    doc = doc.replace("__TOTAL_FILES__", str(total_files))
    doc = doc.replace("__TOTAL_ARCHIVES__", str(len(archives_data)))
    doc = doc.replace("__INDEX_URL__", html.escape(index_url))
    html_path.write_text(doc, encoding="utf-8")


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_manifests(manifests_dir: Path) -> list[dict]:
    if not manifests_dir.is_dir():
        return []
    out = []
    for p in sorted(manifests_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def _archives_data(manifests: list[dict]) -> list[dict]:
    """Per-archive summary AND its own file tree. The catalog displays one
    collapsible section per archive, each containing its tree."""
    out = []
    for m in manifests:
        tree = _build_archive_tree(m.get("files", []))
        out.append({
            "name": m["archive"].removesuffix(".tar"),
            "tar": m["archive"],
            "sha256": m.get("archive_sha256", ""),
            "size_bytes": m.get("archive_size_bytes", 0),
            "file_count": m.get("file_count", 0),
            "source_bytes": m.get("total_uncompressed_bytes", 0),
            "compressed_bytes": m.get("total_compressed_bytes", 0),
            "tree": tree,
        })
    return out


def _build_archive_tree(files: list[dict]) -> dict:
    root = _new_node(".", ".")
    for f in files:
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
        })
    _finalize(root)
    return root


def _aggregate_totals(archives_data: list[dict]) -> tuple[int, int]:
    """Total source bytes + file count across all archives."""
    total_bytes = sum(a["source_bytes"] for a in archives_data)
    total_files = sum(a["file_count"] for a in archives_data)
    return total_bytes, total_files


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

main { background: var(--card); border: 1px solid var(--border);
       border-radius: 8px; padding: 14px 18px; }

#filter { width: 100%; padding: 8px 10px; margin-bottom: 12px;
          border: 1px solid var(--border); border-radius: 6px; font-size: 14px; }

.archive-folder { border: 1px solid var(--border); border-radius: 6px;
                  margin-bottom: 6px; background: #f5f7fa; }
.archive-folder > summary {
  cursor: pointer; padding: 8px 12px; list-style: none;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
}
.archive-folder > summary:hover { background: #ecf0f4; }
.archive-folder > summary::-webkit-details-marker { display: none; }
.archive-folder > summary::before { content: '▸'; display: inline-block;
                                    width: 1em; color: var(--muted);
                                    transition: transform .1s; }
.archive-folder[open] > summary::before { transform: rotate(90deg); }
.archive-folder .folder-icon { color: var(--accent); }
.archive-folder .folder-name { font-weight: 600;
                               font-family: ui-monospace, Menlo, monospace; }
.archive-folder .folder-stats { color: var(--muted); font-size: 0.88em;
                                margin-left: auto; }
.archive-folder .folder-body { padding: 4px 6px 8px 20px;
                               border-top: 1px solid var(--border); }

.archive-section { border: 1px solid var(--border); border-radius: 6px;
                   margin-bottom: 8px; background: #fcfcfd; }
.archive-section > summary {
  cursor: pointer; padding: 10px 12px; list-style: none; border-radius: 6px;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
}
.archive-section > summary:hover { background: #f5f6f7; }
.archive-section > summary::-webkit-details-marker { display: none; }
.archive-section > summary::before { content: '▸'; display: inline-block;
                                     width: 1em; color: var(--muted);
                                     transition: transform .1s; }
.archive-section[open] > summary::before { transform: rotate(90deg); }
.archive-section .arch-name {
  font-family: ui-monospace, Menlo, monospace; font-weight: 600;
  word-break: break-all; flex: 1; min-width: 200px;
}
.archive-section .arch-stats { color: var(--muted); font-size: 0.88em; }
.archive-section .arch-actions { margin-left: auto; }
.archive-section .arch-actions button { margin-left: 4px; }
.archive-section .arch-body { padding: 8px 14px 14px 32px;
                              border-top: 1px solid var(--border); }
.archive-section .arch-meta-line { color: var(--muted); font-size: 0.85em;
                                   margin-bottom: 6px; word-break: break-all; }
.archive-section .arch-notes-inline {
  background: #f5f7fa; border-left: 3px solid var(--accent);
  padding: 6px 10px; margin: 4px 0 8px 0; border-radius: 4px; font-size: 0.9em;
}

.tree { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.9em; }
.tree details { margin-left: 14px; }
.tree > details { margin-left: 0; }
.tree summary { cursor: pointer; padding: 1px 4px; list-style: none;
                border-radius: 4px; }
.tree summary:hover { background: #f5f6f7; }
.tree summary::-webkit-details-marker { display: none; }
.tree summary::before { content: '▸'; display: inline-block; width: 1em;
                        color: var(--muted); transition: transform .1s; }
.tree details[open] > summary::before { transform: rotate(90deg); }
.tree .name { font-weight: 600; }
.tree .meta { color: var(--muted); }
.tree ul { list-style: none; padding-left: 14px; margin: 0;
           border-left: 1px dashed var(--border); }
.tree ul.files { padding-left: 22px; border-left: none; }
.tree ul.files li { padding: 1px 0; color: #444; display: flex;
                    gap: 8px; flex-wrap: wrap; align-items: baseline; }
.tree .file-name { font-weight: normal; color: var(--fg); }
.tree .sha { color: #6b7280; font-size: 0.85em; }
.notes-block { background: #f5f7fa; border-left: 3px solid var(--accent);
               padding: 8px 12px; margin: 8px 0; border-radius: 4px; }
.notes-block .field { margin: 2px 0; }
.notes-block .field .label { color: var(--muted); display: inline-block; min-width: 90px; }
.notes-block .field code { background: transparent; padding: 0; }
.tag { display: inline-block; background: #e3e5e8; color: var(--fg);
       padding: 1px 8px; border-radius: 999px; font-size: 0.82em; margin-right: 4px; }
.status-badge { display: inline-block; padding: 1px 8px; border-radius: 4px;
                font-size: 0.82em; font-weight: 600; }
.status-active { background: #d4ecdc; color: #1a6c3b; }
.status-expiring { background: #fff1cc; color: #8a6500; }
.status-expired { background: #fad7d6; color: #8b1f1c; }
.status-unshipped { background: #e3e5e8; color: var(--muted); }
button.action { background: var(--accent); color: white; border: none;
                padding: 5px 12px; border-radius: 4px; cursor: pointer;
                font-size: 0.9em; }
button.action:hover { filter: brightness(0.95); }
button.copy { background: #e3e5e8; color: var(--fg); padding: 1px 8px;
              border: none; border-radius: 4px; font-size: 0.78em;
              cursor: pointer; margin-left: 4px; }
button.copy:hover { background: #d3d6d8; }
button.copy.copied { background: #d4ecdc; color: #1a6c3b; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.4);
                  display: none; z-index: 100; }
.modal-backdrop.show { display: flex; align-items: center; justify-content: center; }
.modal { background: white; border-radius: 8px; padding: 18px 22px;
         width: 480px; max-width: 92vw; max-height: 90vh; overflow-y: auto; }
.modal h3 { margin-top: 0; }
.modal label { display: block; margin-top: 10px; font-size: 0.9em; color: var(--muted); }
.modal input, .modal textarea {
  width: 100%; padding: 6px 8px; margin-top: 3px; box-sizing: border-box;
  border: 1px solid var(--border); border-radius: 4px; font-family: inherit; font-size: 14px;
}
.modal textarea { resize: vertical; min-height: 60px; }
.modal .row { display: flex; gap: 10px; margin-top: 14px; }
.modal .row button { flex: 1; padding: 8px; }
.nav-bar { background: var(--card); border: 1px solid var(--border);
           border-radius: 8px; padding: 8px 14px; margin-bottom: 12px;
           font-size: 0.9em; }
.nav-bar a { color: var(--accent); text-decoration: none; font-weight: 600; }
.nav-bar a:hover { text-decoration: underline; }
</style>
</head><body>
<div class="nav-bar">
  <a href="__INDEX_URL__">← Back to lab archives index</a>
  <span id="serve-status" style="margin-left:14px; font-size: 0.9em;"></span>
</div>
<header>
  <h1>tape-archive catalog</h1>
  <p>Source: <code>__SOURCE_ROOT__</code></p>
  <p>Generated: <code>__GENERATED_AT__</code></p>
  <p>__TOTAL_FILES__ files · __TOTAL_SOURCE_BYTES__ source bytes · __TOTAL_ARCHIVES__ archives</p>
  <div id="meta-section"></div>
  <div style="margin-top: 8px;">
    <button class="action" id="edit-notes-btn">✎ Edit notes</button>
  </div>
  <p class="hint">Each archive expands to show the files it contains, their SHA-256 (of the original uncompressed bytes), and a copy-restore command. Use the filter to search across all archives.</p>
</header>

<main>
  <input id="filter" type="text" placeholder="Filter files / archives by name, path, or sha256… (case-insensitive)">
  <div id="archives-main"></div>
</main>

<!-- Edit-notes modal: handles both collection-level and per-archive notes -->
<div class="modal-backdrop" id="modal">
  <div class="modal">
    <h3 id="modal-title">Edit notes</h3>
    <p class="hint" id="modal-hint">These travel with the catalog as <code>notes.json</code>.</p>
    <label>Description<textarea id="n-description" rows="3" placeholder="Free-form description"></textarea></label>
    <div id="collection-only-fields">
      <label>PI<input id="n-pi" type="text" placeholder="e.g. Andy Oates"></label>
      <label>Contact<input id="n-contact" type="text" placeholder="email"></label>
    </div>
    <label>Tags (comma-separated)<input id="n-tags" type="text" placeholder="microscopy, wscpaper, ece"></label>
    <label>Expires at<input id="n-expires" type="date"></label>
    <label>Expiration note<input id="n-expnote" type="text" placeholder="Reason / next review action"></label>
    <div class="row">
      <button class="action" id="save-notes-btn">Save changes</button>
      <button class="copy" id="cancel-notes-btn">Cancel</button>
    </div>
    <p class="hint" id="save-hint" style="margin-top:10px;display:none;"></p>
  </div>
</div>

<script>
const ARCHIVES_DATA = __ARCHIVES_DATA_JSON__;
const SHIPPED = __SHIPPED_JSON__;
let NOTES = __NOTES_JSON__;
// Track what's currently being edited in the modal.
let editTarget = { kind: 'collection' };  // or { kind: 'archive', name: '<archive-name>' }

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

// ---------- file-tree rendering inside one archive ----------
function buildTreeSummary(node, isRoot) {
  const sum = document.createElement('summary');
  const name = document.createElement('span');
  name.className = 'name';
  name.textContent = (isRoot ? '.' : node.name) + '/';
  sum.appendChild(name);
  const meta = document.createElement('span');
  meta.className = 'meta';
  meta.textContent = ` [${fmtSize(node.size_subtree)}, ${node.file_count_subtree.toLocaleString()} files]`;
  sum.appendChild(meta);
  return sum;
}

function buildFileLi(f) {
  const li = document.createElement('li');
  li.innerHTML =
    '<span class="file-name">' + escapeHtml(f.name) + '</span>' +
    '<span class="meta">[' + fmtSize(f.size) + ']</span>' +
    '<span class="sha" title="SHA-256 of original (uncompressed) bytes">' + f.sha256.slice(0, 16) + '…</span>';
  return li;
}

function renderNodeLazy(node, isRoot) {
  const det = document.createElement('details');
  if (isRoot) det.open = true;
  det.appendChild(buildTreeSummary(node, isRoot));
  let rendered = false;
  function expand() {
    if (rendered) return;
    rendered = true;
    if (node.children && node.children.length) {
      const ul = document.createElement('ul');
      for (const c of node.children) {
        const li = document.createElement('li');
        li.appendChild(renderNodeLazy(c, false));
        ul.appendChild(li);
      }
      det.appendChild(ul);
    }
    if (node.files && node.files.length) {
      const ul = document.createElement('ul');
      ul.className = 'files';
      for (const f of node.files) ul.appendChild(buildFileLi(f));
      det.appendChild(ul);
    }
  }
  if (isRoot) expand();
  else det.addEventListener('toggle', () => { if (det.open) expand(); });
  return det;
}

function renderNodeFiltered(node, isRoot) {
  const f = filterText;
  const dirSelfMatches = node.path.toLowerCase().includes(f);
  const matchingFiles = (node.files || []).filter(file =>
    file.name.toLowerCase().includes(f) ||
    node.path.toLowerCase().includes(f) ||
    file.sha256.toLowerCase().includes(f)
  );
  const childResults = [];
  for (const c of (node.children || [])) {
    const r = renderNodeFiltered(c, false);
    if (r) childResults.push(r);
  }
  if (!isRoot && !dirSelfMatches && childResults.length === 0 && matchingFiles.length === 0) {
    return null;
  }
  const det = document.createElement('details');
  det.open = true;
  det.appendChild(buildTreeSummary(node, isRoot));
  if (childResults.length) {
    const ul = document.createElement('ul');
    for (const child of childResults) {
      const li = document.createElement('li');
      li.appendChild(child);
      ul.appendChild(li);
    }
    det.appendChild(ul);
  }
  if (matchingFiles.length) {
    const ul = document.createElement('ul');
    ul.className = 'files';
    for (const file of matchingFiles) ul.appendChild(buildFileLi(file));
    det.appendChild(ul);
  }
  return det;
}

// ---------- per-archive sections ----------
function archiveNotes(name) {
  return (NOTES.archives && NOTES.archives[name]) || {};
}

// Split an archive name on '__' (which the planner generates from '/' in the
// source path). Recovers the original folder hierarchy.
function archivePathSegments(name) {
  return name.split('__').filter(s => s.length > 0);
}

// Build a tree: { folders: {name -> tree}, archives: [archive] }
function buildArchiveHierarchy(archives) {
  const root = { folders: {}, archives: [] };
  for (const a of archives) {
    const parts = archivePathSegments(a.name);
    if (parts.length <= 1) {
      // Flat name (or single-segment) → leaf at root
      root.archives.push(a);
      continue;
    }
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (!node.folders[p]) node.folders[p] = { folders: {}, archives: [] };
      node = node.folders[p];
    }
    node.archives.push(a);
  }
  return root;
}

// Aggregate stats (size, file count, archive count) for a folder subtree.
function folderTotals(node) {
  let bytes = 0, files = 0, archives = 0;
  for (const a of node.archives) {
    bytes += a.size_bytes || 0;
    files += a.file_count || 0;
    archives += 1;
  }
  for (const k of Object.keys(node.folders)) {
    const s = folderTotals(node.folders[k]);
    bytes += s.bytes; files += s.files; archives += s.archives;
  }
  return { bytes, files, archives };
}

function renderArchivesMain() {
  const container = document.getElementById('archives-main');
  container.innerHTML = '';
  const tapeRoot = SHIPPED && SHIPPED.tape_root;

  // Apply filter first (so the hierarchy reflects only matching archives).
  const visibleArchives = filterText
    ? ARCHIVES_DATA.filter(a =>
        a.name.toLowerCase().includes(filterText) ||
        a.tar.toLowerCase().includes(filterText) ||
        (a.sha256 || '').toLowerCase().includes(filterText) ||
        anyMatchInTree(a.tree, filterText))
    : [...ARCHIVES_DATA];

  if (visibleArchives.length === 0) {
    container.innerHTML = '<div class="hint">No archives match the filter.</div>';
    return;
  }

  const tree = buildArchiveHierarchy(visibleArchives);
  renderHierarchyInto(container, tree, tapeRoot);
}

// Same matching conditions as renderNodeFiltered uses, so the top-level
// filter and the per-archive tree filter stay in sync.
function anyMatchInTree(node, text) {
  if (node.path && node.path.toLowerCase().includes(text)) return true;
  if ((node.files || []).some(f =>
      f.name.toLowerCase().includes(text) ||
      f.sha256.toLowerCase().includes(text))) return true;
  for (const c of (node.children || [])) if (anyMatchInTree(c, text)) return true;
  return false;
}

function renderHierarchyInto(container, node, tapeRoot) {
  // 1. Folders first, alphabetical
  const folderNames = Object.keys(node.folders).sort();
  for (const fname of folderNames) {
    container.appendChild(renderFolderNode(fname, node.folders[fname], tapeRoot));
  }
  // 2. Leaf archives at this level, largest first
  const sorted = [...node.archives].sort((a, b) => (b.size_bytes || 0) - (a.size_bytes || 0));
  for (const a of sorted) {
    const section = renderArchiveSection(a, tapeRoot);
    if (section) container.appendChild(section);
  }
}

function renderFolderNode(name, node, tapeRoot) {
  const det = document.createElement('details');
  det.className = 'archive-folder';
  if (filterText) det.open = true;  // expand all when filtering

  const sum = document.createElement('summary');
  const icon = document.createElement('span');
  icon.className = 'folder-icon';
  icon.textContent = '📁';
  sum.appendChild(icon);

  const nameEl = document.createElement('span');
  nameEl.className = 'folder-name';
  nameEl.textContent = name + '/';
  sum.appendChild(nameEl);

  const t = folderTotals(node);
  const stats = document.createElement('span');
  stats.className = 'folder-stats';
  stats.textContent = `${t.archives} archive${t.archives === 1 ? '' : 's'} · ${fmtSize(t.bytes)} · ${t.files.toLocaleString()} files`;
  sum.appendChild(stats);
  det.appendChild(sum);

  // Lazy-render children when this folder is opened.
  let rendered = false;
  function expand() {
    if (rendered) return;
    rendered = true;
    const body = document.createElement('div');
    body.className = 'folder-body';
    renderHierarchyInto(body, node, tapeRoot);
    det.appendChild(body);
  }
  if (det.open) expand();
  else det.addEventListener('toggle', () => { if (det.open) expand(); });

  return det;
}

function renderArchiveSection(a, tapeRoot) {
  // When filtering, pre-compute the filtered tree so we know whether to show
  // this archive at all. archive name/sha256 also count as a match.
  let preFilteredTree = null;
  if (filterText) {
    const archMatches = a.name.toLowerCase().includes(filterText) ||
                        a.tar.toLowerCase().includes(filterText) ||
                        (a.sha256 || '').toLowerCase().includes(filterText);
    preFilteredTree = renderNodeFiltered(a.tree, true);
    if (!archMatches && !preFilteredTree) return null;
    if (!preFilteredTree && archMatches) {
      // archive itself matches but no files — render full tree lazily
      preFilteredTree = null;
    }
  }

  const det = document.createElement('details');
  det.className = 'archive-section';
  det.dataset.archive = a.name;
  if (filterText) det.open = true;

  const sum = document.createElement('summary');
  const nameEl = document.createElement('span');
  nameEl.className = 'arch-name';
  nameEl.textContent = a.tar;
  sum.appendChild(nameEl);

  const stats = document.createElement('span');
  stats.className = 'arch-stats';
  const ratio = (a.size_bytes / Math.max(a.source_bytes, 1) * 100).toFixed(1);
  stats.textContent = `${fmtSize(a.size_bytes)} on tape · ${fmtSize(a.source_bytes)} source · ${a.file_count.toLocaleString()} files · ratio ${ratio}%`;
  sum.appendChild(stats);

  const actions = document.createElement('span');
  actions.className = 'arch-actions';
  if (tapeRoot) {
    const tarFull = tapeRoot.replace(/\/$/, '') + '/' + a.tar;
    const cmd = `cp ${tarFull} /restore/ && tape-archive restore /restore/${a.tar} -o /restore/${a.name} --parallel 4`;
    const btn = document.createElement('button');
    btn.className = 'copy';
    btn.dataset.cmd = cmd;
    btn.textContent = '📋 copy restore';
    btn.addEventListener('click', e => e.stopPropagation());  // don't toggle details
    actions.appendChild(btn);
  }
  const notesBtn = document.createElement('button');
  notesBtn.className = 'copy edit-archive-notes';
  notesBtn.textContent = '✎ notes';
  notesBtn.dataset.archive = a.name;
  notesBtn.addEventListener('click', e => {
    e.stopPropagation();
    openModal({ kind: 'archive', name: a.name });
  });
  actions.appendChild(notesBtn);
  sum.appendChild(actions);
  det.appendChild(sum);

  // Body: lazy on open, eager when filtering.
  let bodyRendered = false;
  function renderBody() {
    if (bodyRendered) return;
    bodyRendered = true;
    const body = document.createElement('div');
    body.className = 'arch-body';
    const metaLine = document.createElement('div');
    metaLine.className = 'arch-meta-line';
    metaLine.innerHTML = 'sha256: <code>' + escapeHtml(a.sha256) + '</code>';
    body.appendChild(metaLine);

    // Per-archive notes display
    const n = archiveNotes(a.name);
    if (n && Object.keys(n).length) {
      const notesDiv = document.createElement('div');
      notesDiv.className = 'arch-notes-inline';
      const parts = [];
      if (n.description) parts.push('<div><strong>Description:</strong> ' + escapeHtml(n.description) + '</div>');
      if (n.tags && n.tags.length) parts.push('<div>' + n.tags.map(t => '<span class="tag">' + escapeHtml(t) + '</span>').join('') + '</div>');
      if (n.expires_at) {
        const st = expirationStatus(n);
        parts.push('<div><span class="status-badge status-' + st.kind + '">' + escapeHtml(st.label) + '</span>' +
                   (n.expiration_note ? ' — ' + escapeHtml(n.expiration_note) : '') + '</div>');
      }
      notesDiv.innerHTML = parts.join('');
      body.appendChild(notesDiv);
    }

    const treeWrap = document.createElement('div');
    treeWrap.className = 'tree';
    treeWrap.appendChild(preFilteredTree || renderNodeLazy(a.tree, true));
    body.appendChild(treeWrap);
    det.appendChild(body);
  }
  if (filterText) {
    renderBody();
  } else {
    det.addEventListener('toggle', () => { if (det.open) renderBody(); });
  }
  return det;
}

// ---------- meta section: shipped destination + notes display ----------
function expirationStatus(notes) {
  if (!notes || !notes.expires_at) return { kind: 'active', label: 'No expiration' };
  const expDate = new Date(notes.expires_at);
  if (isNaN(expDate)) return { kind: 'active', label: 'No expiration' };
  const now = new Date();
  const days = Math.round((expDate - now) / (1000 * 60 * 60 * 24));
  if (days < 0) return { kind: 'expired', label: 'Expired ' + (-days) + 'd ago' };
  if (days <= 90) return { kind: 'expiring', label: 'Expiring in ' + days + 'd' };
  return { kind: 'active', label: 'Active until ' + notes.expires_at };
}

function renderMeta() {
  const el = document.getElementById('meta-section');
  const parts = [];
  // Shipped block (tape destination)
  if (SHIPPED && SHIPPED.tape_root) {
    parts.push(
      '<div class="notes-block">' +
        '<div class="field"><span class="label">On tape at:</span> <code>' + escapeHtml(SHIPPED.tape_root) + '</code></div>' +
        '<div class="field"><span class="label">Shipped:</span> ' + escapeHtml((SHIPPED.shipped_at || '').slice(0, 10)) + '</div>' +
      '</div>'
    );
  } else {
    parts.push(
      '<div class="notes-block" style="border-left-color:var(--muted);">' +
        '<div class="field"><span class="status-badge status-unshipped">Not yet on tape</span></div>' +
      '</div>'
    );
  }
  // Notes block
  if (NOTES && Object.keys(NOTES).length > 0) {
    const lines = [];
    if (NOTES.description) lines.push('<div class="field"><span class="label">Description:</span> ' + escapeHtml(NOTES.description) + '</div>');
    if (NOTES.pi) lines.push('<div class="field"><span class="label">PI:</span> ' + escapeHtml(NOTES.pi) + '</div>');
    if (NOTES.contact) lines.push('<div class="field"><span class="label">Contact:</span> ' + escapeHtml(NOTES.contact) + '</div>');
    if (NOTES.tags && NOTES.tags.length) {
      lines.push('<div class="field"><span class="label">Tags:</span> ' +
        NOTES.tags.map(t => '<span class="tag">' + escapeHtml(t) + '</span>').join('') +
        '</div>');
    }
    const st = expirationStatus(NOTES);
    if (NOTES.expires_at) {
      lines.push('<div class="field"><span class="label">Status:</span> <span class="status-badge status-' + st.kind + '">' + escapeHtml(st.label) + '</span></div>');
      if (NOTES.expiration_note) {
        lines.push('<div class="field"><span class="label">Note:</span> ' + escapeHtml(NOTES.expiration_note) + '</div>');
      }
    }
    parts.push('<div class="notes-block">' + lines.join('') + '</div>');
  }
  el.innerHTML = parts.join('');
}

// ---------- edit-notes modal (collection OR per-archive) ----------
function openModal(target) {
  // target: { kind: 'collection' } OR { kind: 'archive', name: '<name>' }
  editTarget = target || { kind: 'collection' };
  const data = editTarget.kind === 'collection' ? NOTES : archiveNotes(editTarget.name);
  document.getElementById('n-description').value = data.description || '';
  document.getElementById('n-pi').value = data.pi || '';
  document.getElementById('n-contact').value = data.contact || '';
  document.getElementById('n-tags').value = (data.tags || []).join(', ');
  document.getElementById('n-expires').value = data.expires_at || '';
  document.getElementById('n-expnote').value = data.expiration_note || '';
  // Collection has PI/contact fields; per-archive notes don't.
  const collectionOnly = document.getElementById('collection-only-fields');
  collectionOnly.style.display = editTarget.kind === 'collection' ? '' : 'none';
  document.getElementById('modal-title').textContent =
    editTarget.kind === 'collection'
      ? 'Edit collection notes'
      : 'Edit notes for ' + editTarget.name;
  document.getElementById('save-hint').style.display = 'none';
  document.getElementById('modal').classList.add('show');
}
function closeModal() { document.getElementById('modal').classList.remove('show'); }

function collectNotesFromForm() {
  const tagsRaw = document.getElementById('n-tags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(/[,;]+/).map(t => t.trim()).filter(Boolean) : [];
  const out = {};
  const desc = document.getElementById('n-description').value.trim();
  const exp  = document.getElementById('n-expires').value;
  const expn = document.getElementById('n-expnote').value.trim();
  if (desc) out.description = desc;
  if (editTarget.kind === 'collection') {
    const pi = document.getElementById('n-pi').value.trim();
    const ct = document.getElementById('n-contact').value.trim();
    if (pi) out.pi = pi;
    if (ct) out.contact = ct;
  }
  if (tags.length) out.tags = tags;
  if (exp) out.expires_at = exp;
  if (expn) out.expiration_note = expn;
  return out;
}

function isServed() {
  return location.protocol === 'http:' || location.protocol === 'https:';
}

async function refreshFromServer() {
  // When the catalog is served via `tape-archive serve`, prefer the sidecar
  // JSON files freshly read from disk over the (possibly stale) baked-in copies.
  if (!isServed()) return;
  try {
    const [nResp, sResp] = await Promise.all([
      fetch('notes.json', { cache: 'no-store' }).catch(() => null),
      fetch('shipped.json', { cache: 'no-store' }).catch(() => null),
    ]);
    if (nResp && nResp.ok) NOTES = await nResp.json();
    if (sResp && sResp.ok) Object.assign(SHIPPED, await sResp.json());
  } catch (e) { console.warn('refreshFromServer:', e); }
}

async function trySaveViaServer(mergedNotes) {
  if (!isServed()) return false;
  // The collection's directory is the URL path without the trailing HTML file.
  const collectionPath = location.pathname.replace(/\/[^/]+\.html?$/i, '/');
  try {
    const resp = await fetch('/api/save-notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ collection_path: collectionPath, notes: mergedNotes }),
    });
    if (resp.ok) return true;
    const text = await resp.text();
    console.warn('server save responded', resp.status, text);
  } catch (e) { console.warn('server save failed:', e); }
  return false;
}

async function saveNotes() {
  const newData = collectNotesFromForm();
  let mergedNotes;
  if (editTarget.kind === 'collection') {
    mergedNotes = { ...newData };
    if (NOTES.archives) mergedNotes.archives = NOTES.archives;
  } else {
    mergedNotes = { ...NOTES };
    if (!mergedNotes.archives) mergedNotes.archives = {};
    mergedNotes.archives = { ...mergedNotes.archives };
    if (Object.keys(newData).length === 0) {
      delete mergedNotes.archives[editTarget.name];
    } else {
      mergedNotes.archives[editTarget.name] = newData;
    }
  }
  const hint = document.getElementById('save-hint');
  hint.style.display = 'block';

  // 1) Best path: when served via `tape-archive serve`, POST directly. The
  //    server writes notes.json and re-renders the catalog HTML, so reloads
  //    (HTTP or file://) immediately show the new content.
  if (await trySaveViaServer(mergedNotes)) {
    NOTES = mergedNotes;
    renderMeta(); renderArchivesMain();
    hint.textContent = '✓ Saved.';
    setTimeout(closeModal, 800);
    return;
  }

  const json = JSON.stringify(mergedNotes, null, 2);

  // 2) FSA (Chromium browsers).
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: 'notes.json',
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(json);
      await writable.close();
      NOTES = mergedNotes;
      renderMeta();
      renderArchivesMain();
      hint.textContent = 'Saved directly to disk. The catalog will reflect this on the next regen.';
      setTimeout(closeModal, 1200);
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') { hint.textContent = 'Save cancelled.'; return; }
      console.warn('FSA save failed, falling back to download:', e);
    }
  }

  // Fallback: download the file.
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'notes.json';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  NOTES = mergedNotes;
  renderMeta();
  renderArchivesMain();
  hint.innerHTML =
    "Downloaded notes.json. Save it as <code>notes.json</code> in this collection's folder on HIVE " +
    "(next to summary.json), then ask the operator to re-run <code>tape-archive index</code>.";
}

// ---------- click-to-copy restore commands ----------
function setupClipboard() {
  document.body.addEventListener('click', async (e) => {
    const btn = e.target.closest('.copy[data-cmd]');
    if (!btn) return;
    const cmd = btn.dataset.cmd;
    try {
      await navigator.clipboard.writeText(cmd);
      const orig = btn.textContent;
      btn.classList.add('copied');
      btn.textContent = '✓ copied';
      setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1500);
    } catch (err) {
      // Fallback: select-to-clipboard via textarea
      const ta = document.createElement('textarea');
      ta.value = cmd; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (_) {}
      ta.remove();
    }
  });
}

function updateServeStatus() {
  const el = document.getElementById('serve-status');
  if (!el) return;
  if (isServed()) {
    el.innerHTML = '<span style="color:#1a6c3b">●</span> connected to <code>tape-archive serve</code> — saves write directly';
  } else {
    el.innerHTML = '<span style="color:#c93">●</span> opened via <code>file://</code> — saves will prompt (run <code>tape-archive serve</code> for direct writes)';
  }
}

async function init() {
  await refreshFromServer();
  updateServeStatus();
  renderMeta();
  renderArchivesMain();
  setupClipboard();
  document.getElementById('edit-notes-btn').addEventListener('click',
    () => openModal({ kind: 'collection' }));
  document.getElementById('save-notes-btn').addEventListener('click', saveNotes);
  document.getElementById('cancel-notes-btn').addEventListener('click', closeModal);
  document.getElementById('modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeModal();
  });
  const f = document.getElementById('filter');
  let t;
  f.addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      filterText = f.value.trim().toLowerCase();
      renderArchivesMain();
    }, 200);
  });
}
document.addEventListener('DOMContentLoaded', init);
</script>
</body></html>
"""
