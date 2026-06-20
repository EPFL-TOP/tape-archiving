# tape-archive runbook — `Ece-thesis-paper` worked example

End-to-end procedure for archiving one collection from jetraw to LTO tape.
Concrete paths below use `Ece-thesis-paper` as the running example — substitute
your own collection name where you see it.

## The three machines

```text
   ┌───────────────────────────┐       ┌──────────────────────────┐       ┌──────────────────────────┐
   │  Local server (HIVE)      │       │  NAS (sv-nas1)           │       │  SCITAS (jed)            │
   │                           │       │                          │       │                          │
   │  - tape-archive CLI       │ ───▶  │  source dir              │       │  - tape-archive CLI      │
   │  - mounts NAS read-only   │       │  catalog dir             │ ───▶  │  - /work (20 TB quota)   │
   │  - local scratch          │ ◀───  │                          │       │  - /archive (tape)       │
   │                           │       │                          │       │                          │
   │  plan + compress + push   │       │  jetraw lands here       │       │  rclone NAS → /work      │
   │   (parallel)              │       │  catalog stays here      │       │  rsync /work → /archive  │
   └───────────────────────────┘       └──────────────────────────┘       └──────────────────────────┘
                                         │
                                         └── biologists open
                                             lab-archives-catalog/index.html
                                             on the NAS to find files
```

**Data path**: jetraw → NAS source dir → local server (read via mount) →
local server scratch (compressed archives + catalog) → NAS catalog dir → SCITAS
/work → tape.

**Source of truth for "what's on tape"**: the per-archive `_MANIFEST.json`
inside each `.tar` (file-level sha256s) + the on-disk `manifests/*.json` on the
NAS catalog (with archive-level sha256s).

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

### On the NAS — directory conventions

```text
nas_rcp:upoates/common/
  UPOATES_DATA_ARCHIVES/TOTAPE/        ← jetraw deposits here
    Ece-thesis-paper/                  ← one source collection
      …
  lab-archives-catalog/                ← long-term home for catalogs
    Ece-thesis-paper/                  ← one collection's catalog
      catalog.html
      summary.json
      plan.yaml
      manifests/
      archives/                        ← (deleted after ship; tape is the home)
    index.html                         ← master page across all collections
```

The two NAS folders (`UPOATES_DATA_ARCHIVES/TOTAPE/` and
`lab-archives-catalog/`) are independent and never touch each other. The first
holds raw data; the second holds the durable catalog and the transient archive
spool.

---

## One collection, end-to-end: `Ece-thesis-paper`

### 0. jetraw downloads to NAS

You handle this. End state: source files live at
`nas_rcp:upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE/Ece-thesis-paper/`.

### 1. Plan — local server

```bash
# On the local server, in tmux (this is the only big-read on the NAS):
tmux new -s plan-ECE
conda activate tape-archive

SRC=/mnt/nas/upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE/Ece-thesis-paper
NAME=Ece-thesis-paper
STAGING=/local/staging/$NAME       # local SSD; needs ≈ compressed size

tape-archive planner $SRC -o /tmp/planner-$NAME.html
```

Copy `planner-$NAME.html` somewhere you can open in a browser (scp, USB, web
server, whatever). In the browser:

1. Tick the folders you want to be archive roots (the per-experiment dirs are
   usually the natural choice).
2. Edit names if needed.
3. Click **Download plan.yaml**.
4. Move the downloaded `plan.yaml` back to the local server as `plan_ECE.yaml`.

### 2. Compress — local server

```bash
# Still in tmux:
tape-archive compress plan_ECE.yaml \
  -o $STAGING \
  --zstd-level 3 \
  --parallel 8 \
  -v
```

- `--zstd-level 3` is locked in (you measured: zstd 9 buys 1.8 % extra
  compression for 4.4× CPU; not worth it on a 12.4 TB dataset).
- `--parallel N` runs N archives concurrently in separate processes. Pick N
  based on (a) cores you can spare and (b) how many concurrent NAS reads the
  server can sustain. Start with 4–8.
- Resumable: re-run after an interruption; archives with a complete manifest
  are skipped. Pass `--force` to rebuild a specific one.
