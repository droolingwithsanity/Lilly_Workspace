#!/usr/bin/env bash
# Automated Demo Workflow
# Orchestrates the phone via sensor server, captures screenshots at each step.
set -e

SENSOR="http://100.115.234.87:8099"
SSH_HOST="${TERMUX_SSH_HOST:-}"
SSH_USER="${TERMUX_SSH_USER:-}"
OUTDIR="screenshots"
mkdir -p "$OUTDIR"

say() { echo -e "\n\033[1;36m▶ $1\033[0m"; }
step()  { say "Step $1: $2"; }

# shell — run command on phone via HTTP sensor server (primary) or SSH (fallback)
shell() {
  local result=""
  if [ -n "$SSH_HOST" ] && [ -n "$SSH_USER" ]; then
    result=$(ssh "$SSH_USER@$SSH_HOST" -p "${TERMUX_SSH_PORT:-8022}" -o StrictHostKeyChecking=no "$1" 2>/dev/null || true)
  else
    result=$(curl -s --connect-timeout 3 --max-time 8 "$SENSOR/shell" -d "cmd=$1" 2>/dev/null || true)
    if [ -n "$result" ]; then
      result=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('output',''))" 2>/dev/null || true)
    fi
  fi
  echo "$result"
}

# screenshot — capture phone screen and save locally as PNG
screenshot() {
  local name="$1" label="$2"
  echo "  📸 Capturing $name ..."
  local b64
  b64=$(shell "screencap -p /sdcard/.lilly_demo.png && base64 /sdcard/.lilly_demo.png" 2>/dev/null || true)
  if [ -z "$b64" ] || [ "$b64" = "" ]; then
    echo "  ⚠ Phone not reachable — generating placeholder"
    _gen_placeholder "screenshots/${name}" "$label"
    return
  fi
  # Validate it looks like base64
  if echo "$b64" | base64 -d > "screenshots/${name}" 2>/dev/null; then
    local size
    size=$(stat -c%s "screenshots/${name}" 2>/dev/null || stat -f%z "screenshots/${name}" 2>/dev/null || echo "0")
    if [ "$size" -gt 1000 ] 2>/dev/null; then
      echo "  ✓ saved screenshots/${name} (${size} bytes)"
    else
      echo "  ⚠ Screenshot too small (${size}b) — using placeholder"
      _gen_placeholder "screenshots/${name}" "$label"
    fi
  else
    echo "  ⚠ base64 decode failed — generating placeholder"
    _gen_placeholder "screenshots/${name}" "$label"
  fi
}

# placeholder generator (when phone is offline)
_gen_placeholder() {
  local path="$1" label="$2"
  python3 -c "
from PIL import Image, ImageDraw, ImageFont
W,H=360,760
img=Image.new('RGB',(W,H),(10,14,26))
d=ImageDraw.Draw(img)
try: f=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',14)
except: f=ImageFont.load_default()
try: big=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',48)
except: big=f
d.rectangle([(0,0),(W,28)],fill=(20,28,46))
d.text((10,6),'9:41',fill=(200,214,229),font=f)
d.text((W-60,6),'DEMO',fill=(87,101,116),font=f)
d.text((W//2,340),'[screenshot]',fill=(87,101,116),font=big,anchor='mm')
d.text((W//2,400),'$label',fill=(87,101,116),font=f,anchor='mm')
d.text((W//2,430),'Replace with real screenshot',fill=(58,74,90),font=f,anchor='mm')
img.save('${path}')
print(f'  ✓ placeholder ${path}')
" 2>/dev/null || true
}

# ═══════════════════════════════════════════════
# WORKFLOW
# ═══════════════════════════════════════════════

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Lily Automated Demo Workflow       ║"
echo "║   Sensor: $SENSOR "
echo "╚══════════════════════════════════════╝"

step 1 "Avatar Home — Open Lily in browser"
screenshot "step1-home.png" "Browser loads Lily UI"

step 2 "Chat — What do you sense?"
shell "input tap 200 700" 2>/dev/null       # tap chat box
shell "input text 'what do you sense?'" 2>/dev/null
sleep 1
shell "input keyevent 66" 2>/dev/null        # press enter
sleep 3  # wait for avatar response
screenshot "step2-chat.png" "Avatar responds with sensor context"

step 3 "Camera — Open camera in PiP"
shell "am start -a android.media.action.IMAGE_CAPTURE -p com.android.camera" 2>/dev/null
sleep 2
shell "input keyevent KEYCODE_HOME" 2>/dev/null  # home to trigger PiP
sleep 1
screenshot "step3-camera-pip.png" "Camera viewfinder in PiP"

step 4 "Object Detection — What do you see?"
# browser already sent a frame to /api/vision/browser
screenshot "step4-detection.png" "YOLO detection: person, laptop, cup"

step 5 "Maps Overlay — Find Pho"
shell "am start -a android.intent.action.VIEW -d 'geo:0,0?q=pho+near+me' -p com.google.android.apps.maps --windowingMode 5 --bounds '540,0,1080,2000'" 2>/dev/null
sleep 3
screenshot "step5-maps-overlay.png" "Maps in half-screen overlay"

step 6 "Navigate — Scroll and tap the overlay"
shell "input keyevent 20; input keyevent 20; input keyevent 20; input keyevent 20; input keyevent 20; input keyevent 20" 2>/dev/null
sleep 1
screenshot "step6-navigate.png" "Scrolling results in overlay"

step 7 "Notifications — Priority alerts"
shell "input keyevent 40" 2>/dev/null  # pull notification panel
sleep 1
screenshot "step7-notification.png" "OS-priority notification alert"
shell "input keyevent KEYCODE_HOME" 2>/dev/null  # go home

step 8 "Full Screen — Maximize the overlay"
shell "am start -p com.google.android.apps.maps" 2>/dev/null  # re-launch full screen
sleep 2
screenshot "step8-fullscreen.png" "Maps expanded to full screen"

step 9 "YouTube PiP + Sensor Skills"
shell "am start -a android.intent.action.VIEW -d 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' -p com.google.android.youtube" 2>/dev/null
sleep 3
shell "input keyevent KEYCODE_HOME" 2>/dev/null
sleep 1
screenshot "step9-youtube-pip.png" "YouTube in PiP with chat"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Done! 9 screenshots captured       ║"
echo "║   Check screenshots/ directory       ║"
echo "║                                      ║"
echo "║   View: open DEMO.html in browser    ║"
echo "╚══════════════════════════════════════╝"
ls -lh screenshots/
