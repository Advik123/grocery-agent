# playwright_agent.py
# Uses Playwright to open an OurGroceries list and add items via the browser.
# Phase 3: ngrok tunneling, multi-store parallel requests, and Siri App Intents
# will reuse this function without changes.

from pathlib import Path

from playwright.async_api import async_playwright

# Persistent browser profile directory — stores login cookies between runs.
PROFILE_DIR = Path(__file__).parent / ".browser-profile"


async def add_to_list(url: str, items: dict[str, int]) -> list[str]:
    """Open the OurGroceries list at *url* and add each item.

    Items are formatted as "name x<qty>" (e.g. "eggs x3").

    Returns a list of the formatted item strings that were submitted.

    Requires that .browser-profile/ exists with a valid login session
    (created via login.py).
    """
    if not PROFILE_DIR.exists():
        raise RuntimeError(
            "No browser profile found.  Run login.py first "
            "to create a login session."
        )

    added: list[str] = []

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url)
        await page.wait_for_load_state("networkidle")

        # If the page redirected to sign-in, the session expired.
        if "/sign-in" in page.url:
            await context.close()
            raise RuntimeError(
                "Login session expired.  Re-run login.py to log in again."
            )

        for name, qty in items.items():
            formatted = f"{name} x{qty}"

            # Click the "+" button to open the "Add an item" panel.
            await page.locator("button.addItemButton").click()

            # The input that appears is #addItemName (type="search").
            input_field = page.locator("#addItemName")
            await input_field.wait_for(state="visible", timeout=5000)
            await input_field.fill(formatted)
            await input_field.press("Enter")

            # Wait for the input to clear / item to appear in the list,
            # confirming the submission went through.
            await page.wait_for_timeout(1000)

            added.append(formatted)

        await context.close()

    return added
