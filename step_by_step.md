# SCITAS-side setup for tape-archive

End-to-end runbook to go from a fresh SCITAS account to a working
`tape-archive run` against the EPFL RCP NAS. Tested values are filled in
below; replace `helsens` with your own GASPAR username and adjust paths as
needed.


rclone lsd hive-project02:PROJECTS-02
rclone lsd nas_rcp:/upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE


rclone copy hive-project02:PROJECTS-02/Clement/FROM-JETRAW-TO-SCITAS-TAPE/2024_12_wscpaper /work/upoates/TO_TAPE/2024_12_wscpaper/ \
  --transfers 16 \
  --checkers 32 \
  --multi-thread-streams 4 \
  --multi-thread-cutoff 100M \
  --progress \
  --stats 5s \
  --stats-one-line \
  --retries 5 \
  --low-level-retries 20 

rclone copy nas_rcp:/upoates/common/UPOATES_DATA_ARCHIVES/TOTAPE/Ece-thesis-paper /work/upoates/TO_TAPE/Ece-thesis-paper \
  --transfers 16 \
  --checkers 32 \
  --multi-thread-streams 4 \
  --multi-thread-cutoff 100M \
  --progress \
  --stats 5s \
  --stats-one-line \
  --retries 5 \
  --low-level-retries 20 




---

## 1. Install jetraw CLI (one-time)

Use a dedicated conda env so the GLIBCXX workaround stays contained:

```bash
conda create -n jetraw python=3.12 -y
conda activate jetraw
pip install jr_cli --index-url https://releases.jetraw.com
```

### 1a. Fix the `GLIBCXX_3.4.30 not found` error

`libjetraw.so` is built against GCC ≥ 12; SCITAS's system `libstdc++` is older.
Install a newer one inside the env and pin it onto `LD_LIBRARY_PATH`:

```bash
conda install -n jetraw -c conda-forge 'libstdcxx-ng>=12' -y

# verify the symbol is present
strings "$CONDA_PREFIX/lib/libstdc++.so.6" | grep GLIBCXX_3.4.30

# make conda set LD_LIBRARY_PATH on every activation of this env
conda env config vars set LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$LD_LIBRARY_PATH" -n jetraw
conda deactivate && conda activate jetraw
```

### 1b. Authenticate jetraw

```bash
jr auth login            # follow the prompts; opens a browser flow
jr destination list      # confirm the CLI works end-to-end
```

---

## 2. Configure rclone for the RCP NAS (one-time)

```bash
module load rclone
rclone version
```

Run the interactive config:

```bash
rclone config
# n) new remote
# name>     nas-rcp                       (the name jetraw expects — note the hyphen)
# Storage>  smb
# host>     sv-nas1.rcp.epfl.ch
# user>     <your GASPAR username>
# port>     (Enter — 445)
# pass>     y, then type GASPAR password (rclone obscures it)
# domain>   INTRANET                       ← confirmed working
# spn>      (empty)
# Edit advanced config> n
# Keep this "nas-rcp" remote> y
# q) quit
chmod 600 ~/.config/rclone/rclone.conf
```

Smoke test (no mount yet):
```bash
rclone lsd nas-rcp:                    # should list the "upoates" share
rclone ls nas-rcp:upoates --max-depth 1
```

---

## 3. Mount the NAS (every session)

Mounts die when the SSH session ends — always start them inside `tmux`.

```bash
tmux new -s rclone-mount

# inside the tmux pane:
module load rclone

MNT=/scratch/helsens/mnt/nas_rcp
mkdir -p "$MNT"

rclone mount nas-rcp:upoates "$MNT" \
  --read-only \
  --vfs-cache-mode minimal \
  --dir-cache-time 12h \
  --attr-timeout 1m \
  --buffer-size 32M \
  --log-file ~/rclone-mount.log \
  --log-level INFO

# detach tmux without killing the mount: Ctrl-b then d
```

Reattach later with `tmux attach -t rclone-mount`. Verify from a fresh shell:

```bash
ls /scratch/helsens/mnt/nas_rcp | head
```

To unmount when done:
```bash
fusermount -u /scratch/helsens/mnt/nas_rcp      # or fusermount3 -u
tmux kill-session -t rclone-mount
```

---

## 4. Point jetraw at the mounted share (every session, or when the path changes)

The jetraw destination URL must point at the directory **inside** the SMB share
that holds the JetRaw-compressed tree — not at the mount root:

```bash
jr destination edit nas-rcp \
  --set-url /scratch/helsens/mnt/nas_rcp/common/JetRawCompressedFiles
```

Sanity check:
```bash
jr destination list
```

After this, jetraw paths use its own virtual namespace
(`/nas-rcp-space/...`).

⚠ `jr download` is long-running — always start it in its own tmux session, for
the same reason as the rclone mount: an SSH disconnect will kill anything in
the foreground shell.

```bash
tmux new -s jet-raw-copy

# inside the tmux pane:
conda activate jetraw
module load rclone     # only needed if the mount must be re-checked from here

jr download "/nas-rcp-space/UPOATES_DATA_ARCHIVES/2024_12_wscpaper-ToTape/H2AGFP - Heidi/20191031_140601_11/RoiSet_1-80.zip" \
            /work/upoates/
# checksum verification is enabled in jr's own config.toml, no CLI flag needed

# detach without killing the download: Ctrl-b then d
```

Reattach to follow progress with `tmux attach -t jet-raw-copy`. When the
download finishes (or you want to stop), close the session with
`tmux kill-session -t jet-raw-copy`.

---

## 5. Install the `tape-archive` CLI

Install into the same conda env as `jr` so it inherits the libstdcxx fix and
LD_LIBRARY_PATH:

```bash
conda activate jetraw
cd /path/to/tape-archiving         # wherever the repo is cloned
pip install -e .                   # editable install; pulls in PyYAML

# verify
tape-archive --help
tape-archive scan --help
```

### Explore a dataset before archiving

`tape-archive scan` walks a directory and emits a markdown (or JSON) summary —
extension breakdown, size hotspots, suggested archive candidates, depth-limited
tree. Pure metadata walk, no content reads, no tmux needed.

```bash
tape-archive scan /work/upoates/2024_12_wscpaper-ToTape -o scan.md
less scan.md

# Useful flags:
#   --candidate-size-gb N     threshold for flagging a folder as tar-worthy (default 10)
#   --candidate-min-files N   min file count for a candidate (default 10)
#   --max-tree-depth N        cap the printed tree depth (default 4)
#   --format json -o scan.json   machine-readable
```

Use the scan output to decide archive granularity before running the full
pipeline.

### Build an archive plan interactively (Phase A, recommended)

`tape-archive planner` walks the tree and emits a **single self-contained HTML
file** with the full folder + file listing. Open it in a browser; click ☐ to
mark folders as archive roots; tweak names; click **Download plan.yaml**.

```bash
tape-archive planner /work/upoates/TO_TAPE/2024_12_wscpaper -o planner.html
# scp to wherever you can open it:
scp scitas:/path/to/planner.html ~/Downloads/
open ~/Downloads/planner.html       # or open in any browser
```

What the page gives you:

- Browsable tree with every folder and file, sizes shown inline.
- Checkbox per folder ☐ → marks it as an archive root.
- Quick-start preset buttons ("Mark all at depth 1/2/3") to seed the selection,
  then refine by clicking individual folders.
- Right sidebar: live list of selected archives with editable names, sizes,
  and the carved-out sub-archives ("excludes").
- Sub-archives are handled automatically: if you mark `foo/` and also `foo/bar/`,
  `foo`'s archive will exclude `foo/bar` and `foo/bar` becomes its own archive.
- Selection persists in `localStorage` per source root, so you can close the
  tab and come back to it.
- **Download plan.yaml** → emits a YAML the compress stage will ingest.

### Build an archive plan from a heuristic (legacy, useful as a starting point)

`tape-archive plan` walks the tree and produces an **editable YAML plan** plus
a **single-file HTML preview**. Useful if you want a non-interactive starting
point, but the interactive `planner` is the recommended path now.

```bash
mkdir -p plans

# One archive per top-level folder (3 archives for wscpaper):
tape-archive plan /work/upoates/TO_TAPE/2024_12_wscpaper \
  --level top \
  -o plans/top.yaml --preview plans/top.html

# One archive per experiment (~10 archives, 600 GB – 3 TB each):
tape-archive plan /work/upoates/TO_TAPE/2024_12_wscpaper \
  --level experiment \
  -o plans/experiment.yaml --preview plans/experiment.html

# One archive per leaf-ish movie folder + per-experiment metadata bundle
# (~33 archives, 500 GB – 1 TB each); recommended starting point:
tape-archive plan /work/upoates/TO_TAPE/2024_12_wscpaper \
  --level position \
  --position-min-size-gb 50 \
  --max-size-gb 3000 \
  -o plans/position.yaml --preview plans/position.html

# Pure size-based bin-packing:
tape-archive plan /work/upoates/TO_TAPE/2024_12_wscpaper \
  --level auto \
  --target-size-gb 1000 --max-size-gb 3000 \
  -o plans/auto.yaml --preview plans/auto.html
```

