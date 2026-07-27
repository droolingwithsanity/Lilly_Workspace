#!/usr/bin/env python3
"""Capture real screenshots of all views from the running Lilly app."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SITE = "http://localhost:8098"
OUT = Path("/tmp/screenshots_capture")
OUT.mkdir(exist_ok=True)
FINAL = Path("screenshots")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--disable-gpu"])
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        await page.goto(SITE, wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)

        # 1. Start screen / avatar picker
        await page.screenshot(path=str(OUT / "step1-home.png"))
        print("  step1-home.png")

        # 2. Click a character to preview (Puppy)
        try:
            puppy = page.locator("#previewPuppy")
            if await puppy.count():
                await puppy.click()
                await page.wait_for_timeout(1000)
        except:
            pass
        await page.screenshot(path=str(OUT / "step2-chat.png"))
        print("  step2-chat.png")

        # 3. Try to bypass login and enter main app
        try:
            await page.evaluate("confirmPickerBypass()")
            await page.wait_for_timeout(2000)
        except:
            try:
                await page.evaluate("confirmPicker()")
                await page.wait_for_timeout(2000)
            except:
                pass

        await page.screenshot(path=str(OUT / "step3-camera-pip.png"))
        print("  step3-camera-pip.png")

        # 4. Toggle dashboard
        try:
            await page.evaluate("toggleDashboard()")
            await page.wait_for_timeout(1000)
            await page.screenshot(path=str(OUT / "step4-detection.png"))
            print("  step4-detection.png")
            await page.evaluate("toggleDashboard()")
            await page.wait_for_timeout(500)
        except Exception as e:
            print(f"  dashboard failed: {e}")
            await page.screenshot(path=str(OUT / "step4-detection.png"))

        # 5. Toggle camera view
        try:
            await page.evaluate("toggleCameraView()")
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT / "step5-maps-overlay.png"))
            print("  step5-maps-overlay.png")
            await page.evaluate("toggleCameraView()")
            await page.wait_for_timeout(500)
        except Exception as e:
            print(f"  camera failed: {e}")
            await page.screenshot(path=str(OUT / "step5-maps-overlay.png"))

        # 6. Toggle char switcher
        try:
            await page.evaluate("toggleCharSwitcher()")
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(OUT / "step6-navigate.png"))
            print("  step6-navigate.png")
            await page.evaluate("closeCharSwitcher()")
            await page.wait_for_timeout(500)
        except Exception as e:
            print(f"  char switcher failed: {e}")
            await page.screenshot(path=str(OUT / "step6-navigate.png"))

        # 7. Toggle conversation mode
        try:
            await page.evaluate("toggleConversationMode()")
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(OUT / "step7-notification.png"))
            print("  step7-notification.png")
            await page.evaluate("toggleConversationMode()")
            await page.wait_for_timeout(500)
        except Exception as e:
            print(f"  conv mode failed: {e}")
            await page.screenshot(path=str(OUT / "step7-notification.png"))

        # 8. Toggle coding mode
        try:
            await page.evaluate("toggleCodingMode()")
            await page.wait_for_timeout(800)
            await page.screenshot(path=str(OUT / "step8-fullscreen.png"))
            print("  step8-fullscreen.png")
        except Exception as e:
            print(f"  coding mode failed: {e}")
            await page.screenshot(path=str(OUT / "step8-fullscreen.png"))

        # 9. Final state
        try:
            await page.evaluate("toggleCodingMode()")
            await page.wait_for_timeout(800)
        except:
            pass
        await page.screenshot(path=str(OUT / "step9-youtube-pip.png"))
        print("  step9-youtube-pip.png")

        await browser.close()
        print(f"\nDone! {len(list(OUT.glob('*.png')))} screenshots in {OUT}/")

asyncio.run(main())
