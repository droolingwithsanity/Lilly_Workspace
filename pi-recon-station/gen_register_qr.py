#!/usr/bin/env python3
"""
gen_register_qr.py — print/encode a scannable QR for headless node onboarding
═══════════════════════════════════════════════════════════════════════════
For a headless Raspberry Pi recon node, the simplest "register" is a QR that
encodes the TAILSCALE AUTH KEY. Scan it with your phone's camera (or the
Tailscale app) to approve the node into your tailnet, or read it back to type
the key into the Pi.

  • Print to console (PNG optional), or
  • Output a PNG you can copy to the web share / print onto the case.

USAGE
  python3 gen_register_qr.py <TAILSCALE_AUTHKEY> [out.png]
  python3 gen_register_qr.py --data "http://<host>:8098/api/nodes/register" [out.png]

Depends on `qrencode` (console/ANSI) or `qrcode[pil]` (PNG).
"""

import argparse
import subprocess
import sys


def console_qr(text):
    """Render an ANSI block QR in the terminal (via qrencode -t ansiutf8)."""
    p = subprocess.run(
        ["qrencode", "-t", "ansiutf8", text], capture_output=True, text=True
    )
    if p.returncode == 0:
        sys.stdout.write(p.stdout)
        return True
    sys.stderr.write("qrencode not available; try `apt install qrencode`\n")
    return False


def png_qr(text, path):
    try:
        import qrcode  # qrcode[pil]

        img = qrcode.make(text)
        img.save(path)
        print(f"wrote {path}")
        return True
    except Exception as e:  # noqa: BLE001
        p = subprocess.run(
            ["qrencode", "-o", path, text], capture_output=True, text=True
        )
        if p.returncode == 0:
            print(f"wrote {path} (via qrencode)")
            return True
        print(f"PNG failed: {e}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description="QR code for headless node register")
    ap.add_argument("payload", help="text to encode (e.g. Tailscale auth key or URL)")
    ap.add_argument("out", nargs="?", default=None, help="optional PNG path")
    ap.add_argument(
        "--data",
        dest="alt",
        help="alternative: encode a URL/string instead of positional",
    )
    args = ap.parse_args()
    text = args.alt or args.payload
    if not text:
        ap.error("nothing to encode")
    print("Encode: " + (text[:24] + "…" if len(text) > 24 else text))
    console_qr(text)
    if args.out:
        png_qr(text, args.out)


if __name__ == "__main__":
    main()
