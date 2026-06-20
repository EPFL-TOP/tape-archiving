"""Master index page: aggregates every collection's catalog under one root.

Walks an NAS directory containing one subfolder per `tape-archive compress`
run (each with its own catalog.html + manifests/ + summary.json) and emits a
single index.html that links to each.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path


def render_master_index(catalog_root: Path, output_path: Path) -> None:
    catalog_root = Path(catalog_root).resolve()
    collections = _scan_collections(catalog_root)
    collections.sort(key=lambda c: c.get("compressed_at", ""), reverse=True)

    totals = {
        "collection_count": len(collections),
        "archive_count": sum(c.get("archive_count", 0) for c in collections),
        "file_count": sum(c.get("file_count", 0) for c in collections),
        "source_bytes": sum(c.get("source_bytes", 0) for c in collections),
        "tape_bytes": sum(c.get("tape_bytes", 0) for c in collections),
    }

    json_data = json.dumps({"collections": collections, "totals": totals},
                           separators=(",", ":")).replace("</", "<\\/")

    doc = _HTML
    doc = doc.replace("__COLLECTIONS_JSON__", json_data)
    doc = doc.replace("__CATALOG_ROOT__", html.escape(str(catalog_root)))
    doc = doc.replace("__GENERATED_AT__", datetime.now(tz=timezone.utc).isoformat())
    output_path.write_text(doc, encoding="utf-8")


def _scan_collections(catalog_root: Path) -> list[dict]:
    """Walk recursively under catalog_root, picking up every directory that
    contains a catalog.html. The collection's "dir_name" preserves the path
    relative to catalog_root, so nested layouts (e.g. group/project/sub) stay
    distinguishable in the master index.
    """
    out: list[dict] = []
    for catalog_html in sorted(catalog_root.rglob("catalog.html")):
        coll_dir = catalog_html.parent
        # Don't treat the catalog root itself as a collection.
        if coll_dir.resolve() == catalog_root.resolve():
            continue
        summary = _load_or_build_summary(coll_dir)
        if not summary:
            continue
        rel = coll_dir.relative_to(catalog_root).as_posix()
        summary["catalog_url"] = f"{rel}/catalog.html"
        summary["dir_name"] = rel
        out.append(summary)
    return out


def _load_or_build_summary(coll_dir: Path) -> dict | None:
    summary_path = coll_dir / "summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    # Fallback: synthesise from manifests/
    manifests_dir = coll_dir / "manifests"
    if not manifests_dir.is_dir():
        return None
    manifests = []
    for p in sorted(manifests_dir.glob("*.json")):
        try:
            manifests.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    if not manifests:
        return None
    return {
        "name": coll_dir.name,
        "compressed_at": min((m.get("created_at", "") for m in manifests), default=""),
        "source_root": manifests[0].get("source_root", ""),
        "archive_count": len(manifests),
        "file_count": sum(m.get("file_count", 0) for m in manifests),
        "source_bytes": sum(m.get("total_uncompressed_bytes", 0) for m in manifests),
        "tape_bytes": sum(m.get("archive_size_bytes", 0) for m in manifests),
    }


_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lab archives</title>
<style>
:root {
  --fg: #1d2127; --muted: #6b7280; --bg: #fafafa; --card: #fff;
  --border: #e3e5e8; --accent: #0b6; --code: #f0f1f3;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px;
       line-height: 1.4; font-size: 14px; max-width: 1100px; margin: 0 auto; }
h1, h2 { margin-top: 0; }
header, section { background: var(--card); border: 1px solid var(--border);
                  border-radius: 8px; padding: 18px 22px; margin-bottom: 16px; }
code { background: var(--code); padding: 1px 5px; border-radius: 4px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: 0.92em; word-break: break-all; }
.hint { color: var(--muted); font-size: 0.88em; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
         gap: 12px; margin-top: 8px; }
.stats .stat { background: #f5f6f7; border-radius: 6px; padding: 10px 12px; }
.stats .stat .num { font-size: 1.4em; font-weight: 600; }
.stats .stat .label { color: var(--muted); font-size: 0.85em; }

#filter { width: 100%; padding: 10px 12px; border: 1px solid var(--border);
          border-radius: 6px; font-size: 14px; margin-bottom: 16px; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 14px; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px 18px; text-decoration: none; color: var(--fg);
        transition: box-shadow .15s, transform .1s; display: block; }
.card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.08); transform: translateY(-1px); }
.card h3 { margin: 0 0 6px; font-family: ui-monospace, Menlo, monospace;
           font-size: 1.05em; word-break: break-word; color: var(--accent); }
.card .meta { color: var(--muted); font-size: 0.88em; margin-top: 6px; }
.card .source { color: var(--muted); font-size: 0.82em; margin-top: 4px;
                word-break: break-all; }
.card .ratio { font-size: 0.82em; color: var(--muted); }
.empty { color: var(--muted); text-align: center; padding: 40px; }
</style>
</head><body>
<header>
  <h1>Lab archives</h1>
  <p>Catalog root: <code>__CATALOG_ROOT__</code></p>
  <p class="hint">Generated <code>__GENERATED_AT__</code>. Each card opens that collection's browsable catalog.</p>
  <div class="stats" id="totals"></div>
</header>

<input id="filter" type="text" placeholder="Filter by collection name or source path… (case-insensitive)">

<section>
  <div class="grid" id="grid"></div>
</section>

<script>
const DATA = __COLLECTIONS_JSON__;

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
function fmtDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toISOString().slice(0, 10); } catch (e) { return iso.slice(0, 10); }
}

function renderTotals() {
  const t = DATA.totals;
  const el = document.getElementById('totals');
  el.innerHTML = `
    <div class="stat"><div class="num">${t.collection_count}</div><div class="label">collections</div></div>
    <div class="stat"><div class="num">${t.archive_count}</div><div class="label">archives</div></div>
    <div class="stat"><div class="num">${t.file_count.toLocaleString()}</div><div class="label">files</div></div>
    <div class="stat"><div class="num">${fmtSize(t.source_bytes)}</div><div class="label">source</div></div>
    <div class="stat"><div class="num">${fmtSize(t.tape_bytes)}</div><div class="label">on tape</div></div>
  `;
}

function renderGrid() {
  const filter = (document.getElementById('filter').value || '').toLowerCase();
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  const visible = DATA.collections.filter(c =>
    !filter ||
    (c.name || c.dir_name || '').toLowerCase().includes(filter) ||
    (c.source_root || '').toLowerCase().includes(filter)
  );
  if (visible.length === 0) {
    grid.innerHTML = '<div class="empty">No collections match.</div>';
    return;
  }
  for (const c of visible) {
    const name = c.name || c.dir_name || '(unnamed)';
    const ratioStr = (c.source_bytes && c.tape_bytes)
      ? `${(100 * c.tape_bytes / c.source_bytes).toFixed(1)}%`
      : '—';
    const a = document.createElement('a');
    a.className = 'card';
    a.href = c.catalog_url;
    a.innerHTML = `
      <h3>${escapeHtml(name)}</h3>
      <div class="meta">${c.archive_count} archives · ${c.file_count.toLocaleString()} files</div>
      <div class="meta">${fmtSize(c.source_bytes)} → ${fmtSize(c.tape_bytes)} <span class="ratio">(${ratioStr})</span></div>
      <div class="meta">Compressed ${fmtDate(c.compressed_at)}</div>
      <div class="source">${escapeHtml(c.source_root || '')}</div>
    `;
    grid.appendChild(a);
  }
}

let t;
document.getElementById('filter').addEventListener('input', () => {
  clearTimeout(t);
  t = setTimeout(renderGrid, 150);
});
document.addEventListener('DOMContentLoaded', () => {
  renderTotals();
  renderGrid();
});
</script>
</body></html>
"""
