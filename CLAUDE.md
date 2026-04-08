# WhatsApp Web — Native GTK4/WebKitGTK GNOME Wrapper

> Guardrails: [umami.md](https://github.com/goodcol-dennis/umami/blob/main/umami.md) — Tier 1 (Foundation)

## Versions & Environment

| Component | Version |
|-----------|---------|
| OS | Ubuntu 26.04 |
| Desktop | GNOME (Wayland) |
| Python | 3 (system) |
| GTK | 4.0 (`gi.require_version("Gtk", "4.0")`) |
| libadwaita | 1 (`gi.require_version("Adw", "1")`) |
| WebKitGTK | 6.0 / 2.52.0 (`gi.require_version("WebKit", "6.0")`) |

System packages required: `gir1.2-webkit-6.0`, GTK4, libadwaita, Python 3.

## Common Commands

```bash
./whatsapp.py          # Run the app
./install.sh           # Kill running instance, install icon + desktop entry, relaunch
```

## Project Structure

```
whatsapp/
├── CLAUDE.md           # This file — project instructions
├── whatsapp.py         # Single-file app (all logic here)
├── whatsapp.svg        # App icon (green rounded square, white chat bubble)
├── whatsapp.desktop    # GNOME desktop entry
├── install.sh          # Installs icon + .desktop, restarts the app
├── BADGE_HOWTO.md      # Implementation guide for dock badge feature
├── LINKS_HOWTO.md      # Implementation guide for link handling
└── .gitignore
```

## Architecture

- Python 3 + GTK4 + libadwaita + WebKitGTK 6.0 (no Electron)
- Single-file app: `whatsapp.py`
- `Adw.ApplicationWindow` with `Adw.ToolbarView` + `Adw.HeaderBar` for native GNOME window controls
- WebKitGTK `NetworkSession.new(data_directory=..., cache_directory=...)` for persistent session/cookies
- Target URL: `https://web.whatsapp.com`
- App ID: `com.local.WhatsApp`
- Data directory: `~/.local/share/whatsapp-web/`

## Critical Rules

1. **No Electron** — GTK4/WebKitGTK only. No extra menus or chrome beyond the GNOME header bar.
2. **Single-file app** — All logic lives in `whatsapp.py`. No splitting into modules unless it exceeds 400 lines.
3. **Persistent login** — Cookies stored in `~/.local/share/whatsapp-web/`. QR scan must survive restarts.
4. **External links in browser** — Navigation policy must allow all `*.whatsapp.com` and `*.whatsapp.net` subdomains. Everything else opens in the default browser via `Gio.AppInfo.launch_default_for_uri()`.
5. **No debug logging in production** — No `console.log`, `print()`, `set_enable_developer_extras(True)`, or `set_enable_write_console_messages_to_stdout(True)` in committed code.
6. **Never monkey-patch browser APIs** — Overriding `AudioContext`, `Audio`, `URL.createObjectURL`, or similar native constructors in user scripts breaks WhatsApp's audio/media playback. Debug hooks that wrap these APIs must be removed before committing.
7. **Scope discipline** — Only modify files inside this project directory. Never modify files in sibling/adjacent projects (e.g., `../telegram/`) without explicit user approval.

## Change Propagation Map

| Change type | Files touched (in order) |
|-------------|--------------------------|
| App behavior / features | `whatsapp.py` → test manually → `CLAUDE.md` (if new critical rule) |
| Icon change | `whatsapp.svg` → `./install.sh` (re-install) |
| Desktop entry metadata | `whatsapp.desktop` → `./install.sh` (re-install) |
| New system dependency | Verify installed → `CLAUDE.md` versions table |

## Implementation Notes

### Clipboard Image Paste (implemented)
WebKitGTK does NOT expose clipboard images to the web Clipboard API. The bridge works as:
1. JS intercepts `paste` events via user script, sends message to Python via `clipboardBridge` handler
2. Python reads GTK clipboard — checks for `image/*` textures first, falls back to `text/uri-list` / `text/plain` for file paths
3. On GNOME portal, clipboard contains `application/vnd.portal.files` + `text/uri-list` + `text/plain;charset=utf-8` — **not** `image/*`. Must parse file paths and read the actual file.
4. Python base64-encodes the image, calls JS `_injectClipboardImage()`
5. JS sets `.files` on an `<input type="file">` via `DataTransfer` and fires a `change` event. **Do NOT use** `ClipboardEvent` (clipboardData is read-only in WebKit) or `DragEvent` (dataTransfer.files is read-only from JS).

### Notifications (implemented)
1. Auto-grant `NotificationPermissionRequest` via `permission-request` signal
2. Handle `show-notification` signal on the WebView
3. Forward as `Gio.Notification` via `app.send_notification()`

### Dock Badge (implemented)
1. Watch `notify::title` signal — WhatsApp Web sets title to `"WhatsApp (N)"` for unread count
2. Parse count with regex, send via `com.canonical.Unity.LauncherEntry` DBus signal
3. DBus object path must match app ID: `/com/local/WhatsApp`
4. Desktop file reference: `application://whatsapp.desktop`

### Audio Playback (implemented)
WebKitGTK defaults break audio. Required settings:
1. `WebKit.WebsitePolicies(autoplay=WebKit.AutoplayPolicy.ALLOW)` passed to WebView constructor
2. `settings.set_enable_webaudio(True)` and `settings.set_enable_encrypted_media(True)`
3. Navigation policy must explicitly `decision.use()` for non-navigation policy types (resource/response loads)
4. Allow `blob:` and `data:` URIs in navigation policy

### Navigation Policy (implemented)
1. Non-navigation policy decisions (resource loads, responses) → `decision.use()` (allow)
2. WhatsApp domains (`*.whatsapp.com`, `*.whatsapp.net`) → `decision.use()` (allow)
3. `blob:` and `data:` URIs → `decision.use()` (allow)
4. All other http/https → `decision.ignore()` + open in system browser

### Permissions (implemented)
Auto-grant: `NotificationPermissionRequest`, `MediaKeySystemPermissionRequest`, `UserMediaPermissionRequest`.

## Pre-Commit Checklist

- [ ] App launches and loads WhatsApp Web without errors
- [ ] No unrelated files modified (scope discipline)
- [ ] Changes match what was requested — nothing more, nothing less
- [ ] No debug logging left in (`console.log`, `print()`, `set_enable_developer_extras(True)`)
- [ ] Git status reviewed — no unintended files staged
