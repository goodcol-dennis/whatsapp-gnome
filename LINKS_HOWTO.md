# Opening External Links in the Default Browser

## Problem
Links clicked in WhatsApp Web (e.g. shared URLs, `target="_blank"` links) do nothing — they don't open in the system browser. This is because WebKitGTK handles them internally and the current `decide-policy` handler only catches `NAVIGATION_ACTION`, not new window requests.

## Root cause
WhatsApp Web opens links with `target="_blank"` or `window.open()`. WebKitGTK handles these via:
1. The `create` signal (fired for `window.open()` / `target="_blank"`)
2. The `decide-policy` signal with `NEW_WINDOW_ACTION` type

The current code only handles `NAVIGATION_ACTION` in `decide-policy`, so new-window links are silently dropped.

## Fix (see ../telegram/telegram.py for a working example)

### 1. Handle `NEW_WINDOW_ACTION` in `_on_decide_policy`:
```python
def _on_decide_policy(self, _webview, decision, decision_type):
    """Open non-WhatsApp links in the system browser."""
    if decision_type in (
        WebKit.PolicyDecisionType.NAVIGATION_ACTION,
        WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
    ):
        nav = decision.get_navigation_action()
        req = nav.get_request()
        uri = req.get_uri()
        # New window actions always open externally
        if decision_type == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION and uri:
            decision.ignore()
            Gio.AppInfo.launch_default_for_uri(uri, None)
            return True
        # Navigation away from WhatsApp opens externally
        if uri and not uri.startswith(WHATSAPP_URL) and not uri.startswith("https://web.whatsapp.com"):
            decision.ignore()
            Gio.AppInfo.launch_default_for_uri(uri, None)
            return True
    return False
```

### 2. Add a `create` signal handler (catches `window.open()` before policy decision):
```python
# In __init__, after connecting decide-policy:
self.webview.connect("create", self._on_create_new_window)
```

```python
@staticmethod
def _on_create_new_window(webview, nav_action):
    """Intercept new window requests (target=_blank) and open in browser."""
    req = nav_action.get_request()
    uri = req.get_uri() if req else None
    if uri:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    return None
```

### Key details
- `return None` from the `create` handler tells WebKitGTK not to create a new webview
- Both handlers are needed: `create` fires first for `window.open()`, `decide-policy` fires for regular link navigations
- The `decision.ignore()` call prevents WebKitGTK from navigating internally
