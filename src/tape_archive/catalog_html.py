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
    shipped = _load_json(output_dir / "shipped.json")    # may be None
    notes = _load_json(output_dir / "notes.json")        # may be None

    def _js(payload) -> str:
        return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    doc = _HTML
    doc = doc.replace("__TREE_DATA_JSON__", _js(tree))
    doc = doc.replace("__ARCHIVES_JSON__", _js(archives_summary))
    doc = doc.replace("__SHIPPED_JSON__", _js(shipped or {}))
    doc = doc.replace("__NOTES_JSON__", _js(notes or {}))
    doc = doc.replace("__SOURCE_ROOT__", html.escape(source_root))
    doc = doc.replace("__GENERATED_AT__", datetime.now(tz=timezone.utc).isoformat())
    doc = doc.replace("__TOTAL_SOURCE_BYTES__", str(tree["size_subtree"]))
    doc = doc.replace("__TOTAL_FILES__", str(tree["file_count_subtree"]))
    doc = doc.replace("__TOTAL_ARCHIVES__", str(len(manifests)))
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
</style>
</head><body>
<header>
  <h1>tape-archive catalog</h1>
  <p>Source: <code>__SOURCE_ROOT__</code></p>
  <p>Generated: <code>__GENERATED_AT__</code></p>
  <p>__TOTAL_FILES__ files · __TOTAL_SOURCE_BYTES__ source bytes · __TOTAL_ARCHIVES__ archives</p>
  <div id="meta-section"></div>
  <div style="margin-top: 8px;">
    <button class="action" id="edit-notes-btn">✎ Edit notes</button>
  </div>
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

<!-- Edit-notes modal -->
<div class="modal-backdrop" id="modal">
  <div class="modal">
    <h3>Edit notes</h3>
    <p class="hint">These travel with the catalog as <code>notes.json</code>. The PI / operator sees them on the master index and at the top of this catalog.</p>
    <label>Description<textarea id="n-description" rows="3" placeholder="Free-form description"></textarea></label>
    <label>PI<input id="n-pi" type="text" placeholder="e.g. Andy Oates"></label>
    <label>Contact<input id="n-contact" type="text" placeholder="email"></label>
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
const TREE_DATA = __TREE_DATA_JSON__;
const ARCHIVES = __ARCHIVES_JSON__;
const SHIPPED = __SHIPPED_JSON__;
let NOTES = __NOTES_JSON__;

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

function renderTree() {
  const container = document.getElementById('tree');
  container.innerHTML = '';
  // No filter active → lazy: only the root and its direct children's stubs.
  // Filter active → eager-but-pruned: render every subtree that matches.
  const node = filterText ? renderNodeFiltered(TREE_DATA, true)
                          : renderNodeLazy(TREE_DATA, true);
  if (node) container.appendChild(node);
  else container.innerHTML = '<div class="hint">No matches.</div>';
}

function buildSummary(node, isRoot) {
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
    '<span class="sha" title="SHA-256 of original (uncompressed) bytes">' + f.sha256.slice(0, 16) + '…</span>' +
    '<span class="arch-link" data-archive="' + escapeHtml(f.archive) + '">' + escapeHtml(f.archive) + '</span>';
  return li;
}

function renderNodeLazy(node, isRoot) {
  const det = document.createElement('details');
  if (isRoot) det.open = true;
  det.appendChild(buildSummary(node, isRoot));
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
  if (isRoot) {
    expand();
  } else {
    det.addEventListener('toggle', () => { if (det.open) expand(); });
  }
  return det;
}

