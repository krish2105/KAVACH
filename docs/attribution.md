# Attribution

## Presence layer — `apps/orb/`

KAVACH's orb UI is forked from **ULTRON Orb UI** by **Sagar Tamang**, used under
the MIT License.

| | |
|---|---|
| Upstream | https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds |
| Forked at commit | `a65306f5a9568655551ec27445f773f20273223a` |
| Commit date | 2026-07-15 |
| License | MIT — `Copyright (c) 2026 Sagar Tamang` |
| Vendored on | 2026-08-13 |

The upstream `LICENSE` file is retained verbatim at `apps/orb/LICENSE`, as the
MIT License requires. Do not delete it.

**What we took:** `lib/orbScene.ts` (the Three.js scene — layered wireframe
shells, spiral inner core, floating code sprites), `lib/handTracker.ts`
(MediaPipe HandLandmarker on the webcam feed), and `components/JarvisOrb.tsx`
(HUD and glue). Per spec §4 these are reused rather than rebuilt.

**Why the git history isn't preserved:** the clone's `.git` directory was
removed so `apps/orb` is a plain directory inside the KAVACH repo rather than a
nested repo or submodule. Provenance lives in this file instead. To diff against
upstream later:

```bash
git clone https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds.git /tmp/ultron-upstream
diff -ru /tmp/ultron-upstream apps/orb --exclude=.git --exclude=node_modules
```

---

## Other building blocks

Named here because spec §11 asks the portfolio write-up to credit the real
components rather than saying "AI assistant":

| Component | Project | License |
|---|---|---|
| AppleScript/JXA MCP | [steipete/macos-automator-mcp](https://github.com/steipete/macos-automator-mcp) | MIT |
| Accessibility API MCP | [CursorTouch/MacOS-MCP](https://github.com/CursorTouch/MacOS-MCP) | MIT |
| Screen-see-and-click MCP | [openclaw/Peekaboo](https://github.com/openclaw/Peekaboo) | see repo |
| Hand tracking | Google MediaPipe Tasks Vision | Apache-2.0 |
| Agent loop | Anthropic Claude Agent SDK | see package |
