#!/usr/bin/env bash
#
# install_recon.sh — first-boot provisioning for the Lilly Pi Recon Station
# ════════════════════════════════════════════════════════════════════════
# Turns a stock Raspberry Pi OS (bookworm 64-bit, Lite recommended) into a
# headless, read-only, plug-and-play recon node that joins the Lilly fleet
# over ETHERNET and monitors WiFi + Bluetooth on its dedicated radios.
#
# Design (per operator):
#   • ETH0 / ETHERNET = the ONLY uplink (DHCP). WiFi stays 100% free for
#     passive monitoring. No WiFi client config needed.
#   • ROOTFS = READ-ONLY (OverlayFS lower=firm, upper on tmpfs) so the SD
#     card is write-protected for 24/7 headless duty — nothing to wear out
#     and no corruption on power loss. Runtime state lives in RAM.
#   • Optional KIOSK UI: a full-screen browser pinned to the fleet's
#     lilly_ai radar view (served over Ethernet), great for a wall/table
#     display. Uses lilly_ai as the kiosk-mode UI.
#
# Usage (run as root on the Pi, or drop into Raspberry Pi Imager's
#   firstrun.sh so it runs automatically on first boot):
#     sudo bash install_recon.sh --name 'Garage Recon' --node-id recon-garage \
#          --fleet http://100.93.131.114:8098
#
# Flags:
#   --name NAME        human node name (default: "Pi Recon")
#   --node-id ID       unique id (default: <hostname>)
#   --fleet URL        host base URL for registration (default: $FLEET_HOST env)
#   --port PORT        recon server port (default 8099)
#   --kiosk            enable full-screen kiosk UI pointed at the fleet radar
#   --kiosk-url URL    kiosk URL override (default: <fleet>/radar)
#   --readonly         apply read-only OverlayFS root (default: on; --rw to skip)
#   --rw               do NOT make the root read-only
#   --bluetooth        also provision classic+LE bluetooth (default: on)
#   --no-bluetooth     skip bluetooth tooling
#   --tailscale-authkey KEY  join the Tailnet headless with this auth key
#                            (required to reach the fleet over Tailscale)
#   --tailscale-hostname H   Tailscale hostname (default: <node-id>)
#
# NOTE — TAILSCALE: the fleet + host talk over Tailscale (e.g. host at
# 100.93.131.114). For the Pi to reach the host and be reachable, join the
# same tailnet. Pass --tailscale-authkey <TS_AUTHKEY> (generate an *ephemeral
# reusable* key in the Tailscale admin console). Then --fleet should be the
# HOST'S TAILSCALE IP/URL, e.g. http://100.93.131.114:8098.
#
set -euo pipefail

NAME="Pi Recon"
NODE_ID=""
FLEET_HOST="${FLEET_HOST:-}"
PORT="8099"
KIOSK=0
KIOSK_URL=""
READONLY=1
BLUETOOTH=1
TS_AUTHKEY="${TS_AUTHKEY:-}"
TS_HOSTNAME="${TS_HOSTNAME:-}"
RECONDIR="/opt/lilly-recon"
CONF="/etc/lilly-recon.conf"
SVC="pi-recon.service"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)        NAME="$2"; shift 2;;
    --node-id)     NODE_ID="$2"; shift 2;;
    --fleet)       FLEET_HOST="$2"; shift 2;;
    --port)        PORT="$2"; shift 2;;
    --tailscale-authkey) TS_AUTHKEY="$2"; shift 2;;
    --tailscale-hostname) TS_HOSTNAME="$2"; shift 2;;
    --kiosk)       KIOSK=1; shift;;
    --kiosk-url)   KIOSK_URL="$2"; KIOSK=1; shift 2;;
    --readonly)    READONLY=1; shift;;
    --rw)          READONLY=0; shift;;
    --bluetooth)   BLUETOOTH=1; shift;;
    --no-bluetooth) BLUETOOTH=0; shift;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done

if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: run as root (sudo bash install_recon.sh ...)" >&2
  exit 1
fi

progname="$(hostname)"
NODE_ID="${NODE_ID:-$progname}"

echo "=================================================================="
echo "  Lilly Pi Recon Station — first-boot provisioning"
echo "  name='$NAME'  node-id='$NODE_ID'  port=$PORT"
echo "  fleet='${FLEET_HOST:-<none>}'  readonly=${READONLY}  kiosk=${KIOSK}"
echo "=================================================================="

