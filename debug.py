# debug.py
# Tries multiple approaches to open the "Add an item" dialog, dumps DOM after each.

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE_DIR = Path(__file__).parent / ".browser-profile"
URL = "https://www.ourgroceries.com/your-lists/list/MadL3iwy0vlLuXW1NAypeh"


async def main() -> None:
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URL)
        await page.wait_for_load_state("networkidle")

        # --- Approach 1: click addItemButton ---
        print("=== Approach 1: click addItemButton ===")
        btn = page.locator("button.addItemButton")
        await btn.click()
        await asyncio.sleep(2)
        await page.screenshot(path="debug-1-after-button-click.png", full_page=True)
        print("Saved screenshot 1")

        # --- Approach 2: click 'Add an item...' text ---
        print("\n=== Approach 2: click 'Add an item...' div ===")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        add_div = page.locator("div.add-an-item")
        if await add_div.count() > 0:
            await add_div.click()
            await asyncio.sleep(2)
            await page.screenshot(path="debug-2-after-div-click.png", full_page=True)
            print("Saved screenshot 2")

        # --- Approach 3: press 'A' hotkey ---
        print("\n=== Approach 3: press 'A' hotkey ===")
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        await page.keyboard.press("a")
        await asyncio.sleep(2)
        await page.screenshot(path="debug-3-after-hotkey.png", full_page=True)
        print("Saved screenshot 3")

        # Dump all inputs across the page now
        inputs = await page.locator("input").all()
        print(f"\nFound {len(inputs)} <input> elements:")
        for i, inp in enumerate(inputs):
            attrs = await inp.evaluate(
                "el => ({type: el.type, placeholder: el.placeholder, "
                "name: el.name, id: el.id, className: el.className})"
            )
            vis = await inp.is_visible()
            print(f"  [{i}] {attrs} visible={vis}")

        # Dump full HTML
        html = await page.content()
        Path("debug-after-all.html").write_text(html, encoding="utf-8")
        print("\nSaved: debug-after-all.html")

        print("\nDone. Close the browser window to exit.")
        # Wait for user to close browser
        try:
            await page.wait_for_event("close", timeout=120000)
        except Exception:
            pass
        await context.close()


asyncio.run(main())
