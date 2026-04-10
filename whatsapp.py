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


DEV_LOGGING = "--dev" in sys.argv
if DEV_LOGGING:
    sys.argv.remove("--dev")
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

# JavaScript injected to bridge GTK clipboard into WhatsApp Web.
# Strategy: try drop event first (works for all file types), fall back to
# finding the file input element.
PASTE_BRIDGE_JS = """
(function() {
    let _waitingForNative = false;
    const _dev = %DEV%;
    function _log() { if (_dev) console.log.apply(console, ['[paste]'].concat(Array.from(arguments))); }

    // File extensions we can handle via clipboard bridge
    var FILE_EXT_RE = new RegExp('[.](png|jpe?g|gif|webp|bmp|mp4|mov|avi|mkv|3gp|pdf|doc|docx|xls|xlsx|ppt|pptx|txt|zip|rar|apk|ogg|mp3|wav|opus)$', 'i');
    var PATH_RE = new RegExp('^(file:///|/)');

    document.addEventListener('paste', function(e) {
        _log('paste event, files=' + (e.clipboardData ? e.clipboardData.files.length : 'N/A'));
        if (e.clipboardData && e.clipboardData.files.length > 0) return;
        if (_waitingForNative) { _log('already waiting'); return; }

        // Only intercept if the clipboard text looks like a file path.
        // Otherwise let the paste through normally (URLs, text, etc.)
        var text = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
        var looksLikePath = PATH_RE.test(text.trim()) && FILE_EXT_RE.test(text.trim());
        if (!looksLikePath && text) {
            _log('not a file path, letting paste through: ' + text.substring(0, 100));
            return;
        }

        _waitingForNative = true;
        if (looksLikePath) e.preventDefault();
        window.webkit.messageHandlers.clipboardBridge.postMessage('paste');
    }, true);

    // Find a suitable file input. The photo/video input (primed via attach menu)
    // has accept containing "video". If not found, fall back to any file input
    // except pure sticker inputs (accept="image/*" only).
    var _capturedInput = null;
    function findFileInput() {
        if (_capturedInput && document.contains(_capturedInput)) return _capturedInput;
        _capturedInput = null;
        var inputs = document.querySelectorAll('input[type="file"]');
        // Prefer input with "video" in accept (photo/video input)
        for (var i = 0; i < inputs.length; i++) {
            if ((inputs[i].accept || '').indexOf('video') !== -1) return inputs[i];
        }
        return null;
    }

    function fillInput(input, file) {
        _log('filling input, accept=' + input.accept);
        var dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        input.dispatchEvent(new Event('change', {bubbles: true}));
    }

    // Prime the photo input by clicking through the attach menu.
    // After capturing the input, use history.back() to undo WhatsApp's navigation.
    var _priming = false;
    function primeAndInject(file) {
        var existing = findFileInput();
        if (existing) {
            fillInput(existing, file);
            return;
        }
        if (_priming) return;
        _priming = true;
        _log('priming photo input...');

        var histLen = window.history.length;

        var origClick = HTMLInputElement.prototype.click;
        HTMLInputElement.prototype.click = function() {
            if (this.type === 'file' && (this.accept || '').indexOf('video') !== -1) {
                HTMLInputElement.prototype.click = origClick;
                _capturedInput = this;
                _priming = false;
                _log('primed, accept=' + this.accept);
                fillInput(this, file);
                // Undo WhatsApp's internal navigation
                setTimeout(function() {
                    if (window.history.length > histLen) {
                        _log('undoing navigation via history.back()');
                        window.history.back();
                    }
                }, 100);
                return;
            }
            return origClick.call(this);
        };

        var hidden = [];
        var obs = new MutationObserver(function(muts) {
            muts.forEach(function(m) {
                m.addedNodes.forEach(function(n) {
                    if (n.nodeType === 1 && n.style) {
                        n.style.cssText = 'position:fixed!important;left:-9999px!important;opacity:0!important;pointer-events:none!important;';
                        hidden.push(n);
                    }
                });
            });
        });
        obs.observe(document.body, {childList: true});

        var attachBtn = document.querySelector('span[data-icon="plus"]')
            || document.querySelector('span[data-icon="attach-menu-plus"]');
        if (attachBtn) attachBtn = attachBtn.closest('button') || attachBtn.closest('div[role="button"]');
        if (!attachBtn) attachBtn = document.querySelector('[data-tab="10"]');
        if (!attachBtn) attachBtn = document.querySelector('span[data-icon="clip"]');
        if (attachBtn && !attachBtn.click) attachBtn = attachBtn.closest('div[role="button"]');

        if (!attachBtn) {
            _log('no attach button found');
            HTMLInputElement.prototype.click = origClick;
            obs.disconnect();
            _priming = false;
            return;
        }

        _log('clicking attach button');
        attachBtn.click();

        var attempts = 0;
        var iv = setInterval(function() {
            var btn = null;
            document.querySelectorAll('div[role="button"], button, li').forEach(function(el) {
                var txt = el.textContent || '';
                if (!btn && /photo/i.test(txt) && !/sticker/i.test(txt)) btn = el;
            });
            if (btn) {
                clearInterval(iv);
                _log('clicking photo/video menu item');
                btn.click();
                setTimeout(function() {
                    obs.disconnect();
                    hidden.forEach(function(n) { n.style.cssText = ''; });
                    if (_priming) {
                        HTMLInputElement.prototype.click = origClick;
                        _priming = false;
                        _log('priming did not capture a file input');
                    }
                }, 300);
            } else if (++attempts > 40) {
                clearInterval(iv);
                HTMLInputElement.prototype.click = origClick;
                obs.disconnect();
                hidden.forEach(function(n) { n.style.cssText = ''; });
                _priming = false;
                _log('priming timed out');
            }
        }, 50);
    }

    window._injectClipboardFile = function(b64, mime, filename) {
        _waitingForNative = false;
        mime = mime || 'application/octet-stream';
        filename = filename || ('clipboard.' + (mime.split('/')[1] || 'bin'));
        _log('injecting file: ' + filename + ', mime=' + mime + ', b64len=' + b64.length);

        var byteStr = atob(b64);
        var arr = new Uint8Array(byteStr.length);
        for (var i = 0; i < byteStr.length; i++) arr[i] = byteStr.charCodeAt(i);
        var file = new File([arr], filename, {type: mime});

        primeAndInject(file);
    };

    // Keep old name as alias
    window._injectClipboardImage = window._injectClipboardFile;

    window._injectClipboardImageFailed = function() {
        _log('clipboard injection failed');
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
        settings.set_enable_developer_extras(DEV_LOGGING)
        settings.set_enable_smooth_scrolling(True)
        if DEV_LOGGING:
            settings.set_enable_write_console_messages_to_stdout(True)

        # User scripts
        content_manager = self.webview.get_user_content_manager()
        content_manager.add_script(WebKit.UserScript(
            NOTIFICATION_FIX_JS,
            WebKit.UserContentInjectedFrames.ALL_FRAMES,
            WebKit.UserScriptInjectionTime.START,
            None, None,
        ))
        paste_js = PASTE_BRIDGE_JS.replace('%DEV%', 'true' if DEV_LOGGING else 'false')
        content_manager.add_script(WebKit.UserScript(
            paste_js,
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
        # Intercept target="_blank" links (new window requests)
        self.webview.connect("create", self._on_create_new_window)

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

    @staticmethod
    def _devlog(msg):
        if DEV_LOGGING:
            print(f"[paste] {msg}")

    def _on_paste_requested(self, _content_manager, message):
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        mime_types = formats.get_mime_types() or []
        self._devlog(f"clipboard mimes: {mime_types}")

        if any(m.startswith("image/") for m in mime_types):
            self._devlog("trying texture read")
            clipboard.read_texture_async(None, self._on_clipboard_texture, None)
        elif "text/plain" in mime_types or "text/uri-list" in mime_types:
            self._devlog("trying text read")
            clipboard.read_text_async(None, self._on_clipboard_text, None)
        else:
            self._devlog("no usable format")
            self._inject_failed()

    def _on_clipboard_texture(self, clipboard, result, _user_data):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as e:
            self._devlog(f"texture failed: {e}, falling back to text")
            clipboard.read_text_async(None, self._on_clipboard_text, None)
            return
        if texture is None:
            self._devlog("texture is None")
            self._inject_failed()
            return
        self._devlog(f"got texture {texture.get_width()}x{texture.get_height()}")
        self._send_texture(texture)

    def _on_clipboard_text(self, clipboard, result, _user_data):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error as e:
            self._devlog(f"text read failed: {e}")
            self._inject_failed()
            return
        if not text:
            self._devlog("text is empty")
            self._inject_failed()
            return
        self._devlog(f"clipboard text: {text[:200]!r}")

        path = text.strip()
        if path.startswith("file://"):
            path = unquote(urlparse(path).path)
        # Handle multiple URIs (take first supported file)
        for line in path.splitlines():
            line = line.strip()
            if line.startswith("file://"):
                line = unquote(urlparse(line).path)
            if os.path.isfile(line):
                path = line
                break
        else:
            if not os.path.isfile(path):
                self._inject_failed()
                return

        self._inject_file(path)

    # Mime type map for supported file extensions
    MIME_TYPES = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska", ".3gp": "video/3gpp",
        ".pdf": "application/pdf",
        ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain", ".zip": "application/zip", ".rar": "application/x-rar-compressed",
        ".apk": "application/vnd.android.package-archive",
        ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".opus": "audio/opus",
    }

    def _inject_file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._inject_failed()
            return
        b64 = base64.b64encode(data).decode("ascii")
        ext = os.path.splitext(path)[1].lower()
        mime = self.MIME_TYPES.get(ext, "application/octet-stream")
        filename = os.path.basename(path)
        self._devlog(f"injecting file ({len(data)} bytes, {mime}, {filename})")
        js = f"window._injectClipboardFile('{b64}', '{mime}', '{filename}');"
        self.webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _send_texture(self, texture):
        png_bytes = texture.save_to_png_bytes()
        b64 = base64.b64encode(png_bytes.get_data()).decode("ascii")
        self._devlog(f"injecting texture ({len(png_bytes.get_data())} bytes)")
        js = f"window._injectClipboardFile('{b64}', 'image/png', 'clipboard.png');"
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

    # -- New window requests (target="_blank") --

    @staticmethod
    def _on_create_new_window(_webview, nav_action):
        """Open target="_blank" links in the system browser."""
        req = nav_action.get_request()
        uri = req.get_uri() if req else None
        if uri:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        return None

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