function renderNodeFiltered(node, isRoot) {
  // Returns <details> only if this subtree has any match; otherwise null.
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
  det.open = true;   // filter mode: always expand matches
  det.appendChild(buildSummary(node, isRoot));
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

function renderArchives() {
  const list = document.getElementById('archives-list');
  list.innerHTML = '';
  const sorted = [...ARCHIVES].sort((a, b) => b.size_bytes - a.size_bytes);
  const tapeRoot = SHIPPED && SHIPPED.tape_root;
  for (const a of sorted) {
    const div = document.createElement('div');
    div.className = 'archive-row';
    div.id = 'arch-' + a.name;
    let restoreBlock = '';
    if (tapeRoot) {
      const tarFull = tapeRoot.replace(/\/$/, '') + '/' + a.tar;
      const restoreCmd =
        'cp ' + tarFull + ' /restore/ && tape-archive restore /restore/' + a.tar +
        ' -o /restore/' + a.name + ' --parallel 4';
      restoreBlock =
        '<div class="arch-meta" style="margin-top:4px;">' +
          '<button class="copy" data-cmd="' + escapeHtml(restoreCmd) + '">📋 copy restore command</button>' +
        '</div>';
    }
    div.innerHTML =
      '<div class="arch-name">' + escapeHtml(a.tar) + '</div>' +
      '<div class="arch-meta">' + fmtSize(a.size_bytes) + ' on tape · ' +
        fmtSize(a.source_bytes) + ' source · ' +
        a.file_count.toLocaleString() + ' files · ' +
        'ratio ' + (a.size_bytes / Math.max(a.source_bytes, 1) * 100).toFixed(1) + '%' +
      '</div>' +
      '<code class="sha">sha256: ' + escapeHtml(a.sha256) + '</code>' +
      restoreBlock;
    list.appendChild(div);
  }
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

// ---------- edit-notes modal: open / populate / save (FSA → download fallback) ----------
function openModal() {
  document.getElementById('n-description').value = NOTES.description || '';
  document.getElementById('n-pi').value = NOTES.pi || '';
  document.getElementById('n-contact').value = NOTES.contact || '';
  document.getElementById('n-tags').value = (NOTES.tags || []).join(', ');
  document.getElementById('n-expires').value = NOTES.expires_at || '';
  document.getElementById('n-expnote').value = NOTES.expiration_note || '';
  document.getElementById('save-hint').style.display = 'none';
  document.getElementById('modal').classList.add('show');
}
function closeModal() { document.getElementById('modal').classList.remove('show'); }
function collectNotes() {
  const tagsRaw = document.getElementById('n-tags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(/[,;]+/).map(t => t.trim()).filter(Boolean) : [];
  const out = {};
  const desc = document.getElementById('n-description').value.trim();
  const pi   = document.getElementById('n-pi').value.trim();
  const ct   = document.getElementById('n-contact').value.trim();
  const exp  = document.getElementById('n-expires').value;
  const expn = document.getElementById('n-expnote').value.trim();
  if (desc) out.description = desc;
  if (pi) out.pi = pi;
  if (ct) out.contact = ct;
  if (tags.length) out.tags = tags;
  if (exp) out.expires_at = exp;
  if (expn) out.expiration_note = expn;
  return out;
}

async function saveNotes() {
  const newNotes = collectNotes();
  const json = JSON.stringify(newNotes, null, 2);
  const hint = document.getElementById('save-hint');
  hint.style.display = 'block';

  // Try the File System Access API first (Chromium browsers).
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: 'notes.json',
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(json);
      await writable.close();
      NOTES = newNotes;
      renderMeta();
      hint.textContent = 'Saved directly to disk. The catalog will reflect this on the next regen.';
      setTimeout(closeModal, 1200);
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') { hint.textContent = 'Save cancelled.'; return; }
      console.warn('FSA save failed, falling back to download:', e);
    }
  }

  // Fallback: download the file. User saves it next to summary.json on HIVE.
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'notes.json';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  NOTES = newNotes;
  renderMeta();
  hint.innerHTML =
    'Downloaded notes.json. Save it as <code>notes.json</code> in this collection\\'s folder on HIVE ' +
    '(next to summary.json), then ask the operator to re-run <code>tape-archive index</code>.';
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
  renderMeta();
  renderTree();
  renderArchives();
  setupArchiveClickHandlers();
  setupClipboard();
  document.getElementById('edit-notes-btn').addEventListener('click', openModal);
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
      filterText = f.value.trim();
      renderTree();
    }, 200);
  });
}
document.addEventListener('DOMContentLoaded', init);
</script>
</body></html>
"""