- Single archive at a time, useful for partial runs or debugging:
  `--archive 210131ablation` (repeatable).

Output under `$STAGING`:

```text
/local/staging/Ece-thesis-paper/
  plan.yaml
  archives/*.tar
  manifests/*.json
  catalog.html
  summary.json
```

Quick local sanity check before pushing:

```bash
tape-archive verify $STAGING        # re-hashes every .tar against its manifest
```

### 3. Push to NAS — local server

```bash
NAS_CATALOG=nas_rcp:upoates/common/lab-archives-catalog/$NAME

# Push everything (catalog assets + the heavy archives):
rclone copy $STAGING $NAS_CATALOG \
  --transfers 4 --checkers 16 --progress
```

After this, `$STAGING` can be deleted on the local server — the NAS now holds
both the catalog AND the archives. The archives stay on NAS until SCITAS has
shipped them to tape (next step).

### 4. Ship to tape — SCITAS

```bash
# On SCITAS, in tmux (this is the long step):
tmux new -s ship-ECE
conda activate jetraw
module load rclone

NAME=Ece-thesis-paper
tape-archive ship \
  --nas  nas_rcp:upoates/common/lab-archives-catalog/$NAME \
  --work /work/upoates/ship/$NAME \
  --tape /archive/upoates/lab-archives/$NAME \
  --batch-budget-gb 17000 \
  -v
```

What ship does, one archive at a time:

1. Skip if the tar is already on `--tape` with matching sha256.
2. `rclone copy` tar + manifest from NAS into `/work`.
3. SHA-256 the tar on `/work`, compare to manifest. Mismatch → leave for inspection, move on.
4. `rsync` to `/archive`.
5. SHA-256 on `/archive`, compare. Mismatch → leave both copies in place, fail.
6. `rm` from `/work`.

Resumable: interrupt anytime, re-run, it picks up where it left off (presence
on tape with correct sha256 = shipped).

`--batch-budget-gb 17000` refuses to start an archive that would push `/work`
past 17 TB — defensive cap on the 20 TB quota.

### 5. Clean the NAS archives — you (any machine with rclone)

Once `ship` has reported `failed=0` and `tape-archive verify` on the tape side
confirms everything is intact, the NAS copy of `archives/` is redundant. Delete
it; keep the catalog (manifests/ + catalog.html + summary.json + plan.yaml).

```bash
rclone purge nas_rcp:upoates/common/lab-archives-catalog/Ece-thesis-paper/archives
```

(You said you'd handle NAS cleanup, so `ship` doesn't do it automatically.)

### 6. Regenerate the master index

The master `index.html` aggregates every collection on the NAS into one
clickable page. Walks recursively, so nested layouts (`group/project/<name>`)
work too.

```bash
# Easiest: pull a thin mirror of the catalog locally, regen, push back:
LOCAL_MIRROR=/local/catalog-mirror
mkdir -p $LOCAL_MIRROR

rclone copy nas_rcp:upoates/common/lab-archives-catalog $LOCAL_MIRROR \
  --exclude "*/archives/**"        # don't pull tars; we only need catalog files

tape-archive index $LOCAL_MIRROR -o $LOCAL_MIRROR/index.html

rclone copy $LOCAL_MIRROR/index.html \
  nas_rcp:upoates/common/lab-archives-catalog/
```

Done. Biologists open
`nas_rcp:upoates/common/lab-archives-catalog/index.html` (or whatever URL maps
to it in your environment) → click the `Ece-thesis-paper` card → browse the
file tree → see which `.tar` to pull from tape.

---

## Next collection

Same procedure with a new `NAME`, new plan. Step 6 picks up the new card
automatically.

---

## Larger datasets with subfolders (multi-plan workflow)

`Ece-thesis-paper` is the easy case: one flat source dir, 29 archives all at
depth 1, one plan. Larger datasets often have hierarchy — e.g.
`Arianne` with multiple top-level subgroups, each containing its own
experiments at varying depths. For those, run **one plan per logical
subgroup**, and keep the hierarchy on NAS/tape mirroring the source.

