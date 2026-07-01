# tape-archive

**A lightweight, transparent pipeline for archiving lab data to LTO tape, with
a durable browsable catalog that outlives the data on tape.**

Turns terabytes of experimental data into verifiable, restorable, indexed
archives — with the metadata, provenance, and browsing experience needed by
biologists who won't touch tape directly.

Built at EPFL to move O(10–100) TB of microscopy data from the RCP NAS onto
SCITAS tape, keeping a queryable "what's where, and what does it mean" record
online forever.

---

## The problem it solves

Lab data ends up on tape when the NAS or project storage fills up. The
mechanics are well-understood (compress, verify, ship, verify again). The
gaps are elsewhere:

- **Discoverability**: once data is on tape, biologists can't `ls` it. They
  need a browsable catalog that describes what was archived and where each
  file went.
- **Provenance**: SHA-256 at every layer (per file, per archive, at every
  transfer hop) so any silent corruption is caught, and every file can be
  cryptographically matched to its pre-archive state years later.
- **Metadata**: PI, experimental context, expiration policies, per-archive
  notes — the things a data manager needs but that don't live in the files
  themselves.
- **A restore command the PI can copy-paste**: no need to remember tape
  paths, tar flags, or decompression order.
- **No exotic infrastructure**: everything is standard `tar` + `zstd` + a
  small Python CLI. Any Unix machine ten years from now can read the archives
  without this tool installed.

---

## Lifecycle at a glance

```text
   ┌────────────┐    ┌──────────┐         ┌───────────────┐         ┌──────────────┐
   │  jetraw    │    │  NAS     │  read → │  local server │  push → │  SCITAS jed  │
   │  download  │ ── │          │         │  (HIVE)       │         │              │
   └────────────┘    └──────────┘         │               │         │  /work       │
                                          │  compress     │         │      ↓ ship  │
                                          │  → .tar files │         │  /archive    │
                                          │  → catalog    │         │   (LTO tape) │
                                          └───────────────┘         └──────────────┘
                                                │
                              browsable HTML ───┴────────→   PI / biologists
                              (notes, filter,                (never touch tape)
                              restore commands)
```

**Two independent artefacts leave every archiving run:**

1. **`.tar` files on tape** — each contains individually zstd-compressed
   files (one `.zst` per source file inside the tar) plus a `_MANIFEST.json`
   with every file's SHA-256. Even without this tool, any modern Unix can
   restore the data with `tar -xf` + `zstd -d`.
2. **A catalog directory on HIVE** — a set of small JSON manifests + a
   self-contained HTML site. Browsable via file share, mounted disk, or a
   tiny local HTTP server. Persists after the tape copy is the only data
   copy that exists.

---

## Key capabilities

- **Interactive planning**: point the CLI at a source folder, get a
  single-file HTML planner. Tick the folders you want as archive roots,
  click Download → a `plan.yaml` describes the archives to build. No YAML
  editing by hand for the common case.
- **Per-file compression**: each source file is zstd-compressed
  independently, then bundled in a plain `tar`. This means partial restore
  of one file doesn't require decompressing neighbours; parallelism scales
  linearly at build time; per-file integrity is verifiable without
  reconstructing the whole archive.
- **SHA-256 at three layers**: (a) every source file, (b) every `.tar`
  archive, (c) verified at every hop (local disk → NAS → SCITAS `/work` →
  `/archive`). Manifests are stored both on disk (in the catalog) and inside
  the tar itself, so either can rebuild the other.
- **Adaptive parallelism**: `--parallel N` compresses N archives
  concurrently. Measured ~2× wall-clock speedup at N=4 on typical
  microscopy data.
- **Resumable everywhere**: `compress`, `ship`, `restore` all skip
  already-complete work on re-run. Interruption is safe.
- **Browsable HTML catalog** (see next section) with notes, per-archive
  restore commands, expiration tracking, filter search, and a local HTTP
  server for zero-prompt notes editing.
- **Deployment surface: negligible**: no database, no service dependencies,
  no exotic packages. A single Python package with only `PyYAML` and
  `zstandard` as runtime deps. The catalog is a static HTML tree that any
  webserver can host.

---

## What users see (the browsable catalog)

Every archived collection produces a static HTML tree on disk. The PI opens
the master index (`index.html`) and gets:

```text
Lab archives
├── 💡 How does this work?  (built-in help panel)
├── Filter tabs: [All] [Active] [Expiring ≤ 90d] [Expired] [Not on tape]
├── Search box (name, path, PI, tag)
└── One card per collection:
      • name, description (from notes), PI, tags
      • total size, archive count, file count, compression ratio
      • tape destination path (📼)
      • status badge (green/amber/red)
      • click → per-collection catalog
```

Inside a collection:

