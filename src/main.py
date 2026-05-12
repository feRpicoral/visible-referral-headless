"""Post a Visible referral comment on r/Visible's biweekly megathread via Playwright.

Drives the regular Reddit web UI in headless Chromium, authenticated by a
captured storage_state. No Reddit API is used. Designed to run idempotently
from GitHub Actions on a daily schedule.
"""

import base64
import contextlib
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
MAX_FEED_SCROLLS = 8
USER_COMMENTS_FETCH_LIMIT = 100
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

    posts = page.locator("shreddit-post")
    seen: set[str] = set()

    for attempt in range(MAX_FEED_SCROLLS):
        count = posts.count()
        for i in range(count):
            post = posts.nth(i)
            title = _read_post_title(post)
            if not title or title in seen:
                continue
            seen.add(title)
            if TITLE_REGEX.search(title):
                permalink = post.get_attribute("permalink") or post.get_attribute("content-href")
                if permalink:
                    log.info("Matched megathread after scanning %d titles: %s", len(seen), title)
                    return _absolute_reddit_url(permalink)

        if len(seen) >= NEW_FALLBACK_LIMIT:
            break

        page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
        with contextlib.suppress(PlaywrightTimeoutError):
            page.wait_for_load_state("networkidle", timeout=4000)
        jitter_sleep(400, 900)

        new_count = posts.count()
        if new_count == count and attempt > 0:
            break

    log.info("Scanned %d unique posts, no megathread match.", len(seen))
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


def extract_post_id(megathread_url: str) -> str:
    after = megathread_url.split("/comments/", 1)[1]
    return after.split("/", 1)[0]


def has_user_commented(page: Page, username: str, megathread_post_id: str) -> bool:
    url = (
        f"https://www.reddit.com/user/{username}/comments.json"
        f"?limit={USER_COMMENTS_FETCH_LIMIT}&sort=new"
    )
    response = page.context.request.get(url)
    if not response.ok:
        raise RuntimeError(f"Failed to fetch user comments JSON ({url}): HTTP {response.status}")

    data = response.json()
    children = data.get("data", {}).get("children", [])
    target_link_id = f"t3_{megathread_post_id}"
    for child in children:
        cdata = child.get("data", {})
        if cdata.get("link_id") == target_link_id:
            permalink = cdata.get("permalink", "")
            log.info("Already commented on this megathread: https://www.reddit.com%s", permalink)
            return True

    log.info(
        "No prior comment on this megathread (scanned %d of u/%s's recent comments).",
        len(children),
        username,
    )
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

            megathread_id = extract_post_id(megathread_url)
            log.info("Found megathread: %s (id=%s)", megathread_url, megathread_id)

            if has_user_commented(page, username, megathread_id):
                return 0

            page.goto(megathread_url, wait_until="domcontentloaded")
            jitter_sleep()

            if check_for_captcha(page):
                take_screenshot(page, "captcha")
                log.error("CAPTCHA challenge detected — bailing.")
                return 1

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
