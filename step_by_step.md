# tape-archive runbook — `Ece-thesis-paper` worked example

End-to-end procedure for archiving one collection from jetraw to LTO tape.
Concrete paths below use `Ece-thesis-paper` as the running example — substitute
your own collection name where you see it.

## The machines

```text
   ┌───────────────────────────────────────┐       ┌──────────────────────────┐
   │  Local server / HIVE                  │       │  SCITAS (jed)            │
   │                                       │       │                          │
   │  - tape-archive CLI                   │       │  - tape-archive CLI      │
   │  - mounts NAS read-only (for source)  │       │  - /work (20 TB quota)   │
   │  - compress output stays here         │ ───▶  │  - /archive (tape)       │
   │  - catalog (manifests + html) lives   │       │                          │
   │    here forever                       │       │  rclone hive → /work     │
   │                                       │       │  rsync  /work → /archive │
   │  reachable from SCITAS as the         │       │                          │
   │  rclone remote 'hive-project02:'      │       │                          │
   └───────────────────────────────────────┘       └──────────────────────────┘
                  │
                  └── biologists open
                      catalog/index.html on HIVE
                      (mounted as G:\PROJECTS-02\... or similar)
                      to find files and look up archive names
```

**Data path**: jetraw → NAS source dir → local server (read via NAS mount) →
HIVE scratch (compressed archives + catalog, durable) → SCITAS /work → tape.

The NAS is **only the source of raw jetraw data**. Everything downstream
lives on HIVE — no NAS round-trip after compress. SCITAS pulls archives
directly from the HIVE rclone remote.

**Source of truth for "what's on tape"**: the per-archive `_MANIFEST.json`
inside each `.tar` (file-level sha256s) + the on-disk `manifests/*.json` on the
HIVE catalog (with archive-level sha256s).

---

## One-time setup

### On the local server

```bash
# Mount the NAS read-only somewhere stable. SMB or NFS, whichever your server
# supports. Example:
sudo mount -t cifs -o ro,credentials=/etc/.smbcreds-upoates \
  //sv-nas1.rcp.epfl.ch/upoates  /mnt/nas/upoates

# Install tape-archive in a Python env that has the GLIBCXX libstdcxx >= 12
# (only matters if you'll run jr here — otherwise plain Python 3.10+ works):
conda create -n tape-archive python=3.12 pip -y
conda activate tape-archive
cd ~/tape-archiving         # wherever the repo is
pip install -e .
tape-archive --help
```

### On SCITAS

```bash
# rclone module + remote already configured (see legacy section if not):
module load rclone
rclone lsd nas_rcp:upoates/common         # smoke test

# tape-archive in the same conda env where you've already fixed the GLIBCXX
# issue and pinned LD_LIBRARY_PATH:
conda activate jetraw
cd ~/tape-archiving
pip install -e .
tape-archive --help
```

### Directory conventions

**NAS** — only stores the raw jetraw source:

```text
nas_rcp:upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE/
  Ece-thesis-paper/        ← one source collection (jetraw deposits here)
    …
```

**HIVE (local server, also reachable from SCITAS as `hive-project02:`)** —
holds the compress output, becomes the durable catalog after archives ship to tape:

```text
G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\        ← Windows view
hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/   ← rclone view
  Ece-thesis-movies/                                 ← one collection
    catalog.html
    summary.json
    plan.yaml
    manifests/
    archives/                ← removed after archives are confirmed on tape
  index.html                 ← master page across all collections
```

The same path is reachable two ways:

- From the local Windows server: `G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies\`
- From SCITAS via rclone: `hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/Ece-thesis-movies`

---

## One collection, end-to-end: `Ece-thesis-movies`

The full lifecycle for one collection, using the same paths you've been
using. Every step is resumable and idempotent — interrupted runs can be
restarted with the same command.

### 0. jetraw downloads to NAS

You handle this. End state: source files live at
`nas_rcp:upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE/Ece-thesis-movies/`.

### 1. Plan — local Windows server

```cmd
:: on the local Windows server, in the conda env:
conda activate jetraw

tape-archive planner ^
  Z:\common\UPOATES_DATA_ARCHIVES\TOTAPE\Ece-thesis-movies ^
  -o planner_ECE.html
```

Open `planner_ECE.html` in a browser:

1. Tick the folders you want as archive roots (per-experiment dirs are the
   natural choice — produces one `.tar` per experiment).
2. Edit names if needed.
3. Click **Download plan.yaml**.
4. Rename the downloaded file: `move %USERPROFILE%\Downloads\plan.yaml plan_ECE.yaml`.

### 2. Compress — local Windows server

Long-running (~24 h for 12 TB at `--zstd-level 3`); use a separate cmd window
or run with `start /B` so you can keep working.

```cmd
tape-archive compress plan_ECE.yaml ^
  -o G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies ^
  --zstd-level 3 ^
  --parallel 8 ^
  -v
