"""Post a Visible referral comment on r/Visible's biweekly megathread via Playwright.

Drives the regular Reddit web UI in headless Chromium, authenticated by a
captured storage_state. No Reddit API is used. Designed to run idempotently
from GitHub Actions on a daily schedule.
"""

import base64
import json
import logging
import os
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

SUBREDDIT_URL = "https://www.reddit.com/r/Visible/"
LOGIN_URL_PREFIX = "https://www.reddit.com/login"
TITLE_REGEX = re.compile(r"(?i)bi-?weekly\s+megathread.*referral\s+codes")
MESSAGES_PATH = Path(__file__).resolve().parent.parent / "messages.yaml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1280, "height": 800}
NEW_FALLBACK_LIMIT = 25
MAX_COMMENT_SCROLLS = 40
SCREENSHOT_DIR = Path("screenshots")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("visible-referral")


class SessionExpiredError(RuntimeError):
    """Raised when Reddit redirects us to the login page."""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        log.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


def load_templates() -> list[str]:
    data = yaml.safe_load(MESSAGES_PATH.read_text())
    templates = data.get("templates") if isinstance(data, dict) else None
    if not templates:
        log.error("No templates found in %s", MESSAGES_PATH)
        sys.exit(1)
    return templates


def decode_storage_state(b64: str) -> dict:
    return json.loads(base64.b64decode(b64).decode("utf-8"))


def select_message(templates: list[str], code: str, link: str) -> str:
    return random.choice(templates).format(code=code, link=link)


def jitter_sleep(min_ms: int = 200, max_ms: int = 800) -> None:
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def take_screenshot(page: Page, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    path = SCREENSHOT_DIR / f"{name}-{ts}.png"
    page.screenshot(path=str(path), full_page=True)
    log.info("Wrote screenshot: %s", path)
    return path


def check_for_captcha(page: Page) -> bool:
    selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="captcha"]',
        'iframe[title*="captcha" i]',
    ]
    for selector in selectors:
        if page.locator(selector).count() > 0:
            return True
    return page.get_by_text(re.compile(r"verify you are human", re.I)).count() > 0


def find_megathread_url(page: Page) -> str | None:
    page.goto(SUBREDDIT_URL, wait_until="domcontentloaded")

    if page.url.startswith(LOGIN_URL_PREFIX):
        log.error("Reddit redirected to login — storage_state is expired or invalid.")
        raise SessionExpiredError

    try:
        page.wait_for_selector("shreddit-post", timeout=15000)
    except PlaywrightTimeoutError:
        log.warning("No shreddit-post elements appeared on the subreddit page.")
        return None

    # Nudge lazy-loading so we scan more than the initial paint.
    page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
    jitter_sleep(800, 1500)
    page.evaluate("window.scrollTo(0, 0)")
    jitter_sleep(300, 600)

    posts = page.locator("shreddit-post")
    count = min(posts.count(), NEW_FALLBACK_LIMIT)
    log.info("Scanning %d posts for the megathread...", count)
    for i in range(count):
        post = posts.nth(i)
        title = _read_post_title(post)
        if title and TITLE_REGEX.search(title):
            permalink = post.get_attribute("permalink") or post.get_attribute("content-href")
            if permalink:
                log.info("Matched megathread: %s", title)
                return _absolute_reddit_url(permalink)

    return None


def _read_post_title(post) -> str | None:
    title = post.get_attribute("post-title")
    if title and title.strip():
        return title.strip()
    title_loc = post.locator('[slot="title"]').first
    if title_loc.count() > 0:
        text = title_loc.inner_text().strip()
        if text:
            return text
    return None


def _absolute_reddit_url(path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"https://www.reddit.com{path_or_url}"


def has_user_commented(page: Page, username: str) -> bool:
    selector = f'shreddit-comment[author="{username}" i]'
    for _ in range(MAX_COMMENT_SCROLLS):
        if page.locator(selector).count() > 0:
            log.info("Already commented as u/%s on this megathread.", username)
            return True
        page.evaluate("window.scrollBy(0, document.body.clientHeight)")
        jitter_sleep(500, 1200)
        at_bottom = page.evaluate(
            "window.scrollY + window.innerHeight >= document.body.scrollHeight - 10"
        )
        if at_bottom:
            if page.locator(selector).count() > 0:
                log.info("Already commented as u/%s on this megathread.", username)
                return True
            break
    return False


def post_comment(page: Page, body: str, code: str) -> None:
    composer = page.get_by_role("textbox", name=re.compile("comment", re.I)).first
    if composer.count() == 0:
        composer = page.get_by_placeholder(re.compile("Add a comment", re.I)).first
    composer.click()
    jitter_sleep()

    markdown_toggle = page.get_by_role("button", name=re.compile("markdown", re.I)).first
    if markdown_toggle.count() > 0:
        markdown_toggle.click()
        jitter_sleep()

    composer.press_sequentially(body, delay=random.randint(20, 60))
    jitter_sleep(400, 900)

    submit = page.get_by_role("button", name=re.compile(r"^(Comment|Post)$", re.I)).first
    submit.click()

    page.wait_for_selector(f'text="{code}"', timeout=15000)
    log.info("Comment posted; referral code visible in the page.")


def main() -> int:
    storage_state_b64 = require_env("REDDIT_STORAGE_STATE_B64")
    username = require_env("REDDIT_USERNAME")
    code = require_env("REFERRAL_CODE")
    dry_run = os.environ.get("DRY_RUN") == "1"

    link = f"https://www.visible.com/get/?{code}"
    templates = load_templates()

    try:
        storage_state = decode_storage_state(storage_state_b64)
    except Exception:
        log.exception("Failed to decode REDDIT_STORAGE_STATE_B64.")
        return 1

    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": True}
        channel = os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL")
        if channel:
            launch_kwargs["channel"] = channel
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            storage_state=storage_state,
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
        )
        page = context.new_page()
        try:
            megathread_url = find_megathread_url(page)
            if megathread_url is None:
                log.info("No megathread found on r/Visible, nothing to do.")
                return 0

            log.info("Found megathread: %s", megathread_url)
            page.goto(megathread_url, wait_until="domcontentloaded")
            jitter_sleep()

            if check_for_captcha(page):
                take_screenshot(page, "captcha")
                log.error("CAPTCHA challenge detected — bailing.")
                return 1

            if has_user_commented(page, username):
                return 0

            body = select_message(templates, code, link)

            if dry_run:
                log.info("DRY RUN — would post:\n%s", body)
                return 0

            post_comment(page, body, code)
            log.info("Done.")
            return 0
        except SessionExpiredError:
            return 1
        except PlaywrightTimeoutError:
            take_screenshot(page, "timeout")
            log.exception("Playwright timed out waiting for an element.")
            return 1
        except Exception:
            take_screenshot(page, "error")
            log.exception("Unexpected failure.")
            return 1
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
