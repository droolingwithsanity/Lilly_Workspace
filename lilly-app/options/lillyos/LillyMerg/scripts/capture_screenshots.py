"""
Capture screenshots of all live portfolio projects using Playwright.
Run: python scripts/capture_screenshots.py
Or via Docker: docker run --rm --network=host mcr.microsoft.com/playwright python -c ...
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api.portfolio_data import PORTFOLIO

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'public', 'screenshots')

SITES = [
    ("lillymerg", "http://100.93.131.114:3006"),
    ("keke_fileshare", "http://100.93.131.114:8095"),
    ("qwen_translate", "http://100.93.131.114:8091"),
    ("familywall", "http://100.93.131.114:8100"),
    ("project_kiki", "http://100.93.131.114:8086"),
    ("media_gallery", "http://100.93.131.114:8092"),
    ("ecommerce_store", "http://100.93.131.114:8085"),
    ("trading_bot", "http://100.93.131.114:8089"),
    ("taxhacker", "http://100.93.131.114:7331"),
    ("file_manager", "http://100.93.131.114:8084"),
    ("wordpress_store", "http://100.93.131.114:8090"),
    ("gitea", "http://100.93.131.114:3000"),
    ("jellyfin", "http://100.93.131.114:8096"),
    ("dify", "http://100.93.131.114:9061"),
    ("buildy_seo", "http://100.93.131.114:9062"),
    ("dws_mern", "http://100.93.131.114:5000"),
    # Archived projects — use placeholder cards
    ("lillyos", None),
    ("lillyshader", None),
    ("spawn", None),
    ("local_llm_gui", None),
    ("genesis", None),
    ("ader", None),
]


def capture():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Install with: pip install playwright && playwright install chromium")
        # Generate placeholder screenshots
        _generate_placeholders()
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
        )

        for name, url in SITES:
            path = os.path.join(OUTPUT_DIR, f"{name}.png")
            if url is None:
                _generate_placeholder(name, path)
                continue

            print(f"Capturing {name} ({url})...")
            try:
                page = context.new_page()
                page.goto(url, timeout=15000, wait_until="networkidle")
                time.sleep(2)
                page.screenshot(path=path, full_page=True)
                page.close()
                print(f"  ✓ Saved to {path}")
            except Exception as e:
                print(f"  ✗ Failed: {e}")
                _generate_placeholder(name, path)

        browser.close()

    print(f"\nAll screenshots saved to {OUTPUT_DIR}")


def _generate_placeholder(name, path):
    """Generate a simple placeholder image using PIL if available, otherwise just text."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (1280, 800), '#F5F5F4')
        draw = ImageDraw.Draw(img)
        draw.text((640, 400), f"{name}", fill="#888888", anchor="mm")
        draw.text((640, 440), "Screenshot unavailable", fill="#AAAAAA", anchor="mm")
        img.save(path, 'PNG')
        print(f"  ⚡ Placeholder saved to {path}")
    except ImportError:
        with open(path.replace('.png', '.txt'), 'w') as f:
            f.write(f"Placeholder for {name}")
        print(f"  ⚡ Text placeholder for {name}")


def _generate_placeholders():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for name, url in SITES:
        path = os.path.join(OUTPUT_DIR, f"{name}.png")
        _generate_placeholder(name, path)


if __name__ == "__main__":
    capture()