```

- `--zstd-level 3` is locked in — measured 3.4× ratio; zstd 9 buys 1.8 %
  extra for 4.4× the CPU on this data.
- `--parallel 8` compresses 8 archives concurrently. Tune based on cores
  available and NAS read throughput.
- Resumable: re-running skips archives whose manifest already exists. Pass
  `--force --archive <name>` to rebuild a specific one.
- The temp file used during compression now sits on the same volume as
  `-o`, so disk-full errors will be on the output volume — easy to spot.

Output under `G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies\`:

```text
plan.yaml
summary.json
catalog.html
archives\*.tar             ← removed after ship+verify (step 7)
manifests\*.json           ← stays forever; the on-disk catalog
```

### 3. Verify locally — local Windows server

Re-hash every `.tar` and compare against its manifest's `archive_sha256`.
Cheap (just disk reads), catches anything botched during compression.

```cmd
tape-archive verify G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies
```

Expect: `checked N archive(s), 0 failure(s)`. If anything fails, rebuild
just that archive:

```cmd
tape-archive compress plan_ECE.yaml ^
  -o G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies ^
  --force --archive <name-of-failed-archive> --zstd-level 3 -v
```

Then re-run verify until clean.

### 4. Smoke-test one archive end-to-end — local Windows server

`verify` checks the .tar's outer sha256; this step actually decompresses one
archive and verifies the per-file sha256s. If this passes on one archive,
every other archive built by the same compress run is trustworthy.

Pick a mid-sized one (small enough to finish in a few minutes):

```cmd
tape-archive restore ^
  G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies\archives\210131ablation.tar ^
  -o G:\TMP\smoke-test ^
  --parallel 4 -v

:: cleanup after:
rmdir /S /Q G:\TMP\smoke-test
```

Pass means: tar opens, every `.zst` decompresses cleanly (zstd frame CRC),
and every decompressed file's sha256 matches the manifest. Exit code 0 +
`failed=0`. If anything fails, the offending file is named and you can dig in.

### 5. Ship to tape — SCITAS

The compress output already sits on HIVE at the same path SCITAS reaches via
the `hive-project02:` rclone remote. `ship` pulls each archive into `/work`,
verifies on /work, rsyncs to `/archive`, verifies on /archive, deletes the
/work copy. All automatic.

```bash
# on SCITAS, in tmux (this can run for hours):
tmux new -s ship-ECE
conda activate jetraw
module load rclone

tape-archive ship \
  --nas  hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/Ece-thesis-movies \
  --work /work/upoates/ship/Ece-thesis-movies \
  --tape /archive/upoates/lab-archives/Ece-thesis-movies \
  --batch-budget-gb 17000 \
  -v
```

(The flag is called `--nas` for historical reasons but accepts any rclone
remote path — here it points at HIVE.)

`--batch-budget-gb 17000` refuses to start an archive that would push
`/work` past 17 TB (defensive cap under the 20 TB quota). Resumable:
interrupt anytime, re-run — archives already on tape with the right sha256
are skipped.

When ship completes cleanly (no failures), it writes a `shipped.json` back
to HIVE via rclone — recording the tape path, timestamp, and host. The
catalog and master index will pick this up at next regen / next reload.

### 6. (One-off) Backfill `shipped.json` if you shipped before this feature

If a collection was shipped before `tape-archive ship` learned to write
`shipped.json` (Ece's case during early testing), do it manually once:

```cmd
:: on the local Windows server:
tape-archive mark-shipped ^
  G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies ^
  --tape /archive/upoates/lab-archives/Ece-thesis-movies
```

Writes `shipped.json` directly into the collection's HIVE folder. Skip this
step for any future collection — `ship` does it automatically.

### 7. Re-verify on tape side (optional belt-and-suspenders)

`ship` already verifies on /archive before deleting from /work, but if you
want a paper trail or some time has passed:

```bash
# on SCITAS:
tape-archive verify \
  --archives /archive/upoates/lab-archives/Ece-thesis-movies \
  --manifests hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/Ece-thesis-movies/manifests