Then copy the HTML previews off SCITAS to wherever you can click on them:

```bash
# from your local machine:
scp scitas:/work/upoates/.../plans/*.html ~/Downloads/
open ~/Downloads/position.html
```

The preview shows: the archive table (sortable by size), a clickable tree with
each directory tagged with the archive it belongs to, and the raw YAML at the
bottom for reference. Yellow rows / orange badges flag archives that exceed
`--max-size-gb` (review and decide whether to split).

When the chosen plan looks right, edit the YAML (rename archives, move members
between archives, merge/split) and feed it to the compress stage.

### Build the archives (Phase B)

`tape-archive compress` ingests `plan.yaml`, reads every source file exactly
once (computing SHA-256 + zstd in the same pass), and writes a `.tar` per
archive plus per-archive JSON manifests and a browsable catalog HTML.

```bash
tmux new -s tape-compress    # long-running for TB-scale; survive SSH drop
conda activate jetraw
cd /home/helsens/tape-archiving

tape-archive compress plan.yaml \
  -o /scratch/helsens/tape_output/Ece-thesis-paper \
  --zstd-level 3            # 1 = fastest, 22 = best ratio; 3 is the sweet spot
  -v
```

Output layout under `-o`:

```text
tape_output/Ece-thesis-paper/
├── plan.yaml                # copy of the input plan (traceability)
├── archives/
│   ├── 210131ablation.tar   # plain tar; each entry is <orig-path>.zst
│   ├── 210225ablation.tar
│   └── …                    # one per archive in plan.yaml
├── manifests/
│   ├── 210131ablation.json  # per-archive manifest (the on-disk catalog)
│   └── …
└── catalog.html             # single-file browsable catalog
```

What's inside each `.tar`:

- Every source file as its own zstd-compressed entry, named `<original-path>.zst`.
- `_MANIFEST.json` — the per-file manifest bundled inside the tar (everything
  except the self-referential archive sha256).

What's inside each on-disk manifest (`manifests/<name>.json`):

- `archive_sha256` — the SHA-256 of the .tar file (for tape bit-rot detection)
- `archive_size_bytes` — the .tar size
- `files: [{path, size_bytes, sha256, compressed_bytes, mtime}, …]` — per-file
  details for every member, including the SHA-256 of the **original
  (uncompressed) bytes**. This is what you compare against after a tape restore.

**Where the checksums live (you asked):**

- Primary: `manifests/*.json` on disk, separate from the tape. Back this up
  off-SCITAS — scp it to HIVE, to your Mac, to a git LFS repo, whatever.
- Bundled: `_MANIFEST.json` inside each `.tar`. Survives if the on-disk
  manifests are lost — restore the tar and read it.

### Restore-time verification (for later, when you actually pull from tape)

```bash
# Extract one archive from tape:
tar -xf 210131ablation.tar -C /restore-target/

# Verify per-file sha256s against the bundled manifest:
python3 - <<'PY'
import tarfile, json, hashlib, zstandard, sys
from pathlib import Path
restore = Path("/restore-target")
m = json.loads((restore / "_MANIFEST.json").read_text())
bad = 0
for f in m["files"]:
    zst = restore / (f["path"] + ".zst")
    data = zstandard.ZstdDecompressor().decompress(zst.read_bytes())
    sha = hashlib.sha256(data).hexdigest()
    if sha != f["sha256"]:
        print("MISMATCH:", f["path"]); bad += 1
print(f"checked {len(m['files'])} files, {bad} bad")
sys.exit(1 if bad else 0)
PY
```

(We'll formalise that into a `tape-archive verify` subcommand once you're ready
to actually pull from tape.)

### Browsing the catalog

Copy `catalog.html` off SCITAS — it's self-contained, no server needed:

```bash
# from your local machine:
scp scitas:/scratch/helsens/tape_output/Ece-thesis-paper/catalog.html ~/Downloads/
open ~/Downloads/catalog.html
```