### Example: `Arianne` with three subgroups

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
# 1. plan (on local server)
SUBROOT=G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a
tape-archive planner "$SUBROOT" -o planner_arianne_group1_project_a.html
# → open in browser, pick archive roots, Download → plan_arianne_group1_project_a.yaml

# 2. compress
tape-archive compress plan_arianne_group1_project_a.yaml \
  --zstd-level 3 --parallel 4 -v \
  -o G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a__COMPRESSED

# 3. push to NAS, MIRRORING the source path under lab-archives-catalog/
rclone copy \
  G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\arianne\group1\project_a__COMPRESSED \
  nas_rcp:upoates/common/lab-archives-catalog/arianne/group1/project_a \
  --transfers 4 --checkers 16 --progress

# 4. on SCITAS, ship — note the path mirrors NAS and the source
tape-archive ship \
  --nas  nas_rcp:upoates/common/lab-archives-catalog/arianne/group1/project_a \
  --work /work/upoates/ship/arianne/group1/project_a \
  --tape /archive/upoates/lab-archives/arianne/group1/project_a \
  --batch-budget-gb 17000 -v

# 5. clean NAS tars (you handle this)
rclone purge \
  nas_rcp:upoates/common/lab-archives-catalog/arianne/group1/project_a/archives
```

Repeat for `arianne/group1/project_b`, `arianne/group2/sub/data`, `arianne/group3`, etc.

### Final layout you end up with

NAS (browsable, catalog only — no tars after cleanup):
```text
nas_rcp:upoates/common/lab-archives-catalog/
├── index.html                      ← master page (recursive, sees everything below)
├── Ece-thesis-paper/               (flat)
│   ├── catalog.html
│   └── manifests/, plan.yaml, summary.json
├── arianne/
│   ├── group1/
│   │   ├── project_a/
│   │   │   └── catalog.html ...
│   │   └── project_b/
│   │       └── catalog.html ...
│   ├── group2/sub/data/
│   │   └── catalog.html ...
│   └── group3/
│       └── catalog.html ...
└── ...
```

Tape (parallel structure, just the tars):
```text
/archive/upoates/lab-archives/
├── Ece-thesis-paper/*.tar
└── arianne/
    ├── group1/project_a/*.tar
    ├── group1/project_b/*.tar
    ├── group2/sub/data/*.tar
    └── group3/*.tar
```

The master `index.html` on the NAS will pick up **every** collection no matter
how deep, because `tape-archive index` walks recursively. Each card shows the
full path relative to `lab-archives-catalog/`, so biologists see at a glance
which subgroup a collection belongs to.

### Batch tip — script the loop

Once you've generated all the plan YAMLs for one dataset, the
compress/push/ship cycle is mechanical. A shell loop on the local server:

```bash
for plan in plan_arianne_*.yaml; do
  # derive a name like "arianne_group1_project_a" from the plan filename
  NAME=${plan#plan_}; NAME=${NAME%.yaml}
  NAS_REL=$(echo "$NAME" | tr '_' '/')   # adjust if your naming differs
  tape-archive compress "$plan" \
    -o "G:\PROJECTS-02\Clement\TMP-ARCHIVE-TO_SCITAS\${NAME}__COMPRESSED" \
    --zstd-level 3 --parallel 4 -v
  rclone copy "G:\...\${NAME}__COMPRESSED" \
    "nas_rcp:upoates/common/lab-archives-catalog/${NAS_REL}" \
    --transfers 4 --checkers 16 --progress
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

## Command reference

```text
tape-archive scan <path>                                   # quick markdown survey
tape-archive planner <path> -o planner.html                # interactive plan builder
tape-archive plan <path> --level position -o plan.yaml     # heuristic plan (auto-fill)
tape-archive compress <plan.yaml> -o <out> --parallel N    # build archives + manifests + catalog
tape-archive verify <out>                                  # re-hash tars, compare to manifests
tape-archive ship --nas <r:path> --work <p> --tape <p>     # NAS→/work→tape, one archive at a time
tape-archive catalog <out>                                 # rebuild one collection's catalog.html
tape-archive index <catalog-root>                          # rebuild the master index.html
```

All commands have `--help` for the full flag list.
