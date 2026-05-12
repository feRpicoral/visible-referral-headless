# visible-referral-headless

A GitHub-Actions-powered bot that posts your **Visible** referral code as a comment on the latest [r/Visible](https://www.reddit.com/r/Visible) biweekly referral megathread — **without using the Reddit API**.

It drives the regular Reddit web UI in headless Chromium via [Playwright](https://playwright.dev/python/), authenticated by a `storage_state.json` you capture once locally. This is the fallback for the [API-based bot](https://github.com/feRpicoral/visible-referral-bot) for accounts that haven't been approved under Reddit's 2025 Responsible Builder Policy.

- Runs daily on a free GitHub Actions cron (12:00 UTC).
- Idempotent: if you've already commented on the open megathread, it does nothing.
- Picks a different message template each time from `messages.yaml`.
- Failure screenshots are uploaded as a workflow artifact so you can diagnose CAPTCHAs, expired sessions, or UI drift after the fact.

## Caveats (read first)

- **Account risk.** Driving the web UI looks more like a real user than the API path, but it is still automation. Use a dedicated, low-stakes Reddit account, not your main.
- **Session expires.** The captured `storage_state.json` is a cookie jar. Reddit invalidates sessions every few weeks, sometimes sooner. When the workflow starts failing with "session expired", re-capture (see below).
- **2FA.** Handled once during the interactive capture step; afterwards the cookies carry you.
- **CAPTCHAs.** If Reddit serves a challenge, the bot screenshots and exits non-zero. There's no auto-bypass — you'd typically re-run after some time, ideally from a different IP if it keeps happening.
- **UI fragility.** Reddit ships frontend changes often. If a locator stops matching, the bot will time out and screenshot; you'll need to update the selectors in `src/main.py`.

## Setup

1. **Fork this repo** on GitHub.

2. Use a **dedicated Reddit account** with some prior activity (very new accounts get auto-removed by many subs).

3. **Locally:**

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

4. **Capture your Reddit session:**

   ```bash
   python scripts/capture_session.py
   ```

   A Chromium window opens at the Reddit login page. Sign in (handle 2FA if prompted), wait until you land on the Reddit home page, then come back to the terminal and press Enter. The script writes `storage_state.json`.

5. **Base64-encode the storage state:**

   ```bash
   # macOS
   base64 -i storage_state.json | pbcopy

   # Linux
   base64 -w0 storage_state.json
   ```

6. **Add these 3 GitHub Secrets** on your fork (Settings → Secrets and variables → Actions → New repository secret):

   | Secret | Value |
   | --- | --- |
   | `REDDIT_STORAGE_STATE_B64` | the base64 string from step 5 |
   | `REDDIT_USERNAME` | your Reddit username, no `/u/` |
   | `REFERRAL_CODE` | e.g. `69GHWFW` |

7. **Enable Actions** on your fork (GitHub disables them on new forks by default — one click in the Actions tab).

8. *(optional)* Edit `messages.yaml` to rewrite the templates in your own voice.

9. **Test before going live:** Actions → **Post Visible referral** → **Run workflow** → tick **Log the message but don't actually post** → run. Check the log shows the matched megathread URL and the formatted message containing your code and link.

10. Uncheck the dry-run box, run again, and verify your comment appears on the megathread.

After that, the daily cron takes over.

## Local dry-run

```bash
DRY_RUN=1 \
REDDIT_STORAGE_STATE_B64=$(base64 -i storage_state.json) \
REDDIT_USERNAME=your_username \
REFERRAL_CODE=YOURCODE \
python -m src.main
```

The bot will find the megathread, check whether you've already commented, then log the message it would post and exit 0 without posting.

## How it runs in CI

- **Daily cron:** 12:00 UTC, via `.github/workflows/post-referral.yml`.
- **Manual dispatch:** Actions tab → **Post Visible referral** → **Run workflow**, with a `dry_run` checkbox.
- **Failure artifact:** when the bot exits non-zero, any screenshots written to `screenshots/` (e.g. on CAPTCHA, timeout, or unexpected errors) are uploaded as a workflow artifact called `failure-screenshots`.

## Refreshing an expired session

When the workflow starts failing with `Reddit redirected to login`, re-run the capture and update the secret:

```bash
python scripts/capture_session.py
base64 -i storage_state.json | pbcopy  # macOS
```

Then update the `REDDIT_STORAGE_STATE_B64` secret on GitHub with the new value.

## Troubleshooting

- **CAPTCHA challenge detected.** The session may be flagged. Re-capture from a different IP if you can, or just wait and re-run.
- **Session expired (`Reddit redirected to login`).** Re-run `capture_session.py` and update the secret.
- **No megathread found.** r/Visible may have temporarily un-stickied the thread, or the title wording changed. Compare the live title against `TITLE_REGEX` in `src/main.py` and open a PR to update the regex if needed.
- **Playwright timeout / locator not found.** Reddit changed something in the UI. The locators in `src/main.py` (composer textbox, submit button, Markdown toggle, `shreddit-post` post-title slot) likely need updating. Failure screenshots in the workflow artifact will show what the page actually looked like.

## Code quality

[Ruff](https://docs.astral.sh/ruff/) for formatting and linting; pytest for the pure-helper tests. Same checks run in CI.

```bash
ruff format .
ruff check . --fix
pytest
```