```text
Ece-thesis-movies
├── 💡 How does this work?
├── Header meta: source path, tape path, shipped-at date, collection notes
├── [✎ Edit notes] button (collection-level)
├── Filter: search by filename, path, or SHA-256
└── Archive tree (reconstructed from `__`-separated names):
      📁 group1/
        ▸ group1__project_a.tar   [5.3 GB, 134 files, ratio 39.8 %]
                                  [📋 copy restore]  [✎ notes]
                                  ├── per-archive notes if any
                                  ├── sha256: <full>
                                  └── (lazy-rendered file tree)
                                         └── per-file: name, size, sha256
        ▸ group1__project_b.tar   [...]
      📁 group2/sub/
        ▸ group2__sub__data.tar   [...]
      ▸ flat_archive.tar          (flat names sit at root)
```

- Click any 📋 button → the exact `cp + tape-archive restore` command lands
  on the clipboard. Paste into a terminal with `/archive` mounted, done.
- Click ✎ → modal for notes editing. Two levels: collection-wide (PI,
  contact, tags, expiration) and per-archive (description, tags,
  expiration).
- Notes save either **directly to disk** (when hosted by
  `tape-archive serve`), via the File System Access API (Chromium
  browsers), or as a download-and-place file (any browser). Same
  `notes.json` schema, three save paths, automatic fallback.
- Lazy DOM: 36 000 files across 29 archives → the page opens instantly and
  builds nodes only on expand. Filter operates across all archives at once.

---

## Integrity story

Every byte written on tape can be traced back to its source, and vice
versa:

| Level                        | What's hashed                          | Where the hash lives                                 |
| ---------------------------- | -------------------------------------- | ---------------------------------------------------- |
| Per source file              | SHA-256 of original uncompressed bytes | `manifests/<archive>.json` + `_MANIFEST.json` in tar |
| Per compressed frame         | zstd's built-in frame CRC32            | Inside each `.zst` entry (verified by `zstd -d`)     |
| Per `.tar` archive           | SHA-256 of the whole tar               | `manifests/<archive>.json`                           |
| Verified at compress-time    | on-disk output                         | `tape-archive verify <out>` (mandatory)              |
| Verified during ship         | `/work` copy, then `/archive` copy     | `tape-archive ship` (both checks automatic)          |
| Verified during restore      | every decompressed file's SHA-256      | `tape-archive restore` (compares to bundled manifest)|
| Bit-rot detection on tape    | archive-level SHA-256                  | `tape-archive verify --archives /tape/ ...`          |

Bit rot at any hop is detected, named (which file / which archive), and
localised (compressed frame CRC → manifest per-file SHA-256 → archive-level
SHA-256).

The `_MANIFEST.json` bundled inside every `.tar` means that even if the
disk catalog is lost, restoring one archive and reading its manifest tells
you what's in it and lets you verify every file. **Belt and suspenders by
design.**

---

## Standard components (no vendor lock-in)

The whole pipeline uses tools that have existed for 20+ years, are
available on every Linux install, and produce output any Unix system can
read without this tool:

| Component | Purpose | Alternative? |
| --------- | ------- | ------------ |
| `tar`     | Bundle files into a single archive | POSIX standard, universal |
| `zstd`    | Per-file compression + CRC | Reads by `zstd -d` on any modern box; falls back to `pip install zstandard` |
| `sha256sum` | Integrity hashing | Standard on all Unix |
| `rclone`  | NAS ↔ HIVE ↔ SCITAS transfers | Widely deployed at EPFL |
| `rsync`   | `/work` → `/archive` transfer | Standard |
| Python 3.10+ | Orchestration only | Nothing binary; readable Python code |

**In 10 years, without this tool installed**: `tar -xf X.tar` on any Unix
gives you a directory of `.zst` files plus the manifest. `zstd -d *.zst`
gives you the original data. The manifest tells you the SHA-256 of each
original file so integrity is provable independently.

---

## The pipeline in one command each

```bash
# 1. Plan interactively (produces a plan.yaml the operator can edit)
tape-archive planner <source-dir> -o planner.html
# → open in a browser, tick archive roots, download plan.yaml

# 2. Compress (parallelizable; resumable)
tape-archive compress plan.yaml -o <out-dir> --zstd-level 3 --parallel 8

# 3. Verify locally (re-hash every tar against its manifest)
tape-archive verify <out-dir>

# 4. Smoke test: restore one archive locally, verify per-file sha256
tape-archive restore <out-dir>/archives/<one>.tar -o <tmp> --parallel 4

# 5. Ship to tape (resumable, verifies at both hops)
tape-archive ship --nas <rclone-path> --work </work> --tape </archive>

# 6. (Aggregate step across many collections)
tape-archive index <catalog-root>          # regenerate master index HTML

# 7. Browsable catalog with direct-save notes (optional local server)
tape-archive serve <catalog-root>           # http://127.0.0.1:8080

# 8. Restore from tape when needed
tape-archive restore <tar-path> -o <dest> --parallel 8
```

