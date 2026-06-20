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

## Restore (for later, when you actually pull from tape)

```bash
# Pull a tar from tape to a working directory:
cp /archive/upoates/lab-archives/Ece-thesis-paper/210131ablation.tar /restore/
tar -xf /restore/210131ablation.tar -C /restore/

# Verify per-file sha256s against the bundled manifest:
python3 - <<'PY'
import json, hashlib, zstandard
from pathlib import Path
restore = Path("/restore")
m = json.loads((restore / "_MANIFEST.json").read_text())
bad = 0
for f in m["files"]:
    zst = restore / (f["path"] + ".zst")
    data = zstandard.ZstdDecompressor().decompress(zst.read_bytes())
    if hashlib.sha256(data).hexdigest() != f["sha256"]:
        print("MISMATCH:", f["path"]); bad += 1
print(f"checked {len(m['files'])} files, {bad} bad")
PY
```

The `.zst` files stay as-is once extracted — only decompress the ones you
actually need. (We'll formalise this into `tape-archive restore-verify` when
you start doing it routinely.)

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