What biologists see: clickable tree of every original file, with size,
SHA-256 (truncated; hover the full one is shown in tooltip), and a green
badge naming the archive that holds it. Clicking the badge highlights the
archive on the right panel (its tape filename, full sha256, size, file count,
compression ratio). A filter box at the top searches by filename, path, or
sha256.

To regenerate the catalog after changes (e.g., you rebuilt one archive):

```bash
tape-archive catalog /scratch/helsens/tape_output/Ece-thesis-paper
```

### Pipeline B — strategy with 20 TB /work quota on SCITAS

If a dataset is bigger than /work can hold all at once, compress on a local
server (HIVE, your workstation, anything with enough disk), push the result
to the NAS, then ship to tape in chunks from SCITAS. The `ship` subcommand
walks one archive at a time, never letting /work get full.

**On the local server** (no SCITAS quota concerns here):

```bash
# 1. Compress a subset of the plan (use --archive flags to chunk it):
tape-archive compress plan_ECE.yaml \
  -o /local/staging/Ece-thesis-movies \
  --archive 210131ablation --archive 210225ablation \
  --zstd-level 3 -v

# 2. Push the compressed output to the NAS so SCITAS can pull it:
rclone copy /local/staging/Ece-thesis-movies \
  nas_rcp:upoates/common/lab-archives-catalog/Ece-thesis-movies \
  --transfers 4 --progress

# 3. Loop: compress the next subset, push, repeat until the full plan is on NAS.
#    `summary.json` on NAS auto-aggregates across runs.
```

**On SCITAS** (inside tmux — long running, walks one archive at a time):

```bash
tmux new -s ship-ECE
conda activate jetraw      # or your tape-archive env
module load rclone

tape-archive ship \
  --nas nas_rcp:upoates/common/lab-archives-catalog/Ece-thesis-movies \
  --work /work/upoates/ship/Ece-thesis-movies \
  --tape /archive/upoates/lab-archives/Ece-thesis-movies \
  --batch-budget-gb 17000 \
  -v
```

What `ship` does, per archive listed in the collection's `summary.json`:

1. If the tar already sits on `--tape` with the right sha256 → skip.
2. `rclone copy` the tar + its manifest from NAS into `--work`.
3. Hash the tar on `--work`, compare to manifest. Mismatch → fail loudly, leave tar in `--work` for inspection.
4. `rsync` the tar to `--tape`.
5. Hash on `--tape`, compare to manifest. Mismatch → leave tape copy alone, leave /work copy, fail.
6. Delete the tar from `--work` to free quota space.

Resumable: every step verifies sha256 before removing anything, and a tar already on tape with the right sha256 is auto-skipped. Re-run the same command after an interruption.

`--archive NAME` (repeatable) limits the run to specific archives if you want to ship one at a time. `--batch-budget-gb` refuses to start an archive that would push /work past the limit — useful as a defensive cap so you don't hit the quota mid-rsync.

**After `ship` completes** — NAS-side cleanup is on you (per our agreement). Once everything has landed on tape and `tape-archive verify --archives <tape> --manifests <local-catalog>/manifests` passes, you can delete `archives/*.tar` from NAS. The `manifests/`, `catalog.html`, `plan.yaml`, `summary.json` stay on NAS as the long-term catalog.

**Regenerate the master index** (now walks recursively, finds nested collections):

```bash
# Local catalog mirror you've been syncing from NAS, OR a freshly-pulled copy:
tape-archive index /local/catalog_mirror
rclone copy /local/catalog_mirror/index.html \
  nas_rcp:upoates/common/lab-archives-catalog/
```

The master `index.html` will show each collection by its full path under
`lab-archives-catalog/`, so `Ece-thesis-movies` and
`group_alpha/project_beta/wscpaper` both appear as distinct cards.

---

### Ship one collection: SCITAS → tape + NAS (legacy / single-server flow)

Once a collection is compressed and verified, split it: heavy `.tar` files go
to tape, the catalog + manifests stay on the NAS where biologists can browse.

Pick paths once at the top:

```bash
NAME=Ece-thesis-paper
SRC=/scratch/helsens/tape_output/$NAME            # compress output on SCITAS
TAPE=/archive/upoates/lab-archives/$NAME           # tape mount destination
NAS=/path/to/nas/lab-archives-catalog/$NAME        # NAS catalog destination
```

