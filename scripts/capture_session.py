"""One-time helper: walks you through a manual Reddit login and saves
storage_state.json for the bot to reuse.

Run once per session refresh. After it writes the file, base64-encode it and
paste the result into the REDDIT_STORAGE_STATE_B64 GitHub secret.
"""

from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1280, "height": 800}
OUTPUT_PATH = "storage_state.json"


def main() -> None:
    print(
        "This will open a Chromium window pointed at Reddit's login page.\n"
        "Sign in (handle 2FA if prompted) until you land on the Reddit home\n"
        "page, then return to this terminal and press Enter."
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()
        page.goto("https://www.reddit.com/login")
        input("Press Enter once you're fully logged in and see the Reddit home page... ")
        context.storage_state(path=OUTPUT_PATH)
        browser.close()

    print(
        f"\nWrote {OUTPUT_PATH}.\n"
        "Next: base64-encode it and put the result in the GitHub secret\n"
        "REDDIT_STORAGE_STATE_B64.\n\n"
        "  macOS:  base64 -i storage_state.json | pbcopy\n"
        "  Linux:  base64 -w0 storage_state.json\n"
    )


if __name__ == "__main__":
    main()