Every command is idempotent: safe to re-run on interruption.

---

## Architecture

Three machines currently, plus tape:

```text
┌──────────────────┐     ┌────────────────────┐     ┌───────────────────┐
│  NAS             │     │  Local server /    │     │  SCITAS (jed)     │
│  (sv-nas1)       │     │  HIVE              │     │                   │
│                  │     │                    │     │  /work (20 TB)    │
│  jetraw dumps    │ →  │  tape-archive CLI  │ →  │  /archive (tape)  │
│  raw source data │     │  compress + serve  │     │  ship (rclone     │
│  here            │     │  catalog lives     │     │  + rsync + verify)│
└──────────────────┘     │  here permanently  │     └───────────────────┘
                          │  reachable from    │
                          │  SCITAS via rclone │
                          │  `hive-project02:` │
                          └────────────────────┘
                                    │
                                    └── PI / biologists access
                                        catalog via mounted drive
                                        or HTTP (with `tape-archive serve`)
```

**Nothing is required to run in a particular place** — the CLI works on any
Python 3.10+ machine that can reach the relevant paths. The current
deployment happens to have compress on a Windows workstation because
that's what has HIVE mounted, and ship on SCITAS because that's what has
`/archive` mounted. Neither is inherent to the design.

---

## For a small institutional deployment

The pieces a hosted service would need:

- **A machine with the catalog root mounted** (HIVE or wherever the catalog
  tree lives) — this is what `tape-archive serve` runs on.
- **Optional HTTPS + auth** if the catalog is exposed beyond the lab. The
  built-in server is intentionally minimal (localhost by default, `--bind
  0.0.0.0` for LAN, no auth); a real deployment would front it with nginx
  or a similar reverse proxy with your institution's SSO.
- **~10–50 MB of RAM** while the server runs; latency is dominated by the
  filesystem, not the server itself.
- **No database**. The catalog IS the state, in JSON files on disk. Backup
  is `cp -r` or `rsync`. Recovery is opening the file tree.

The `tape-archive` CLI itself takes zero configuration — everything is in
per-collection `plan.yaml` / `notes.json` files under the catalog root.

---

## Where it goes next: active data management

The same catalog + integrity model applies to **live** research data, not
just tape:

- Same "one HTML per project, with per-file SHA-256 and browsable structure"
  UX.
- Add: watch a source directory, incrementally re-hash on changes, alert on
  drift or accidental deletion.
- Add: tags + expiration + retention policies driven by the `notes.json`
  schema we already have.
- Add: automatic promotion from "active" to "tape-archived" when a
  collection hits expiration age / becomes read-only.

The tape-archive tool is deliberately structured so a sibling
`active-archive` tool can share the catalog format and much of the code
(`scan`, `catalog`, `master`, `serve`, `notes` are all reusable). The
schema of `summary.json` / `notes.json` / manifests is stable and can be
extended without breaking existing catalogs.

---

## Quick install

```bash
git clone <this-repo> && cd tape-archiving
python -m venv .venv && source .venv/bin/activate   # or use conda
pip install -e .
tape-archive --help
```

Dependencies: Python 3.10+, `PyYAML`, `zstandard`. Everything else
(`tar`, `zstd`, `rclone`, `rsync`, `sha256sum`) is discovered on `PATH`.

---

## Documentation

- **[step_by_step.md](step_by_step.md)** — full operational runbook
  (worked example: `Ece-thesis-movies`, ~12 TB, 29 archives).
- **[smoke_test.md](smoke_test.md)** — 4-step per-collection validation
  procedure (verify locally → restore one → ship → verify on tape).

Both are meant to be readable start-to-finish and copy-pasteable.

---

## Repo layout

```text
src/tape_archive/
  cli.py           — top-level argparse dispatcher
  scan.py          — filesystem walker
  planner_ui.py    — interactive plan builder (single-file HTML)
  plan.py          — heuristic plan generator (top / experiment / auto)
  archive_builder.py — per-file zstd + tar bundling, manifests, verify
  compress.py      — legacy per-plan compressor
  ship.py          — rclone/rsync orchestration, /work → /archive
  restore.py       — extract + decompress + verify
  catalog_html.py  — per-collection browsable catalog
  master_html.py   — master index across collections
  serve.py         — local HTTP server for direct notes saves
configs/           — example jetraw + plain plan configs
step_by_step.md    — worked-example runbook
smoke_test.md      — validation procedure
```

Every module is < 500 lines, no framework, only stdlib + zstandard + PyYAML.

---

## Contact

Clément Helsens — clement.helsens@epfl.ch