```

(The manifests dir can be on rclone — pull a thin copy first or run from a
host that has HIVE mounted directly.)

### 8. Clean the HIVE archives — local Windows server

Once `ship` and the tape verify both pass, the HIVE copy of `archives/` is
redundant — tape is the home. Delete it; keep everything else (the catalog:
`manifests/` + `catalog.html` + `summary.json` + `plan.yaml` + `shipped.json`).

```cmd
:: from the local Windows server:
rmdir /S /Q G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies\archives
```

`ship` doesn't do this automatically — by design, so you can personally
confirm the tape ingest before letting the HIVE copy go.

### 9. Regenerate the master index — local Windows server

```cmd
tape-archive index G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS ^
  -o G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\index.html
```

This walks the tree recursively (so nested layouts work too), aggregates
each collection's `summary.json` + `shipped.json` + `notes.json` into the
master `index.html`, and rewrites each per-collection `catalog.html` with
the right depth-aware `← Back to lab archives index` link.

### 10. Browse & annotate via `tape-archive serve` — local Windows server

For zero-prompt note editing from the browser, run the local HTTP server
and open the URL it prints (do NOT double-click the .html file from
Explorer — that opens via `file://` which can't save directly):

```cmd
tape-archive serve G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS
:: -> http://127.0.0.1:8080/index.html
```

Open `http://127.0.0.1:8080/index.html` in any browser. At the top of every
page you'll see one of:

- 🟢 **connected to `tape-archive serve` — saves write directly**
- 🟠 opened via `file://` — saves will prompt (run serve for direct writes)

When 🟢:

1. Click `Ece-thesis-movies` card → opens that collection's catalog.
2. Click **✎ Edit notes** in the header (collection-level) → modal with
   description, PI, contact, tags, expires_at fields → **Save**.
3. Click any archive's `▸` to expand it; click its **✎ notes** button for
   archive-specific description and tags → **Save**.

Every save:

- Writes `notes.json` straight to the right HIVE folder
- Re-renders the catalog HTML so reloads (HTTP or file://) show the changes
- The master index also picks up changes via `fetch` at next reload

`Ctrl-C` the serve terminal when you're done. For permanent PI access from
their own workstation, swap `--bind 0.0.0.0` and open your chosen port in
the firewall.

---

## Next collection

Same procedure with a new `NAME`, new plan. Step 9 (the master index
regen) picks up the new card automatically; no other config changes needed.

---

## Larger datasets with subfolders (multi-plan workflow)

`Ece-thesis-paper` is the easy case: one flat source dir, 29 archives all at
depth 1, one plan. Larger datasets often have hierarchy — e.g.
`arianne` with multiple top-level subgroups, each containing its own
experiments at varying depths. For those, run **one plan per logical
subgroup**, and keep the hierarchy on HIVE/tape mirroring the source.

### Example: `arianne` with three subgroups

```text
G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\
├── group1\
│   ├── project_a\         (~6 TB, ~12 experiment dirs)
│   └── project_b\         (~8 TB)
├── group2\
│   └── sub\data\          (~15 TB, deeply nested)
└── group3\                (~3 TB, flat)
```

50 TB total. Run **one planner + plan + compress + ship cycle per top-level
subgroup**. Don't try to plan the whole 50 TB in one HTML — the planner page
embeds the full file tree as JSON and gets sluggish past a few million entries.

### One iteration, for `arianne/group1/project_a`

```bash
# 1. plan (on the local Windows server)
SUBROOT=G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a
tape-archive planner "$SUBROOT" -o planner_arianne_group1_project_a.html
# → open in browser, pick archive roots, Download → plan_arianne_group1_project_a.yaml

# 2. compress — output lands on HIVE next to the source, mirroring the path
tape-archive compress plan_arianne_group1_project_a.yaml \
  --zstd-level 3 --parallel 4 -v \
  -o G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a

# 3. on SCITAS, ship directly from HIVE — paths mirror each other
tape-archive ship \
  --nas  hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/arianne/group1/project_a \
  --work /work/upoates/ship/arianne/group1/project_a \
  --tape /archive/upoates/lab-archives/arianne/group1/project_a \
  --batch-budget-gb 17000 -v

# 4. delete the now-redundant archives/ on HIVE (catalog stays)
rmdir /S /Q G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a\archives
```

Repeat for `arianne/group1/project_b`, `arianne/group2/sub/data`, `arianne/group3`, etc.

### Final layout you end up with

HIVE (browsable catalog — no tars after cleanup):

```text
G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\
├── index.html                      ← master page (recursive, sees everything below)
├── Ece-thesis-movies\              (flat)
│   ├── catalog.html
│   └── manifests\, plan.yaml, summary.json
├── arianne\
│   ├── group1\
│   │   ├── project_a\
│   │   │   └── catalog.html ...
│   │   └── project_b\
│   │       └── catalog.html ...
│   ├── group2\sub\data\
│   │   └── catalog.html ...
│   └── group3\
│       └── catalog.html ...
└── ...
```

Tape (parallel structure, just the tars):

```text
/archive/upoates/lab-archives/
├── Ece-thesis-movies/*.tar
└── arianne/
    ├── group1/project_a/*.tar
    ├── group1/project_b/*.tar
    ├── group2/sub/data/*.tar
    └── group3/*.tar
```

The master `index.html` on HIVE will pick up **every** collection no matter
how deep, because `tape-archive index` walks recursively. Each card shows the
full path relative to `TMP-ARCHIVE-TO_SCITAS\`, so biologists see at a glance
which subgroup a collection belongs to.

### Batch tip — script the loop

Once you've generated all the plan YAMLs for one dataset, the
compress + ship cycle is mechanical.

On the local server (compress every plan):

```bash
for plan in plan_arianne_*.yaml; do
  # derive the path under TMP-ARCHIVE-TO_SCITAS from the plan filename
  NAME=${plan#plan_}; NAME=${NAME%.yaml}
  REL=$(echo "$NAME" | tr '_' '/')         # adjust if your naming differs
  tape-archive compress "$plan" \
    -o "G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\${REL}" \
    --zstd-level 3 --parallel 4 -v
done
```

On SCITAS (ship every collection):

```bash
for REL in arianne/group1/project_a arianne/group1/project_b arianne/group2/sub/data arianne/group3; do
  tape-archive ship \
    --nas  "hive-project02:PROJECTS-02/Clement/TMP-ARCHIVE-TO_SCITAS/${REL}" \
    --work "/work/upoates/ship/${REL}" \
    --tape "/archive/upoates/lab-archives/${REL}" \
    --batch-budget-gb 17000 -v
done
```

And on SCITAS, a matching loop calling `tape-archive ship` for each
collection. Both loops are resumable — compress and ship skip what's already
done.

---

## Restore (when you pull from tape)

`tape-archive restore` closes the loop: extract one `.tar`, decompress each
per-file `.zst`, and verify the SHA-256 of every original byte against the
bundled manifest. Single read pass per file.

```bash
# Pull a tar from tape to a working directory:
cp /archive/upoates/lab-archives/Ece-thesis-paper/210131ablation.tar /restore/

# Full restore + verify:
tape-archive restore /restore/210131ablation.tar -o /restore/210131ablation/ \
  --parallel 8 -v

# Exit 0 = every file decompressed and matches its manifest sha256.
# Exit 1 = at least one file failed; the FAIL lines name which.
```

What you get in `/restore/210131ablation/`:
- The original directory tree with all files restored to their pre-archive bytes
- The bundled `_MANIFEST.json` (kept; useful for future re-verification)
- (No `.zst` files by default — they're removed once their sibling decompressed file passes verification)

### Useful flags

```bash
# Cheap inspection: just lay out the .zst tree, don't decompress yet:
tape-archive restore archive.tar -o /restore/ --no-decompress

# Decompress but keep .zst sidecars (re-verifiable later, costs ~2x disk):
tape-archive restore archive.tar -o /restore/ --keep-compressed

# Resume an interrupted restore (skip files already present and correct):
tape-archive restore archive.tar -o /restore/ --skip-existing

# External manifest (e.g., from the NAS catalog) instead of the bundled one:
tape-archive restore archive.tar -o /restore/ \
  --manifest /nas/lab-archives-catalog/Ece-thesis-paper/manifests/210131ablation.json

# Skip verification (size-only check; faster but no integrity guarantee):
tape-archive restore archive.tar -o /restore/ --no-verify
```

### What catches corruption

Two independent layers:

1. **zstd frame CRC** — every `.zst` contains a checksum of the compressed data.
   If a bit flipped during tape read or transfer, `zstd -d` fails with
   `Restored data doesn't match checksum` *before* we even compute the sha256.
   You'll see a `FAIL <path>: decompress failed: ...` line.
2. **SHA-256 against manifest** — even if zstd somehow decompresses cleanly,
   a content mismatch with the manifest's per-file sha256 is reported as
   `sha256 mismatch (<got> != <expected>)`.

Together: any tape bit-rot, transfer corruption, or accidental modification is
caught on the first restore attempt.

---

## Troubleshooting

**`GLIBCXX_3.4.30 not found`** (only when calling `jr`):
```bash
conda install -n jetraw -c conda-forge 'libstdcxx-ng>=12' -y
conda env config vars set LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$LD_LIBRARY_PATH" -n jetraw
conda deactivate && conda activate jetraw
```

**`git pull` fails inside the jetraw env** with `libldap.so.2: undefined symbol: EVP_md2`:
The env's modern OpenSSL clashes with system libldap. One-shot fix:
`LD_LIBRARY_PATH= git pull`. Permanent fix: `conda install -n jetraw -c conda-forge git -y`.

**Editable install fails with `setup.py not found`**:
Your env doesn't have its own pip. Either reuse the `jetraw` env, or recreate
with Python: `conda create -n tape-archive python=3.12 pip -y`.

**rclone hangs or is glacial on a mount**:
Don't use `rclone mount` for the read phase — go through a real OS-level mount
(SMB / NFS / FUSE-via-`mount.cifs`). `rclone copy` for ship is fine because
each archive is one chunk at a time.

---

## Annotations, tape destination, expirations

Two optional sidecar JSON files live next to each collection's `summary.json`:

- **`shipped.json`** — written automatically by `ship` after a clean run.
  Records `tape_root`, `shipped_at`, `host`, `archive_count`. The catalog and
  master index pick it up and show "📼 on tape at …" + the shipped date.
- **`notes.json`** — operator/PI-supplied. Holds `description`, `pi`, `contact`,
  `tags[]`, `expires_at` (YYYY-MM-DD), `expiration_note`. All fields optional.

### Adding notes from the browser (no CLI needed)

**Recommended: run `tape-archive serve` once, get direct saves.**

Open a terminal on any machine that can write to HIVE (the local Windows
server is the obvious choice — that's where you ran compress):

```cmd
tape-archive serve G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS
```

The command stays running, hosting the catalog on `http://127.0.0.1:8080`.
Open that URL in any browser → the master index loads → click any
collection → edit notes (✎ at the header for collection notes, ✎ at any
archive header for per-archive notes) → **Save**.

What happens on Save: the browser POSTs the new `notes.json` straight to
the server. The server writes the file to disk AND re-renders that
collection's `catalog.html` with the updated content, so even later visits
via `file://` see the change. No file picker, no download.

`Ctrl-C` to stop the server when you're done.

To let the PI access this from their own machine, swap `--bind 0.0.0.0` and
open the firewall to your chosen port. Anyone on the LAN can then point
their browser at `http://<your-hostname>:8080/`.

**Without `serve`: it still works**, via the same form. Save behaviour
falls back to:

- **Chrome / Edge** (File System Access API): browser prompts for a file
  location once, writes `notes.json` directly.
- **Firefox / Safari**: downloads `notes.json`. Drop it next to
  `summary.json` in the collection's folder on HIVE, then regenerate:
  `tape-archive catalog <collection-dir>` + `tape-archive index <root>`.

### Backfilling shipped.json (for collections shipped before this feature)

```cmd
tape-archive mark-shipped ^
  G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\Ece-thesis-movies ^
  --tape /archive/upoates/lab-archives/Ece-thesis-movies
```

Writes `shipped.json` directly in the collection folder. Re-run
`tape-archive index ...` to surface it in the master page.

### Expiration tabs

The master index shows tabs **All / Active / Expiring (≤90 d) / Expired /
Not on tape** with counts. The "Expiring" and "Expired" tabs are where the
PI can scan for review actions. Status is derived from `notes.expires_at`
at generation time; if the date is absent, the collection is "Active".

### Click-to-copy restore command

Every archive card in the per-collection catalog now has a "📋 copy restore
command" button. It puts the exact two-line shell snippet for restoring
that .tar (cp from tape + `tape-archive restore`) on the clipboard, ready
to paste.

---

## Command reference

```text
tape-archive scan <path>                                   # quick markdown survey
tape-archive planner <path> -o planner.html                # interactive plan builder
tape-archive plan <path> --level position -o plan.yaml     # heuristic plan (auto-fill)
tape-archive compress <plan.yaml> -o <out> --parallel N    # build archives + manifests + catalog
tape-archive verify <out>                                  # re-hash tars, compare to manifests
tape-archive ship --nas <r:path> --work <p> --tape <p>     # NAS→/work→tape, one archive at a time
tape-archive mark-shipped <coll-dir> --tape <p>            # write shipped.json for a collection (backfill or manual)
tape-archive serve <catalog-root>                          # local HTTP server: direct notes saves from browser
tape-archive catalog <out>                                 # rebuild one collection's catalog.html
tape-archive index <catalog-root>                          # rebuild the master index.html
```

All commands have `--help` for the full flag list.
