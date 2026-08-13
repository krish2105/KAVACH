# Commanding KAVACH from your iPhone

Phase 7. Two Apple Shortcuts talking to the local API over Tailscale. No Xcode,
no Apple Developer account, nothing to re-sign every seven days.

This is the **opposite direction** from the iPhone control already in
`hands/allowlist.json`. That is KAVACH driving your phone. This is your phone
driving KAVACH.

---

## What you need first

1. **Tailscale on both devices** — see the Tailscale section of
   [`SETUP.md`](../SETUP.md). Account creation and login are yours to do.
2. **KAVACH running** on the Mac: `uv run python -m kavach.voice`
3. **Serve the API to your tailnet** (once — `--bg` survives reboots):

   ```bash
   tailscale serve --bg 8770
   ```

4. **Your two values.** Everything below needs them:

   ```bash
   cd brain && grep '^KAVACH_API_TOKEN=' .env    # the token
   tailscale status --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
   ```

   Your base URL is `https://<that-dns-name>` — call it **`$KAVACH`** below.

Confirm the whole path works before touching your phone:

```bash
curl -s -H "Authorization: Bearer $KAVACH_API_TOKEN" https://$KAVACH/status
```

If that returns JSON, the phone will work. If it doesn't, the phone won't —
fix it here, where the errors are readable.

---

## Shortcut 1 — "Ask KAVACH"

The one you'll actually use. Say *"Hey Siri, Ask KAVACH"*, speak, hear the answer.

| # | Action | Settings |
|---|---|---|
| 1 | **Dictate Text** | stop listening: *After Pause* |
| 2 | **Get Contents of URL** | URL `https://$KAVACH/command` |
| | | Method **POST** |
| | | Headers: `Authorization` → `Bearer <your token>` |
| | | Request Body **JSON**, one field: `text` (Text) = *Dictated Text* |
| 3 | **Get Dictionary Value** | Get `reply` from *Contents of URL* |
| 4 | **Speak Text** | *Dictionary Value* |

Rename it exactly **Ask KAVACH** — that name is the Siri phrase.

**Proof it should work**, run on the Mac first:

```bash
curl -s -X POST https://$KAVACH/command -H "Authorization: Bearer $KAVACH_API_TOKEN" -H 'Content-Type: application/json' -d '{"text":"what time is it"}'
```

---

## Shortcut 2 — "KAVACH Control"

Status, approvals and the stop button behind one menu.

Start with **Choose from Menu** with three items: `Pending`, `Status`, `Stop`.

### Menu → Status

| # | Action | Settings |
|---|---|---|
| 1 | **Get Contents of URL** | `https://$KAVACH/status`, Method **GET**, same `Authorization` header |
| 2 | **Show Result** | *Contents of URL* |

### Menu → Pending

This is the screen the Phase 6 confirmation flow was built for.

| # | Action | Settings |
|---|---|---|
| 1 | **Get Contents of URL** | `https://$KAVACH/pending`, **GET**, `Authorization` header |
| 2 | **Get Dictionary Value** | `pending` from *Contents of URL* |
| 3 | **Get Item from List** | *First Item* |
| 4 | **If** | *Item from List* **has any value** |
| 5 |  ↳ **Get Dictionary Value** | `prompt` from *Item from List* |
| 6 |  ↳ **Choose from Menu** | Prompt: *Dictionary Value* — items `Approve`, `Deny` |
| 7 |  ↳ *Approve:* **Get Dictionary Value** | `id` from *Item from List* |
| 8 |  ↳ *Approve:* **Get Contents of URL** | `https://$KAVACH/confirm`, **POST**, header, JSON body: `id` (Text) = *Dictionary Value*, `approved` (Boolean) = **true** |
| 9 |  ↳ *Deny:* same as 7–8 but `approved` = **false** |
| 10 | **Otherwise** → **Show Alert** | "Nothing waiting." |

Two things to know before you rely on this:

* **Confirmations expire after 120 seconds.** Approving late is refused, not
  delayed — a stale yes must not authorise anything.
* **Nothing notifies you.** Without push (paid Apple membership) the phone
  cannot be woken when KAVACH needs an answer. This works for *"I sent a
  destructive command from my phone, now I approve it"* and not for *"KAVACH
  needs me and I'm in another room."*

### Menu → Stop

| # | Action | Settings |
|---|---|---|
| 1 | **Show Alert** | "Stop KAVACH? It will not restart itself." — leave *Show Cancel Button* **on** |
| 2 | **Get Contents of URL** | `https://$KAVACH/kill`, **POST**, header, JSON body: `reason` (Text) = `stopped from iPhone` |
| 3 | **Show Result** | *Contents of URL* |

**It only goes one way.** You can stop KAVACH from anywhere; nothing re-arms it
remotely, by this or any other route. Re-arming is a deliberate act at the Mac.
That is the §7 latch: an ambiguous state stays stopped, and a request from a
device that isn't in the room is exactly that.

Pressing Stop twice is safe — a second kill confirms rather than errors.

---

## Adding Siri, the Action Button, or Back Tap

In the Shortcuts editor, tap ⓘ:

* **Siri** — the shortcut's name *is* the phrase. "Ask KAVACH" works as-is.
* **Action Button** — Settings → Action Button → Shortcut → *Ask KAVACH*
* **Back Tap** — Settings → Accessibility → Touch → Back Tap → *KAVACH Control*
* **Lock Screen / Home Screen widget** — long-press → add a Shortcuts widget

---

## Security, stated plainly

* The bearer token is stored **in plaintext inside the shortcut**. Anyone with
  your unlocked phone can run these — including Stop.
* Tailscale Serve exposes the API to **your tailnet only**, never the public
  internet. The Mac's own binding stays `127.0.0.1`; Serve proxies to it. That
  is why `kavach-doctor` still reports "not listening on the network" — verify
  it does, and treat a change there as a real problem.
* Every request from the phone lands in the action log with a timestamp:

  ```bash
  curl -s -H "Authorization: Bearer $KAVACH_API_TOKEN" "https://$KAVACH/log?limit=20"
  ```

* If you ever think the token has leaked: delete the `KAVACH_API_TOKEN` line
  from `brain/.env` and restart. A new one is generated on the next run, and
  every existing shortcut stops working until you paste the new value in.

---

## When something doesn't work

| Symptom | Cause |
|---|---|
| `401` | Token wrong, or the header isn't `Bearer <token>` |
| `409` on a command | Kill switch is latched — re-arm at the Mac |
| `404` on confirm | The confirmation expired (120 s) or was already answered |
| Shortcut hangs | Tailscale not connected on one end — check both |
| Works on Mac, not phone | `tailscale serve status` on the Mac; is 8770 listed? |

`uv run kavach-doctor` checks the Mac side of all of this in one place.
