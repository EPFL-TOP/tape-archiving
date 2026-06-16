# SCITAS-side setup for tape-archive

End-to-end runbook to go from a fresh SCITAS account to a working
`tape-archive run` against the EPFL RCP NAS. Tested values are filled in
below; replace `helsens` with your own GASPAR username and adjust paths as
needed.


rclone lsd hive-project02:PROJECTS-02


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

## 5. Run tape-archive

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
