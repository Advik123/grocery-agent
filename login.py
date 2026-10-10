 # login.py
# One-time login helper.  Opens OurGroceries in a headed browser so you can
# sign in manually.  The session is saved in .browser-profile/ and reused
# by app.py on subsequent runs.

from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / ".browser-profile"


def main() -> None:
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.ourgroceries.com/sign-in")

        print()
        print("=" * 60)
        print("  Browser opened.  Log in to OurGroceries.")
        print("  Once you see your list overview, press Enter here.")
        print("=" * 60)
        print()

        input()

        context.close()

    print("Login session saved to .browser-profile/.  You can now run app.py.")


if __name__ == "__main__":
    main()
