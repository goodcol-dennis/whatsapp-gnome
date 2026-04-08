#!/usr/bin/env python3
"""WhatsApp Web — native GTK4/WebKitGTK wrapper for GNOME."""

import base64
import os
import re
import sys
from urllib.parse import urlparse, unquote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, Adw, WebKit, GLib, Gio


APP_ID = "com.local.WhatsApp"
DATA_DIR = GLib.get_user_data_dir() + "/whatsapp-web"
WHATSAPP_URL = "https://web.whatsapp.com"
WHATSAPP_DOMAINS = (".whatsapp.com", ".whatsapp.net")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)

# Ensure Notification.permission persists as "granted" across sessions.
# WebKitGTK grants the permission request but doesn't persist the JS-side state.
NOTIFICATION_FIX_JS = """
(function() {
    if (window.Notification) {
        Object.defineProperty(Notification, 'permission', {
            get: function() { return 'granted'; },
            configurable: true
        });
        var origRequestPerm = Notification.requestPermission;
        Notification.requestPermission = function(cb) {
            if (cb) cb('granted');
            return Promise.resolve('granted');
        };
    }
})();
"""

# JavaScript injected to bridge GTK clipboard images into web paste events.
# ClipboardEvent.clipboardData is read-only in WebKit, so we simulate a file
# drop instead — WhatsApp Web handles drop events the same as paste.
PASTE_BRIDGE_JS = """
(function() {
    let _waitingForNative = false;

    document.addEventListener('paste', function(e) {
        if (e.clipboardData && e.clipboardData.files.length > 0) return;
        if (_waitingForNative) return;

        const text = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
        const looksLikePath = /^(file:\\/\\/\\/|\\/).*\\.(png|jpe?g|gif|webp|bmp)$/im.test(text.trim());

        _waitingForNative = true;
        if (looksLikePath) e.preventDefault();
        window.webkit.messageHandlers.clipboardBridge.postMessage('paste');
    }, true);

    window._injectClipboardImage = function(b64, mime) {
        _waitingForNative = false;
        mime = mime || 'image/png';
        const ext = mime.split('/')[1] || 'png';
        const byteStr = atob(b64);
        const arr = new Uint8Array(byteStr.length);
        for (let i = 0; i < byteStr.length; i++) arr[i] = byteStr.charCodeAt(i);
        const file = new File([arr], 'clipboard.' + ext, {type: mime});

        // Approach: hijack any <input type="file"> change to deliver our file,
        // then programmatically open WhatsApp's attach-image flow.
        // But first, try the direct input.files approach on existing inputs.

        // Find all file inputs WhatsApp has in the DOM
        const inputs = document.querySelectorAll('input[type="file"]');
        if (inputs.length > 0) {
            // Use the last file input (WhatsApp re-uses them)
            const input = inputs[inputs.length - 1];
            const dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
            input.dispatchEvent(new Event('change', {bubbles: true}));
            return;
        }

        // Fallback: watch for a file input to appear, then fill it
        const observer = new MutationObserver(function(mutations) {
            const inp = document.querySelector('input[type="file"]');
            if (inp) {
                observer.disconnect();
                const dt = new DataTransfer();
                dt.items.add(file);
                inp.files = dt.files;
                inp.dispatchEvent(new Event('change', {bubbles: true}));
            }
        });
        observer.observe(document.body, {childList: true, subtree: true});

        // Click the attach button to trigger file input creation
        const attachBtn = document.querySelector('[data-tab="10"]')     // attach button
                       || document.querySelector('[title="Attach"]')
                       || document.querySelector('span[data-icon="plus"]')?.closest('button')
                       || document.querySelector('span[data-icon="attach-menu-plus"]')?.closest('div[role="button"]');
        if (attachBtn) {
            attachBtn.click();
            // Then click the photo/video option
            setTimeout(function() {
                const photoBtn = document.querySelector('span[data-icon="attach-image"]')?.closest('button')
                              || document.querySelector('span[data-icon="attach-image"]')?.closest('div[role="button"]');
                if (photoBtn) photoBtn.click();
            }, 300);
        }

        // Clean up observer after 5 seconds
        setTimeout(function() { observer.disconnect(); }, 5000);
    };

    window._injectClipboardImageFailed = function() {
        _waitingForNative = false;
    };
})();
"""


class WhatsAppWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(
            title="WhatsApp",
            default_width=1100,
            default_height=750,
            **kwargs,
        )

        # -- WebKit setup --
        network_session = WebKit.NetworkSession.new(
            data_directory=DATA_DIR,
            cache_directory=DATA_DIR + "/cache",
        )

        # Persistent cookie jar so the QR login survives restarts
        cookie_manager = network_session.get_cookie_manager()
        cookie_manager.set_persistent_storage(
            DATA_DIR + "/cookies.sqlite",
            WebKit.CookiePersistentStorage.SQLITE,
        )

        policies = WebKit.WebsitePolicies(
            autoplay=WebKit.AutoplayPolicy.ALLOW,
        )
        self.webview = WebKit.WebView(
            network_session=network_session,
            website_policies=policies,
        )

        settings = self.webview.get_settings()
        settings.set_user_agent(USER_AGENT)
        settings.set_enable_media_stream(True)
        settings.set_enable_mediasource(True)
        settings.set_enable_webaudio(True)
        settings.set_enable_encrypted_media(True)
        settings.set_javascript_can_access_clipboard(True)
        settings.set_enable_developer_extras(False)
        settings.set_enable_smooth_scrolling(True)

        # User scripts
        content_manager = self.webview.get_user_content_manager()
        content_manager.add_script(WebKit.UserScript(
            NOTIFICATION_FIX_JS,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None, None,
        ))
        content_manager.add_script(WebKit.UserScript(
            PASTE_BRIDGE_JS,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None, None,
        ))
        content_manager.register_script_message_handler("clipboardBridge")
        content_manager.connect(
            "script-message-received::clipboardBridge",
            self._on_paste_requested,
        )

        # Watch title changes for unread badge
        self.webview.connect("notify::title", self._on_title_changed)
        # Allow notification permission requests
        self.webview.connect("permission-request", self._on_permission_request)
        # Forward web notifications to GNOME
        self.webview.connect("show-notification", self._on_show_notification)
        # Open external links in default browser
        self.webview.connect("decide-policy", self._on_decide_policy)

        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        self.webview.load_uri(WHATSAPP_URL)

        # -- Layout: proper GNOME/libadwaita structure --
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="WhatsApp", subtitle=""))

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.webview)

        self.set_content(toolbar_view)

    # -- Clipboard image bridge --

    def _on_paste_requested(self, _content_manager, message):
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        mime_types = formats.get_mime_types() or []

        if any(m.startswith("image/") for m in mime_types):
            clipboard.read_texture_async(None, self._on_clipboard_texture, None)
        elif "text/plain" in mime_types or "text/uri-list" in mime_types:
            clipboard.read_text_async(None, self._on_clipboard_text, None)
        else:
            self._inject_failed()

    def _on_clipboard_texture(self, clipboard, result, _user_data):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            clipboard.read_text_async(None, self._on_clipboard_text, None)
            return
        if texture is None:
            self._inject_failed()
            return
        self._send_texture(texture)

    def _on_clipboard_text(self, clipboard, result, _user_data):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            self._inject_failed()
            return
        if not text:
            self._inject_failed()
            return

        path = text.strip()
        if path.startswith("file://"):
            path = unquote(urlparse(path).path)
        # Also handle multiple URIs (take first image)
        for line in path.splitlines():
            line = line.strip()
            if line.startswith("file://"):
                line = unquote(urlparse(line).path)
            if os.path.isfile(line) and line.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            ):
                path = line
                break
        else:
            if not (os.path.isfile(path) and path.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )):
                self._inject_failed()
                return

        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._inject_failed()
            return

        b64 = base64.b64encode(data).decode("ascii")
        # Detect mime type from extension
        ext = os.path.splitext(path)[1].lower()
        mime = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".webp": "image/webp", ".bmp": "image/bmp",
        }.get(ext, "image/png")
        js = f"window._injectClipboardImage('{b64}', '{mime}');"
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _send_texture(self, texture):
        png_bytes = texture.save_to_png_bytes()
        b64 = base64.b64encode(png_bytes.get_data()).decode("ascii")
        js = f"window._injectClipboardImage('{b64}', 'image/png');"
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _inject_failed(self):
        self.webview.evaluate_javascript(
            "window._injectClipboardImageFailed();",
            -1, None, None, None, None, None,
        )

    # -- Title / badge --

    def _on_title_changed(self, webview, _pspec):
        title = webview.get_title() or ""
        match = re.search(r"\((\d+)\)", title)
        count = int(match.group(1)) if match else 0
        app = self.get_application()
        if app:
            app.update_badge(count)

    # -- Permissions --

    @staticmethod
    def _on_permission_request(_webview, request):
        """Auto-grant notification & media permissions."""
        if isinstance(
            request,
            (WebKit.NotificationPermissionRequest, WebKit.MediaKeySystemPermissionRequest),
        ):
            request.allow()
            return True
        if isinstance(request, WebKit.UserMediaPermissionRequest):
            request.allow()
            return True
        return False

    # -- Notifications --

    def _on_show_notification(self, _webview, notification):
        """Forward web notifications to GNOME desktop notifications."""
        app = self.get_application()
        if not app:
            return False
        gnome_notif = Gio.Notification.new(notification.get_title() or "WhatsApp")
        body = notification.get_body()
        if body:
            gnome_notif.set_body(body)
        gnome_notif.set_default_action("app.activate")
        app.send_notification(None, gnome_notif)
        return True

    # -- Navigation policy --

    def _on_decide_policy(self, _webview, decision, decision_type):
        """Allow WhatsApp domains, open everything else in the system browser."""
        if decision_type != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            # Allow all resource/response loads (media, XHR, etc.)
            decision.use()
            return True

        nav = decision.get_navigation_action()
        req = nav.get_request()
        uri = req.get_uri() or ""
        host = urlparse(uri).hostname or ""

        # Allow any whatsapp.com / whatsapp.net subdomain
        if any(host.endswith(d) for d in WHATSAPP_DOMAINS):
            decision.use()
            return True
        # Allow blob: and data: URIs (used for media playback)
        if uri.startswith("blob:") or uri.startswith("data:"):
            decision.use()
            return True
        # External link → system browser
        if uri.startswith("https://") or uri.startswith("http://"):
            decision.ignore()
            Gio.AppInfo.launch_default_for_uri(uri, None)
            return True
        return False


class WhatsAppApp(Adw.Application):
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

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = WhatsAppWindow(application=self)
        win.present()


def main():
    app = WhatsAppApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
