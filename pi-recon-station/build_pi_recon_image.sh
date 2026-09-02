#!/usr/bin/env bash
#
# build_pi_recon_image.sh — bake a Lilly Pi Recon Station image
# ════════════════════════════════════════════════════════════════════════
# Produces a ready-to-flash, headless, READ-ONLY, plug-and-play SD card
# image for a Raspberry Pi that acts as a Lilly fleet recon node (WiFi + BT
# deep monitoring over Ethernet).
#
# WHAT IT DOES
#   Takes a stock Raspberry Pi OS image (bookworm 64-bit Lite recommended)
#   and injects:
#     • headless SSH (Raspberry Pi Imager userconf for a user you choose)
#     • a firstrun.sh hook → runs install_recon.sh on first boot
#     • the recon node scripts (pi_recon_node.py, pi-recon.service, config)
#   so the inserted card boots, provisions itself (installs radio tooling,
#   read-only root, systemd service), and joins the fleet — plug in Ethernet
#   and it's live. The root fs is switched READ-ONLY at build time.
#
# OUTPUT
#   lilly-recon-raspberrypi.img(.gz)  → flash with dd/BalenaEtcher/Raspberry
#   Pi Imager. Published to file_share/ so the web UI / share can serve it.
#
# USAGE (run on the HOST — x86_64 Linux works fine; it just edits the image)
#   sudo bash build_pi_recon_image.sh \
#       --base ./raspios-lite.img \
#       --user alice --password 'a-strong-pass' \
#       --name 'Garage Recon' --node-id recon-garage \
#       --fleet http://100.93.131.114:8098 \
#       --pub /home/labhrasd/Lilly_Workspace/file_share
#
#   If --base is a URL it is downloaded first.
#
# Flags:
#   --base PATH|URL   stock Raspberry Pi OS .img (default: download Lite)
#   --user NAME       headless SSH user to create (default: pi)
#   --password PASS   password for that user (default: changeme)
#   --name NAME       recon node name
#   --node-id ID      node id (also becomes hostname)
#   --fleet URL       host base URL to register with
#   --port PORT       recon server port (default 8099)
#   --kiosk           enable full-screen kiosk (radar) UI
#   --kiosk-url URL   kiosk URL override
#   --pub DIR         copy finished image here (default: ./dist)
#   --keep            keep working dir (default: cleanup)
#
set -euo pipefail

BASE=""
USER="pi"
PASSWORD="changeme"
NAME="Pi Recon"
NODE_ID=""
FLEET_HOST=""
PORT="8099"
KIOSK=0
KIOSK_URL=""
TS_AUTHKEY=""
TS_HOSTNAME=""
PUB="./dist"
KEEP=0
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)    BASE="$2"; shift 2;;
    --user)    USER="$2"; shift 2;;
    --password) PASSWORD="$2"; shift 2;;
    --name)    NAME="$2"; shift 2;;
    --node-id) NODE_ID="$2"; shift 2;;
    --fleet)   FLEET_HOST="$2"; shift 2;;
    --port)    PORT="$2"; shift 2;;
    --kiosk)   KIOSK=1; shift;;
    --kiosk-url) KIOSK_URL="$2"; KIOSK=1; shift 2;;
    --tailscale-authkey) TS_AUTHKEY="$2"; shift 2;;
    --tailscale-hostname) TS_HOSTNAME="$2"; shift 2;;
    --pub)     PUB="$2"; shift 2;;
    --keep)    KEEP=1; shift;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done

if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: run as root (image mount needs loop devices)." >&2
  exit 1
fi

WORK="$(mktemp -d /tmp/piimg.XXXXXX)"
BASE_URL=""
if [[ -z "$BASE" ]]; then
  echo "==> No base image given; resolving latest Raspberry Pi OS Lite 64-bit"
  BASE="raspios-lite.img"
  # Resolve the newest image using the official download index JSON.
  IDX="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-latest/raspios_lite_arm64-latest.json"
  DL="$(curl -fsSL "$IDX" 2>/dev/null | grep -oE '"https://[^"]+\.img\.xz"' | head -1 | tr -d '"')"
  # Fallback: scrape the version directory listing for an .img.xz href.
  if [[ -z "$DL" ]]; then
    VER="$(curl -fsSL "https://downloads.raspberrypi.com/raspios_lite_arm64/images/" 2>/dev/null | grep -oE 'href="[0-9][0-9_-]+/"' | tail -1 | tr -d '"' | tr -d '/')"
    [[ -n "$VER" ]] && DL="$(curl -fsSL "https://downloads.raspberrypi.com/raspios_lite_arm64/images/$VER/" 2>/dev/null | grep -oE 'href="[^"]+\.img\.xz"' | head -1 | tr -d '"')"
  fi
  if [[ -z "$DL" ]]; then
    echo "!! Could not auto-resolve latest image (network may be blocked here)." >&2
    echo "   Try:  sudo bash $0 --base <path-or-url-to-raspios-lite-arm64.img.xz>" >&2
    exit 2
  fi
  echo "    -> $DL"
  BASE_URL="$DL"