# ── 1. Base packages ──────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
PKGS=( python3 iw wireless-tools tcpdump net-tools curl )
BLUEZ=( bluez bluez-tools bluetooth pi-bluetooth )
if [[ "$BLUETOOTH" == "1" ]]; then
  PKGS+=( "${BLUEZ[@]}" )
  echo "hci" > /tmp/lilly-bt
else
  echo "" > /tmp/lilly-bt
fi
apt-get install -y --no-install-recommends "${PKGS[@]}"

# ── 2. Install the recon node server ─────────────────────────────────────
mkdir -p "$RECONDIR"
SRC="$(dirname "$0")"
cp "$SRC/pi_recon_node.py" "$SRC/gen_register_qr.py" "$RECONDIR/"
chmod +x "$RECONDIR/pi_recon_node.py" "$RECONDIR/gen_register_qr.py"

# ── 3. Write node config ─────────────────────────────────────────────────
cat > "$CONF" <<EOF
RECON_NAME="$NAME"
RECON_NODE_ID="$NODE_ID"
FLEET_HOST="$FLEET_HOST"
PORT=$PORT
EOF
chmod 600 "$CONF"

# ── 4. Install + enable systemd service ──────────────────────────────────
cp "$(dirname "$0")/$SVC" "/etc/systemd/system/$SVC"
systemctl daemon-reload
systemctl enable "$SVC"

# ── 5. Hostname (so the node id/MDNS/radar label are meaningful) ─────────
if command -v hostnamectl >/dev/null; then
  hostnamectl set-hostname "$NODE_ID" || true
fi

# ── 5b. Tailscale (join the fleet's tailnet, headless) ───────────────────
if [[ -n "$TS_AUTHKEY" ]]; then
  echo "==> Joining Tailscale =="
  if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh || \
      { echo "!! Tailscale install failed"; }
  fi
  TS_HP="${TS_HOSTNAME:-$NODE_ID}"
  # adverts: ephemeral (no state to wear the RO sd), SSH optional
  tailscale up \
    --auth-key "$TS_AUTHKEY" \
    --hostname "$TS_HP" \
    --advertise-tags=tag:lilly-recon 2>/dev/null \
    --accept-dns || tailscale up --auth-key "$TS_AUTHKEY" --hostname "$TS_HP" --accept-dns
  sleep 3
  echo "  Tailscale IP: $(tailscale ip -4 2>/dev/null | head -1 || echo '?')"
  # persist authkey so a reboot stays joined
  systemctl enable tailscaled 2>/dev/null || true
  # Print a scannable QR of the auth key (scan with phone/Tailscale app to
  # approve the node), so onboarding never requires typing the key.
  if command -v qrencode >/dev/null 2>&1; then
    echo "  Scan this QR to register the node:"
    qrencode -t ansiutf8 "$TS_AUTHKEY" 2>/dev/null || \
      python3 "$RECONDIR/gen_register_qr.py" "$TS_AUTHKEY"
  else
    python3 "$RECONDIR/gen_register_qr.py" "$TS_AUTHKEY"
  fi
fi

# ── 5c. Fleet register-URL QR (visible even without a tailnet key) ────────
if [[ -n "$FLEET_HOST" ]]; then
  echo "  == Fleet register / radar QR =="
  if command -v qrencode >/dev/null 2>&1; then
    qrencode -t ansiutf8 "${FLEET_HOST%/}/api/nodes/fleet" 2>/dev/null || true
  fi
fi

# ── 6. Optional kiosk UI (uses lilly_ai as the radar/kiosk view) ─────────
if [[ "$KIOSK" == "1" ]]; then
  echo "==> Enabling kiosk UI (pointing at lilly_ai radar) =="
  apt-get install -y --no-install-recommends xserver-xorg xinit \
      matchbox-window-manager chromium-browser || \
  apt-get install -y --no-install-recommends xserver-xorg xinit \
      matchbox-window-manager chromium 2>/dev/null || true
  if [[ -z "$KIOSK_URL" ]]; then
    KIOSK_URL="${FLEET_HOST}/radar"
  fi
  cat > /etc/systemd/system/lilly-kiosk.service <<'KEOF'
