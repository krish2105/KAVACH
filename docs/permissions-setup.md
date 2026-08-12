# macOS permissions setup

Written from an actual Phase 0 run on **macOS 26.6.1 (Tahoe), M4 Pro**, 2026-08-13.
Every command below was executed; the outputs are real.

---

## The thing that trips everyone up

**macOS grants these permissions to the process that *launches* the MCP server,
not to the MCP server itself.**

`npx` and `uvx` are not what gets asked about — the terminal, IDE, or daemon
that spawned them is. Consequences worth internalising before you debug for an
hour:

- Grant it to Terminal, then run KAVACH from iTerm, and **nothing works** — you
  granted a different app.
- The launchd daemon (Phase 5) is its own "app" as far as TCC is concerned and
  needs its **own** grants, separate from your terminal's.
- Permission changes **only take effect on process restart**. A running process
  keeps its old answer.

---

## What each layer needs

| MCP server | Permission | Why |
|---|---|---|
| `macos-automator` | **Automation** (per target app) | AppleScript/JXA driving Finder, Safari, Notes, Calendar. macOS prompts once per controlled app. |
| `macos-automator` | **Accessibility** | Only for System Events — synthetic clicks, keystrokes, menu walking. |
| `macos-accessibility` (`macos-mcp`) | **Accessibility** | Reading and driving the UI element tree. |
| `macos-accessibility` | **Screen Recording** | Its `Snapshot` tool. Note the spec attributes Screen Recording to Peekaboo alone — that is wrong, this server needs it too. |
| `peekaboo` | **Screen Recording** | Screen capture, the whole point of the fallback layer. |
| `peekaboo` | **Accessibility** | UI inspection and clicking. |
| kill-switch hotkey | **Input Monitoring** | Global key monitoring (`kTCCServiceListenEvent`). Distinct from Accessibility. |

---

## Granting them

**System Settings → Privacy & Security →** then each of:

- **Accessibility**
- **Screen Recording**
- **Input Monitoring**
- **Automation** (populates itself as apps request; you approve per pair)

Add the app that will launch KAVACH. Restart it afterwards.

---

## Verifying — do not trust the checkboxes

Checkboxes lie (stale TCC entries after an app update are common). Make a real
call instead.

### All three servers speak MCP

```bash
nvm use && python3 hands/probe_mcp.py
```

Actual Phase 0 output:

```
✓ macos-automator: macos_automator v0.4.6 (protocol 2025-06-18)
      2 tools: execute_script, get_scripting_tips
✓ macos-accessibility: macos-mcp v3.4.7 (protocol 2025-06-18)
      12 tools: App, Shell, Snapshot, Click, Type, Scroll, Move, Shortcut, Wait, Scrape, Desktop, Notification
✓ peekaboo: peekaboo-mcp v4.0.0 (protocol 2025-06-18)
      26 tools: menu, app, agent, permissions, image, action, dialog, sleep, …
3/3 servers responded to MCP.
```

### Read the live grant state

```bash
python3 hands/call_mcp.py peekaboo permissions '{}'
```

```
macOS Permissions Status:
Screen Recording: [ok] Granted
Accessibility: [ok] Granted
```

### Prove each layer can actually act

```bash
# Automation → Finder (read-only)
python3 hands/call_mcp.py macos-automator execute_script \
  '{"script_content":"tell application \"Finder\" to return name of home as string"}'

# Accessibility → full UI element tree
python3 hands/call_mcp.py macos-accessibility Snapshot '{"use_vision":false}'

# Screen Recording → a real screenshot
python3 hands/call_mcp.py peekaboo image \
  '{"app_target":"screen:0","path":"/tmp/kavach-probe.png","format":"png"}'
```

All three returned real data in Phase 0 — the home folder name, a full
element tree including Dock and menu bar, and a 2.7 MB PNG.

### Input Monitoring (kill-switch hotkey)

```bash
cd brain && uv run python -c \
  "from kavach.killswitch.hotkey import self_test; print(self_test()[1])"
```

If it is not granted:

```bash
cd brain && uv run python -m kavach.killswitch.daemon --request-permission
```

Approve the dialog, then **restart the daemon** — macOS applies the grant only
on restart. Until then the daemon says `hotkey ⌃⌥⌘K [NOT WORKING]` rather than
pretending, and `kavach kill` over the socket still works.

---

## Gotchas found the hard way

**Parameter names are not what you would guess.** `macos-automator` takes
`script_content` (snake_case), not `scriptContent`. `macos-mcp`'s `App` tool
has no `list` mode — modes are `launch`/`resize`/`move`/`switch`; use
`Snapshot` for read-only inspection.

**`uvx macos-mcp` alone no longer starts a server.** As of 0.3.17 it needs the
`serve` subcommand — `uvx macos-mcp@0.3.17 serve --transport stdio`. The
project's own README still shows the older bare form.

**Calendar's bundle id is `com.apple.iCal`,** not `com.apple.Calendar`.

**Node ≥24 is mandatory,** not a nicety: `@steipete/macos-automator-mcp`
declares `engines: node >=24`, Peekaboo `>=22`. Run `nvm use` (reads `.nvmrc`)
before anything. Under Node 20 the automator server refuses to start.

---

## Revoking

System Settings → Privacy & Security → each list → toggle off or `−`.
`tccutil reset Accessibility` and `tccutil reset ScreenCapture` clear grants
machine-wide, which is the blunt instrument if TCC state gets confused.