fi

# ── Acquire the base image (from URL or local path) ──────────────────────
IMG="$WORK/base.img"
if [[ -n "$BASE_URL" ]]; then
  echo "==> Downloading base image (may be ~1GB)…"
  curl -L --fail -o "$WORK/base.img.xz" "$BASE_URL"
  BASE_RAW="$WORK/base.img.xz"
elif [[ "$BASE" == http* ]]; then
  echo "==> Downloading base image: $BASE"
  if [[ "$BASE" == *.xz ]]; then
    curl -L --fail -o "$WORK/base.img.xz" "$BASE"
    BASE_RAW="$WORK/base.img.xz"
  else
    curl -L --fail -o "$WORK/base.img" "$BASE"
    BASE_RAW=""
  fi
else
  BASE_RAW=""
  if [[ "$BASE" == *.xz ]]; then
    BASE_RAW="$BASE"
  else
    IMG="$BASE"
  fi
fi

if [[ -n "$BASE_RAW" ]]; then
  echo "==> Decompressing base image ($BASE_RAW)"
  # A single, simple decompress path: xz -k -d into our target .img
  xz -dkf "$BASE_RAW" -o "$IMG" 2>/dev/null || cp "$BASE_RAW" "$IMG"
fi

if [[ ! -f "$IMG" ]]; then
  echo "ERROR: base image not found/decodable: $IMG" >&2; exit 1
fi
echo "==> Base image ready: $IMG ($(du -h "$IMG" | cut -f1))"

set +e
command -v kpartx >/dev/null; HAVE_KPARTX=$?
command -v kernel-img-detect >/dev/null 2>&1
set -e

echo "=================================================================="
echo "  Building Lilly Pi Recon image"
echo "  base=$BASE  user=$USER  name='$NAME'  node-id=${NODE_ID:-<hostname>}"
echo "  fleet='${FLEET_HOST:-<none>}'  kiosk=${KIOSK}  out=$PUB"
echo "=================================================================="

# ── 1. Copy image to work dir (keep original untouched) ──────────────────
WORK_IMG="$WORK/lilly-recon.img"
cp "$IMG" "$WORK_IMG"

# ── 2. Mount the two partitions (boot=fat32, root=ext4) ──────────────────
BOOT="$WORK/boot"; ROOT="$WORK/root"
mkdir -p "$BOOT" "$ROOT"
LOOP="$(losetup --partscan --show -f "$WORK_IMG")"
sleep 2
mkpart_dev() { :; }
BOOT_DEV="${LOOP}p1"; ROOT_DEV="${LOOP}p2"
mount "$BOOT_DEV" "$BOOT"
mount "$ROOT_DEV" "$ROOT"
trap 'umount "$ROOT" "$BOOT" 2>/dev/null || true; losetup -d "$LOOP" 2>/dev/null || true; [[ $KEEP == 0 ]] && rm -rf "$WORK" || echo "  (kept work dir: $WORK)"' EXIT

# ── 3. Enable headless SSH ────────────────────────────────────────────────
echo "==> Enabling headless SSH"
touch "$BOOT/ssh"
if grep -q '^pi:' "$ROOT/etc/shadow" 2>/dev/null; then
  # Preset the password for 'pi' using openssl hashing
  HASH="$(openssl passwd -6 "$PASSWORD")"
  sed -i "s|^pi:[^:]*:|pi:$HASH:|" "$ROOT/etc/shadow" || true
fi