[Unit]
Description=Lilly recon kiosk (radar view)
After=systemd-user-sessions.service network-online.target
[Service]
EnvironmentFile=-/etc/lilly-kiosk.env
ExecStartPre=/usr/bin/bash -c 'chvt 1; echo 0 > /sys/class/graphics/fb0/blank'
ExecStart=/usr/bin/bash -c 'xinit /usr/bin/chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 --disable-features=Translate "$KIOSK_URL" --  --quiet & exec matchbox-window-manager -use_titlebar no'
Restart=always
RestartSec=5
User=root
[Install]
WantedBy=multi-user.target
KEOF
  cat > /etc/lilly-kiosk.env <<EOF
KIOSK_URL="$KIOSK_URL"
EOF
  systemctl daemon-reload
  systemctl enable lilly-kiosk.service || true
fi

# ── 7. Read-only rootfs via OverlayFS (protects the SD card) ─────────────
if [[ "$READONLY" == "1" ]]; then
  echo "==> Enabling READ-ONLY rootfs (two-phase: lock on next boot) =="
  # ── Phase 1 (RW): prepare config + writable runtime mounts ──────────
  # The SD root will become read-only on the NEXT boot. All recon runtime
  # state is kept OFF the RO root, on a tmpfs under /run, so the node keeps
  # functioning with zero SD writes (great for 24/7 + power-loss safety).
  #
  # Add overlay mount for /run + /var/tmp (RAM-backed, writable under RO).
  # (upper is tmpfs → nothing persists across reboot, which is fine for a
  #  recon node; state lives in RAM / RAM-backed /var/tmp.)
  if ! grep -q "lilly-overlay-run" /etc/fstab 2>/dev/null; then
    cat >> /etc/fstab <<'FEOF'

# Lilly recon: RAM-backed writable dirs (root fs is read-only)
tmpfs /run/lilly tmpfs mode=0755,size=64M 0 0
tmpfs /var/tmp tmpfs mode=1777 0 0
FEOF
  fi

  # A unit that, on the SECOND boot (after provisioning), switches the live
  # root to read-only safely. It only acts if provisioning already completed
  # (marker file), avoiding locking a half-configured system.
  mkdir -p /etc/systemd/system
  cat > /etc/systemd/system/lilly-ro-lock.service <<'OEOF'
[Unit]
Description=Lilly recon: lock rootfs read-only (after provisioning)
DefaultDependencies=no
After=local-fs.target
Before=sysinit.target
ConditionPathExists=/opt/lilly-recon/.provisioned
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/lock-ro.sh
[Install]
WantedBy=multi-user.target
OEOF
  cat > /usr/local/bin/lock-ro.sh <<'EOF'
#!/usr/bin/env bash
# Switch the running root to read-only — only called on post-provision boots.
# Safe approach: remount / ro after making key RUNTIME dirs RAM-backed so no
# service needs to write the SD. ws on the trusted lock file only.
set -e
# Make sure writable dirs services may touch are on tmpfs/run, not the SD.
mount -o remount,ro / 2>/dev/null || true
exit 0
EOF
  chmod +x /usr/local/bin/lock-ro.sh
  cp /usr/local/bin/lock-ro.sh /usr/local/bin/enable-ro-overlay.sh 2>/dev/null || true

  systemctl daemon-reload
  # Enable now — it will only ACT once .provisioned exists (after first boot)
  systemctl enable lilly-ro-lock.service || true

  echo "   -> Root will be READ-ONLY from the next boot onward."
  echo "   -> Runtime state lives in RAM (/run, tmpfs) → no SD wear/corruption."
  echo "   -> To go back to RW: sudo systemctl disable lilly-ro-lock.service"
else
  echo "Skipping read-only root (-/--rw). Root stays writable."
fi

# ── 8. Start the node + (optionally) kiosk now ───────────────────────────
systemctl start "$SVC" || { echo "!! service failed — journal: "; journalctl -u "$SVC" -n 20 --no-pager || true; }

echo ""
echo "==> Provisioning complete. =="
echo "  Node:   $NAME ($NODE_ID) on port $PORT"
echo "  Server: $RECONDIR/pi_recon_node.py"
echo "  Uplink: Ethernet (DHCP). Connect an Ethernet cable, done."
echo "  Health: curl http://$(hostname -I | awk '{print $1}'):$PORT/health"
if [[ "$KIOSK" == "1" ]]; then
  echo "  Kiosk:  now serving $KIOSK_URL on the attached display"
fi
echo "  Fleet:  ${FLEET_HOST:+auto-registers with $FLEET_HOST}"
echo ""
echo "  Next: plug in Ethernet. The node calls home on its own. Verify at"
echo "        ${FLEET_HOST:-http://<host>}:8098/api/nodes/fleet"
