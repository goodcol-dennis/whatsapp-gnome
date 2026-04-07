# Adding a Dock Badge (Unread Count) to WhatsApp

## What this does
Shows an unread message count badge on the app icon in GNOME's dock, like Slack and other native apps do.

## How it works
1. WhatsApp Web updates the page title to `"WhatsApp (5)"` when there are unread messages.
2. Connect to the WebView's `notify::title` signal to watch for title changes.
3. Parse the count from the title with a regex like `r"\((\d+)\)"`.
4. Send the count to GNOME's dock via DBus using the `com.canonical.Unity.LauncherEntry` protocol (supported by Ubuntu Dock / Dash to Dock).

## Implementation (see ../telegram/telegram.py for a working example)

### 1. Add `import re` at the top

### 2. In the Window class, connect the title signal (after creating the webview):
```python
self.webview.connect("notify::title", self._on_title_changed)
```

### 3. Add the title-changed handler to the Window class:
```python
def _on_title_changed(self, webview, _pspec):
    title = webview.get_title() or ""
    match = re.search(r"\((\d+)\)", title)
    count = int(match.group(1)) if match else 0
    app = self.get_application()
    if app:
        app.update_badge(count)
```

### 4. Update the App class to manage the DBus connection and emit badge updates:
```python
def __init__(self):
    super().__init__(
        application_id=APP_ID,
        flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
    )
    self._dbus_connection = None
    self._badge_count = 0

def do_startup(self):
    Adw.Application.do_startup(self)
    self._dbus_connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def update_badge(self, count):
    if count == self._badge_count or not self._dbus_connection:
        return
    self._badge_count = count
    self._dbus_connection.emit_signal(
        None,
        "/com/local/WhatsApp",
        "com.canonical.Unity.LauncherEntry",
        "Update",
        GLib.Variant("(sa{sv})", (
            "application://whatsapp.desktop",
            {
                "count": GLib.Variant("x", count),
                "count-visible": GLib.Variant("b", count > 0),
            },
        )),
    )
```

### Key details
- The DBus object path should match the app ID: `/com/local/WhatsApp`
- The desktop file reference must match the installed `.desktop` filename: `application://whatsapp.desktop`
- The `"x"` variant type is a 64-bit int (required by the Unity Launcher API)
- No extra dependencies needed — just `Gio` and `GLib` which are already imported