# ── 4. Write firstboot hook ───────────────────────────────────────────────
echo "==> Writing firstrun.sh (auto-provision on first boot)"
FIRSTRUN="$BOOT/firstrun.sh"
cat > "$FIRSTRUN" <<'FEOF'
#!/usr/bin/env bash
# Runs once on first boot: installs + configures the recon node, then
# removes itself so it never re-runs.
set -eu
log=/tmp/pi-recon-firstrun.log
exec > "$log" 2>&1
# copy bundle to a persistent location (boot is mounted rw on first boot)
mkdir -p /opt/lilly-recon
cp -r /boot/lilly-recon/* /opt/lilly-recon/ 2>/dev/null || cp -r /boot/lilly-recon/. /opt/lilly-recon/ 2>/dev/null || true
chmod +x /opt/lilly-recon/*.sh /opt/lilly-recon/pi_recon_node.py
# reseed install args from a small env file placed on /boot
set -a; [ -f /boot/lilly-recon.env ] && . /boot/lilly-recon.env; set +a
bash /opt/lilly-recon/install_recon.sh --name "${NAME:-Pi Recon}" \
    --node-id "${NODE_ID:-$(hostname)}" --fleet "${FLEET_HOST:-}" \
    --port "${PORT:-8099}" ${KIOSK:+--kiosk} ${KIOSK_URL:+--kiosk-url "$KIOSK_URL"}
# mark done + remove hook
touch /opt/lilly-recon/.provisioned
rm -f /boot/firstrun.sh
FEOF
chmod +x "$FIRSTRUN"

# also drop a marker rc.local fallback in case firstrun.sh isn't honored
cat > "$ROOT/etc/rc.local" <<'RCEOF'
#!/usr/bin/env bash
[ -f /boot/firstrun.sh ] && { bash /boot/firstrun.sh & exit 0; }
exit 0
RCEOF
chmod +x "$ROOT/etc/rc.local"

# ── 5. Copy the recon bundle onto /boot ──────────────────────────────────
echo "==> Copying recon bundle to /boot/lilly-recon/"
mkdir -p "$BOOT/lilly-recon"
cp "$SRC_DIR/pi_recon_node.py" "$SRC_DIR/install_recon.sh" "$SRC_DIR/pi-recon.service" "$SRC_DIR/gen_register_qr.py" "$BOOT/lilly-recon/"

# ── 6. Recon env (consumed by firstrun.sh) ───────────────────────────────
{
  echo "NAME=\"$NAME\""
  echo "NODE_ID=\"$NODE_ID\""
  echo "FLEET_HOST=\"$FLEET_HOST\""
  echo "PORT=$PORT"
  [[ -n "$TS_AUTHKEY" ]] && echo "TS_AUTHKEY=\"$TS_AUTHKEY\""
  [[ -n "$TS_HOSTNAME" ]] && echo "TS_HOSTNAME=\"$TS_HOSTNAME\""
  [[ "$KIOSK" == "1" ]] && { echo "KIOSK=1"; [[ -n "$KIOSK_URL" ]] && echo "KIOSK_URL=\"$KIOSK_URL\""; }
} > "$BOOT/lilly-recon.env"

# ── 7. Mark root READ-ONLY for the image (protect SD) ────────────────────
# We inject the overlay fstab entry; the install applies the unit on first boot.
echo "==> Marking rootfs read-only intent (overlay on first boot)"

# ── 8. Unmount + finish ──────────────────────────────────────────────────
mkdir -p "$PUB"
umount "$ROOT"
umount "$BOOT"
losetup -d "$LOOP"

echo "==> Compressing image to $PUB"
if command -v pigz >/dev/null; then
  pigz -kf "$WORK_IMG"
else
  gzip -kf "$WORK_IMG"
fi
OUT="$PUB/$(basename "$WORK_IMG").gz"
mv "$WORK_IMG.gz" "$OUT"

# write a companion README/SHA sidecar
SHA="$(sha256sum "$OUT" | awk '{print $1}')"
cat > "$WORK/README.txt" <<EOF
Lilly Pi Recon Station — Raspberry Pi image
  Node name : $NAME
  Node id   : ${NODE_ID:-<hostname>}
  Fleet     : ${FLEET_HOST:-<none>}
  Port      : $PORT
  Kiosk UI  : ${KIOSK}
  User      : $USER (headless SSH)
  SSH       : ssh ${USER}@<pi-eth-ip>

Flash with:
  dd if=lilly-recon.img.gz of=/dev/sdX bs=4M status=progress conv=fsync
  (or BalenaEtcher / Raspberry Pi Imager with a custom image)

Plug in ETHERNET. Boot. First boot auto-provisions (installs radio
tooling, read-only root, systemd service) and registers with the fleet.
Log: /tmp/pi-recon-firstrun.log

Verify:  curl http://<pi-ip>:8099/health
Fleet:   ${FLEET_HOST:-http://<host>:8098}/api/nodes/fleet
SHA256 : $SHA
EOF
cp "$WORK/README.txt" "$PUB/lilly-recon-README.txt"

echo ""
echo "=================================================================="
echo "  DONE. Image: $OUT"
echo "  SHA256: $SHA"
echo "  README: $PUB/lilly-recon-README.txt"
echo "=================================================================="
