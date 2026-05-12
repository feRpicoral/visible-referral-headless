"""Tests for pure helpers in src.main.

DOM-touching helpers (find_megathread_url, has_user_commented, post_comment,
check_for_captcha, take_screenshot) are exercised end-to-end via
`DRY_RUN=1 python -m src.main` rather than mocked here.
"""

import base64
import binascii
import json
import random

import pytest

from src.main import (
    TITLE_REGEX,
    decode_storage_state,
    extract_post_id,
    require_env,
    select_message,
)

# ---------------------------------------------------------------------------
# TITLE_REGEX
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Biweekly Megathread for Referral Codes - Please post your codes here!",
        "Bi-weekly Megathread for Referral Codes",
        "BIWEEKLY MEGATHREAD FOR REFERRAL CODES",
        "biweekly megathread for referral codes (Q1)",
        "Bi-Weekly  Megathread  for  Referral  Codes",
    ],
)
def test_title_regex_matches_megathreads(title):
    assert TITLE_REGEX.search(title)


@pytest.mark.parametrize(
    "title",
    [
        "Daily discussion thread",
        "Megathread: Coverage Map Updates",
        "Random user post about Visible",
        "Weekly Megathread for Referral Codes",  # not bi-weekly
        "Biweekly Megathread for Coverage Issues",  # wrong topic
    ],
)
def test_title_regex_does_not_match_unrelated(title):
    assert not TITLE_REGEX.search(title)


# ---------------------------------------------------------------------------
# require_env
# ---------------------------------------------------------------------------


def test_require_env_returns_value(monkeypatch):
    monkeypatch.setenv("VISIBLE_TEST_VAR", "bar")
    assert require_env("VISIBLE_TEST_VAR") == "bar"


def test_require_env_exits_when_missing(monkeypatch):
    monkeypatch.delenv("VISIBLE_TEST_VAR", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        require_env("VISIBLE_TEST_VAR")
    assert exc_info.value.code == 1


def test_require_env_exits_when_empty(monkeypatch):
    monkeypatch.setenv("VISIBLE_TEST_VAR", "")
    with pytest.raises(SystemExit) as exc_info:
        require_env("VISIBLE_TEST_VAR")
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# decode_storage_state
# ---------------------------------------------------------------------------


def test_decode_storage_state_round_trips_an_object():
    payload = {"cookies": [], "origins": []}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    assert decode_storage_state(encoded) == payload


def test_decode_storage_state_raises_on_malformed_base64():
    with pytest.raises(binascii.Error):
        decode_storage_state("not!valid!base64!@#$")


def test_decode_storage_state_raises_on_non_json_payload():
    encoded = base64.b64encode(b"this is not json").decode("ascii")
    with pytest.raises(json.JSONDecodeError):
        decode_storage_state(encoded)


# ---------------------------------------------------------------------------
# select_message
# ---------------------------------------------------------------------------


def test_select_message_returns_a_template_member():
    templates = ["A {code} {link}", "B {code} {link}", "C {code} {link}"]
    picked = select_message(templates, "CODE", "https://example.com")
    assert any(picked == t.format(code="CODE", link="https://example.com") for t in templates)


def test_select_message_substitutes_both_placeholders():
    templates = ["Hello {code} at {link}"]
    rendered = select_message(templates, "ABC123", "https://www.visible.com/get/?ABC123")
    assert "ABC123" in rendered
    assert "https://www.visible.com/get/?ABC123" in rendered
    assert "{code}" not in rendered
    assert "{link}" not in rendered


def test_select_message_is_deterministic_with_seeded_random():
    templates = ["one {code} {link}", "two {code} {link}", "three {code} {link}"]
    random.seed(42)
    first = select_message(templates, "X", "Y")
    random.seed(42)
    second = select_message(templates, "X", "Y")
    assert first == second


# ---------------------------------------------------------------------------
# extract_post_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://www.reddit.com/r/Visible/comments/1ta46qu/biweekly_megathread_for_referral_codes_please/",
            "1ta46qu",
        ),
        (
            "https://www.reddit.com/r/Visible/comments/1ta46qu/",
            "1ta46qu",
        ),
        (
            "https://www.reddit.com/r/Visible/comments/abc123/some_post_slug",
            "abc123",
        ),
    ],
)
def test_extract_post_id(url, expected):
    assert extract_post_id(url) == expected
