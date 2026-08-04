# WhatsApp Web — Native GTK4/WebKitGTK GNOME Wrapper

> Guardrails: [umami.md](https://github.com/goodcol-dennis/umami/blob/main/umami.md) — Tier 1 (Foundation)
>
> Shared techniques live in the [WebKitGTK SPA Wrapper Playbook](../webkit-wrapper-playbook.md)
> — consult it before changing navigation policy, notifications, downloads, or
> the paste bridge. slack.py is the family reference implementation.

## Versions & Environment

| Component | Version |
|-----------|---------|
| OS | Ubuntu 26.04 |
| Desktop | GNOME (Wayland) |
| Python | 3 (system) |
| GTK | 4.0 (`gi.require_version("Gtk", "4.0")`) |
| libadwaita | 1 (`gi.require_version("Adw", "1")`) |
| WebKitGTK | 6.0 / 2.52.3 (`gi.require_version("WebKit", "6.0")`) |

System packages required: `gir1.2-webkit-6.0`, GTK4, libadwaita, Python 3.

## Common Commands

```bash
./whatsapp.py          # Run the app
./whatsapp.py --dev    # Run with WebKit inspector + [paste] logging to stdout
./whatsapp.py --test   # Test mode: F11/F12 hooks, separate profile + app id
./install.sh           # Kill running instance, install icon + desktop entry, relaunch
cd tests/narkina-e2e && cargo test   # Headless E2E through narkina
```

**Agents:** neither `./whatsapp.py` nor `./install.sh` may be run on the user's
live session — see Critical Rule 8. Test through the e2e crate above, or drive
[narkina](../narkina/) directly: `Session::builder(".../whatsapp.py").arg("--test")`,
`.env("WHATSAPP_TEST_DIR", ...)`, `.stderr_to_file()`; then F11 executes a
command file, F12 dumps `key=value` state. `cage`, `wtype`, `wlrctl` are
installed.

## Project Structure

```
whatsapp/
├── CLAUDE.md                  # This file — project instructions
├── whatsapp.py                # Single-file app (all logic here)
├── whatsapp.svg               # App icon (green rounded square, white chat bubble)
├── com.local.WhatsApp.desktop # GNOME desktop entry (name MUST match app id)
├── install.sh                 # Installs icon + .desktop, restarts the app
├── tests/narkina-e2e/         # Headless E2E (Rust, drives the app via narkina)
├── BADGE_HOWTO.md             # Historical design note (badge is implemented)
├── LINKS_HOWTO.md             # Historical design note (links are implemented)
└── .gitignore
```

Untracked and intentionally ignored: `.claude/`, `.vscode/`, `.mcp.json`,
`SWEEP_NOTES.md` (local working notes), `tests/narkina-e2e/target/`.

## Architecture

- Python 3 + GTK4 + libadwaita + WebKitGTK 6.0 (no Electron)
- Single-file app: `whatsapp.py` (family convention — slack.py is ~1000 lines
  single-file too; rule 2's 400-line clause permits splitting but parity wins)
- `Adw.ApplicationWindow` with `Adw.ToolbarView` + `Adw.HeaderBar`
- WebKitGTK `NetworkSession.new(data_directory=..., cache_directory=...)`,
  SQLite cookie jar, cookie policy `ALWAYS`, ITP disabled
- Target URL: `https://web.whatsapp.com`
- App ID: `com.local.WhatsApp` (`com.local.WhatsApp.Test` in `--test` mode)
- Data directory: `~/.local/share/whatsapp-web/` (`whatsapp-web-test/` in test mode)
- Config: `~/.local/share/whatsapp-web/config.json` — `zoom` (persisted by the
  zoom shortcuts), `user_agent` (manual override for a stale UA), and
  `enable_service_workers` (re-enable SW to re-test WebKit bug 239925)
- User agent: Chrome 151 shape (`USER_AGENT_DEFAULT`) — **required**; WhatsApp
  serves an "update your browser" wall to UAs it considers stale. Bump every
  few months or override via config.json.

## Critical Rules

1. **No Electron** — GTK4/WebKitGTK only. No extra menus or chrome beyond the GNOME header bar.
2. **Single-file app** — All logic lives in `whatsapp.py`. Splitting is permitted past 400 lines but the family convention is single-file; keep parity with slack.py.
3. **Persistent login** — Cookies + IndexedDB in `~/.local/share/whatsapp-web/`. QR scan must survive restarts. Cookie policy `ALWAYS`, ITP off.
4. **External links in browser** — but only deliberate user clicks leave the app (gesture-gated policy, playbook §1). Redirects/form-submissions/JS navigations stay in-app. Dot-boundary domain matching only.
5. **No unconditional debug logging** — `console.log`, `print()`, `set_enable_developer_extras()` and console-to-stdout are permitted only behind `DEV_LOGGING` (`--dev`/`--test`). Nothing may log on a default launch.
6. **Never monkey-patch browser APIs** — Overriding `Notification`, `AudioContext`, `Audio`, `URL.createObjectURL`, or similar in user scripts breaks WhatsApp's functionality. Native WebKit APIs exist for every case (e.g. `initialize_notification_permissions`). The paste bridge's *transient* input-click interception (restored on every exit path) is the one sanctioned exception.
7. **Scope discipline** — Only modify files inside this project directory. Never modify files in sibling/adjacent projects without explicit user approval.
8. **Headless testing only — never launch on the live session** — Any run of the app for testing goes through [narkina](../narkina/) (the `tests/narkina-e2e` crate is the ready-made path). Do **not** run `./whatsapp.py` or `./install.sh` on the user's real compositor — `install.sh` relaunches the app by design. This applies to subagents too: state the constraint explicitly in their prompts.
9. **App id ↔ desktop filename coupling** — The desktop entry must be named `com.local.WhatsApp.desktop` or GNOME Shell rejects every `Gio.Notification` (verified hard failure, playbook §2). The badge's `application://com.local.WhatsApp.desktop` string and install.sh must change together with it.

## Change Propagation Map

| Change type | Files touched (in order) |
|-------------|--------------------------|
| App behavior / features | `whatsapp.py` → `tests/narkina-e2e` (extend if testable) → `CLAUDE.md` Implementation Notes (always, if the mechanism changed) → playbook (if family-wide) |
| Icon change | `whatsapp.svg` → `./install.sh` (re-install) |
| Desktop entry metadata | `com.local.WhatsApp.desktop` → `./install.sh` (re-install) — never rename without Rule 9 |
| New system dependency | Verify installed → `CLAUDE.md` versions table |

## Implementation Notes

### Clipboard File Paste (implemented)
WebKitGTK does NOT expose clipboard images to the web Clipboard API. The bridge:
1. JS intercepts `paste` events (capture phase); intercepts only when every
   non-empty line of the clipboard text looks like a file path (no extension
   whitelist — Python validates with `isfile()`). Stashes the text so a failed
   round-trip can restore it via `execCommand('insertText')`.
2. Python reads the GTK clipboard — `image/*` textures first; `text/uri-list`
   via `read_async` (the GNOME portal **silently blocks** `read_text_async`
   for file clipboards); `text/plain` last.
3. Python base64-encodes (50 MB cap → notification), maps extension →
   MIME (`MIME_TYPES`), calls `window._injectClipboardFile(b64, mime, name)` —
   mime/filename pass through `json.dumps` (script-injection guard).
4. JS builds a `File` and injects, in order (each gated on the page consuming
   the event): **synthetic paste** with constructor-init clipboardData
   (`new ClipboardEvent('paste', {clipboardData: dt})` — the Karere recipe,
   verified on this WebKitGTK by telegram.py; re-entry into our own capture
   listener is safe via the `files.length > 0` early-return) → **synthetic
   drop** on `#main`, only when a chat is open (a bare document-level
   `preventDefault` would false-positive) → file-input fill.
5. Input fallback: the photo/video `<input type="file">` (`accept` contains
   `video`; the sticker input is image-only and must not be targeted) via
   `DataTransfer` + synthetic `change`. When WhatsApp hasn't created the input
   yet, `primeAndInject()` clicks through the attach menu invisibly (observer
   hides menu nodes, then restores their previous inline styles) and undoes
   WhatsApp's navigation with `history.back()`.
6. *Assigning* `event.clipboardData` / `dataTransfer.files` post-construction
   is read-only in WebKit — only the constructor-init dict works.
7. A 10 s JS watchdog resets the `_waitingForNative` latch if the native side
   never answers, so one lost round-trip can't wedge every future paste.

### Sticker Input (future feature)
WhatsApp has a dedicated sticker file input accepting only `image/*` without
`video/*`. `findFileInput()` in the bridge deliberately skips it — a sticker
feature would need its own selector for the image-only input.

### Notifications (implemented)
1. Native permission: `initialize-notification-permissions` signal →
   `initialize_notification_permissions([web.whatsapp.com origin], [])`. No JS
   shim (rule 6). `permission-request` auto-grant kept as belt-and-braces.
2. `query-permission-state` → GRANTED for `"notifications"` so
   `navigator.permissions.query` agrees with `Notification.permission`.
3. `show-notification` → `Gio.Notification` with
   `set_default_action_and_target("app.notification-clicked", id)`; the action
   calls the stored `WebKitNotification.clicked()` (WhatsApp's own onclick
   jumps to the chat) and presents the window. `closed` signal → withdraw.
   Stable per-chat tags make updates replace instead of stack; map bounded at 100.
4. **Never** `set_default_action("app.activate")` — the action doesn't exist.

### Downloads (implemented)
1. `download-started` on **NetworkSession** (not WebContext).
2. RESPONSE policy: `decision.download()` when main-frame main-resource and
   (unsupported MIME or `Content-Disposition: attachment`) — cross-origin
   `<a download>` is ignored by WebCore so attachments arrive as navigations.
3. `decide-destination`: XDG download dir (**None fallback** → `~/Downloads`),
   `makedirs`, dedupe to `name (2).ext`; `set_destination` takes a plain path,
   not `file://`.
4. `finished` fires after `failed` too — the failed handler marks the download
   to prevent double notification.

### Audio / Calls (implemented)
1. `WebKit.WebsitePolicies(autoplay=ALLOW)` **passed to the WebView constructor**
2. `enable_webaudio`, `enable_encrypted_media`, `enable_mediasource`, `enable_media_stream`
3. `enable_webrtc` — RTCPeerConnection is off by default; calls need it
4. Non-navigation policy decisions → `decision.use()`; `blob:`/`data:` allowed

### Navigation Policy (implemented — playbook §1)
1. RESPONSE → download detection (above) else `use()`.
2. Other non-NAVIGATION_ACTION → `use()`.
3. Allowed domains (`whatsapp.com`, `whatsapp.net`, dot-boundary) → `use()`.
4. `blob:`/`data:`/`about:` → `use()`.
5. http(s) out of scope: only `LINK_CLICKED` + `is_user_gesture()` +
   `not is_redirect()` leaves for the system browser; everything else stays.
6. Non-web schemes (`mailto:`, `tel:`) → `ignore()` + `launch_default_for_uri`
   wrapped in try/except (no handler registered is not a crash).
7. `create` (target=_blank): out-of-scope → browser; in-scope → **related-view
   popup** sharing the web process/session (`window.opener` stays alive).

### Zoom (implemented)
Ctrl+=/− /0 and Ctrl+scroll. Persisted to config.json. Default 1.0, range 0.5–3.0.

### WebKit bug 239925 — observed here, fixed (service workers off)
WebKit bug 239925 (GTK): service-worker `FetchEvent.respondWith` delivery is
unreliable on this engine. Observed 2026-08-03 as (a) scattered blank sprites
in the emoji picker under *both* Chrome and Safari UAs — SW-served static
assets failing per-request — and (b) received videos that download but never
play, while just-sent copies (local `blob:` URLs) were fine. That asymmetry is
the diagnostic fingerprint; don't chase codecs or UA theories first (we did —
an AVIF/UA theory was falsified by the UA A/B). Fix (telegram's, ported):
disable the `ServiceWorkers` runtime feature via `_set_webkit_feature` at
startup; cost is only the offline boot shell. config.json
`{"enable_service_workers": true}` re-enables to re-test after engine
upgrades.

### Robustness (implemented)
- `WEBKIT_DMABUF_RENDERER_DISABLE_GBM=1` set before `import gi` (playbook §4
  #29 — Arrow Lake-P dmabuf shearing on i915; `WHATSAPP_FORCE_DMABUF=1`
  bypasses the guard to re-test the hardware).
- `set_enable_page_cache(False)` (playbook §4 #28 — bfcache parks documents
  with open IndexedDB connections and deadlocks the next page's `open()`;
  verified on Slack, preventative here).
- `web-process-terminated` → `reload()`, rate-limited to one per 10 s.
- `Gio.bus_get_sync` wrapped in try/except — headless sessions have no bus;
  badge degrades instead of crashing at startup.
- `hasattr(WebKit, "ClipboardPermissionRequest")` guard before isinstance.
- Spell checking on, languages from `GLib.get_language_names()` filtered
  (entries containing `.` and `C` match no hunspell dictionary).
- Context menu filtered to spelling entries (playbook #32): WebKit's menu is
  the only UI for applying a correction, so it shows when it carries spelling
  suggestions — stripped to just those — and is suppressed otherwise so
  WhatsApp's own right-click menus win. No browser chrome in the app.

### Dock Badge (implemented)
1. `notify::title` → **anchored** regex `^\((\d+)\)` on "(N) WhatsApp"
2. `com.canonical.Unity.LauncherEntry` `Update` signal on `/com/local/WhatsApp`
3. `application://com.local.WhatsApp.desktop`, `count` as int64 variant `"x"`
4. Guarded on count-changed; disabled when no session bus

### Test hooks (implemented — `--test` mode only)
- App id `com.local.WhatsApp.Test`, profile `whatsapp-web-test/` (concurrent
  WebKit sessions on one data dir corrupt cookies.sqlite/IndexedDB)
- F11 executes `$WHATSAPP_TEST_DIR/whatsapp-test-input.txt` (`navigate`,
  `navigate-js`, `js`, `paste`); F12 dumps state to `whatsapp-test-state.txt`
- `tests/narkina-e2e/` runs the full scenario headlessly (launch, UA, zoom,
  paste failure branch, popup) — `cargo test` in that dir

## Pre-Commit Checklist

- [ ] `python3 -m py_compile whatsapp.py` clean
- [ ] `cd tests/narkina-e2e && cargo test` passes (headless — safe anywhere)
- [ ] No unrelated files modified (scope discipline)
- [ ] Changes match what was requested — nothing more, nothing less
- [ ] No unconditional debug logging (everything behind `DEV_LOGGING`)
- [ ] CLAUDE.md Implementation Notes match the code if a mechanism changed
- [ ] Git status reviewed — no unintended files staged