```bash
# 1. Verify the archives on /scratch match their manifests (catches anything
#    that went wrong during compression):
tape-archive verify "$SRC"

# 2. Copy .tar files to tape. rsync (not mv) so a failure mid-copy leaves
#    the source intact:
mkdir -p "$TAPE"
rsync -av --progress "$SRC/archives/" "$TAPE/"

# 3. Re-verify on the tape side, against the same manifests:
tape-archive verify --archives "$TAPE" --manifests "$SRC/manifests"

# 4. Copy the catalog assets (everything EXCEPT archives/) to the NAS:
mkdir -p "$NAS"
rsync -av \
  "$SRC/catalog.html" "$SRC/plan.yaml" "$SRC/summary.json" \
  "$SRC/manifests" \
  "$NAS/"

# 5. Regenerate the master index on the NAS so this collection appears:
tape-archive index "$(dirname "$NAS")" -o "$(dirname "$NAS")/index.html"

# 6. (Only after both verifies pass and the index is regenerated) reclaim
#    scratch space by deleting the local .tar copies:
rm -rf "$SRC/archives"
# Keep the rest of $SRC around if you like; it's redundant with the NAS copy.
```

After step 5, biologists open `index.html` on the NAS and see a card for every
collection. Click → that collection's `catalog.html` → browse files and find
the tape archive name they need to restore.

NAS layout after a few collections:

```text
/path/to/nas/lab-archives-catalog/
├── index.html                       ← master page, lists every collection
├── 2024_12_wscpaper/
│   ├── catalog.html
│   ├── plan.yaml
│   ├── summary.json
│   └── manifests/*.json
├── Ece-thesis-paper/
│   ├── catalog.html
│   ├── ...
└── …
```

Tape layout (parallel structure, just the `.tar` files):

```text
/archive/upoates/lab-archives/
├── 2024_12_wscpaper/
│   ├── 0001_top_level_dir.tar
│   └── ...
├── Ece-thesis-paper/
│   ├── 210131ablation.tar
│   ├── 210225ablation.tar
│   └── ...
└── …
```

### Disaster-recovery story (worth knowing)

- **Lose the NAS catalog** → every `.tar` on tape contains its own
  `_MANIFEST.json` (per-file sha256s). Restore one tar, read manifest, verify.
- **Lose a tape archive** → the per-file sha256s in the NAS manifest still
  exist; you know exactly what was lost and can re-derive a recovery plan.
- **Lose both** → the bundled `_MANIFEST.json` inside any other tar also names
  its archive's contents, so neighbouring tapes still tell you what was where.

### Adding another collection later

Same recipe with a different `$NAME` / plan. After step 5, the master index
will list the new card alongside the existing ones; nothing else changes.

---

## 7. Run tape-archive (jetraw end-to-end pipeline, legacy)

Edit [configs/example-jetraw.yaml](configs/example-jetraw.yaml):

```yaml
rclone:
  mount_path: /scratch/helsens/mnt/nas_rcp
jetraw:
  destination: nas-rcp
source:
  jetraw_path: /nas-rcp-space/UPOATES_DATA_ARCHIVES/2024_12_wscpaper-ToTape/...
  name: 2024_12_wscpaper
staging:
  root: /scratch/helsens/tape_staging
tape:
  mount_path: /tape/<group>/<project>
```

Then, in a tmux session:
```bash
tmux new -s tape-archive
conda activate jetraw
module load rclone

# precheck first (cheap; no downloads):
tape-archive run configs/example-jetraw.yaml --steps precheck -v

# full run when precheck is clean:
tape-archive run configs/example-jetraw.yaml -v
```

---

## Quick teardown

```bash
fusermount -u /scratch/helsens/mnt/nas_rcp
tmux kill-server                                  # nukes all tmux sessions
```

---

## Open items (to revisit)

- **SLURM jobs**: the login-node mount is invisible to compute nodes. To run
  `tape-archive` under SLURM, the mount + jr config must happen inside the job
  script. Add a wrapper once the interactive flow is solid.
- **Mount lifetime**: a single long mount is fine for one dataset at a time;
  if multiple users on the same node mount the same share concurrently, expect
  rclone-cache contention. Each user should mount under their own `$SCRATCH`.
- **Credentials**: `rclone.conf` stores the password obscured (not encrypted).
  `chmod 600` is the only thing protecting it. If we need stronger security,
  rclone supports a config-file passphrase (`rclone config encryption set`).
