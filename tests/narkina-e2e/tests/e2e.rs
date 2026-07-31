//! Headless E2E for the WhatsApp GTK wrapper, driven through narkina.
//!
//! The app is launched as `whatsapp.py --test` inside an isolated cage
//! session (headless backend, isolated HOME + XDG_RUNTIME_DIR, no
//! session DBus). Test IO goes through a per-session directory passed
//! via WHATSAPP_TEST_DIR:
//!   F11 — app reads a command from  $WHATSAPP_TEST_DIR/whatsapp-test-input.txt
//!   F12 — app dumps state to        $WHATSAPP_TEST_DIR/whatsapp-test-state.txt
//!
//! One #[test] runs the whole scenario: fixed file paths inside the
//! session mean steps are ordered, and a single cage launch keeps the
//! suite fast (WebKit + WhatsApp warmup dominates).

use narkina::{Modifier, Session};
use std::fs;
use std::path::PathBuf;
use std::time::{Duration, Instant};

const APP: &str = "../../whatsapp.py";
const WARMUP: Duration = Duration::from_millis(8000);
const CMD_DELAY: Duration = Duration::from_millis(800);

struct Harness {
    session: Session,
    test_dir: PathBuf,
}

impl Harness {
    fn launch() -> Self {
        let app = fs::canonicalize(APP).expect("whatsapp.py not found relative to test crate");
        let test_dir = std::env::temp_dir().join(format!("whatsapp-e2e-{}", std::process::id()));
        let _ = fs::remove_dir_all(&test_dir);
        fs::create_dir_all(&test_dir).unwrap();

        let session = Session::builder(&app)
            .arg("--test")
            .env("WHATSAPP_TEST_DIR", &test_dir)
            .stderr_to_file(test_dir.join("app-stderr.log"))
            .expect("stderr redirect failed")
            .spawn()
            .expect("failed to launch whatsapp.py under cage");
        session.sleep(WARMUP);
        Harness { session, test_dir }
    }

    fn input_file(&self) -> PathBuf {
        self.test_dir.join("whatsapp-test-input.txt")
    }

    fn state_file(&self) -> PathBuf {
        self.test_dir.join("whatsapp-test-state.txt")
    }

    /// Write a command + payload, press F11 to make the app execute it.
    fn send_cmd(&self, lines: &[&str]) {
        fs::write(self.input_file(), lines.join("\n")).unwrap();
        assert!(self.session.press_key("F11"), "wtype F11 failed");
        self.session.sleep(CMD_DELAY);
    }

    /// Press F12, wait for the state dump, parse `key=value` lines.
    fn dump_state(&self) -> Vec<(String, String)> {
        let _ = fs::remove_file(self.state_file());
        assert!(self.session.press_key("F12"), "wtype F12 failed");
        let deadline = Instant::now() + Duration::from_secs(4);
        while !self.state_file().exists() {
            if Instant::now() >= deadline {
                let log = fs::read_to_string(self.test_dir.join("app-stderr.log"))
                    .unwrap_or_default();
                panic!("state file never appeared; app stderr:\n{log}");
            }
            std::thread::sleep(Duration::from_millis(150));
        }
        let raw = fs::read_to_string(self.state_file()).unwrap();
        raw.lines()
            .filter_map(|l| l.split_once('=').map(|(k, v)| (k.into(), v.into())))
            .collect()
    }

    fn state(&self, key: &str) -> String {
        self.dump_state()
            .into_iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
            .unwrap_or_default()
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.test_dir);
    }
}

fn assert_alive(h: &mut Harness, what: &str) {
    h.session.assert_alive(what);
}

#[test]
fn full_scenario() {
    let mut h = Harness::launch();

    // ── P0: launch ──────────────────────────────────────────────
    assert_alive(&mut h, "app after warmup");
    let uri = h.state("uri");
    assert!(
        uri.contains("web.whatsapp.com"),
        "expected a web.whatsapp.com URI, got '{uri}'"
    );
    assert!(!h.state("title").is_empty(), "page should have a title");

    // UA must be the Chrome-shaped one (or a config override) — the WebKitGTK
    // default trips WhatsApp's browser-version wall.
    let ua = h.state("ua");
    assert!(
        ua.contains("Chrome/"),
        "expected Chrome-shaped UA, got '{ua}'"
    );

    // ── P1: downloads dir visibility ────────────────────────────
    let dl_dir = h.state("dl_dir");
    assert!(!dl_dir.is_empty(), "download dir should resolve");

    // ── P2: zoom ────────────────────────────────────────────────
    let zoom_before = h.state("zoom");
    h.session.key_combo(&[Modifier::Ctrl], "equal");
    h.session.sleep(Duration::from_millis(400));
    let zoom_after = h.state("zoom");
    assert_ne!(zoom_before, zoom_after, "Ctrl+= should change zoom");

    h.session.key_combo(&[Modifier::Ctrl], "0");
    h.session.sleep(Duration::from_millis(400));
    assert_eq!(h.state("zoom"), "1.0", "Ctrl+0 should reset zoom");

    // ── P3: JS eval + survival ──────────────────────────────────
    h.send_cmd(&["js", "document.title = document.title"]);
    assert_alive(&mut h, "app after JS eval");

    // ── P4: paste path survival (empty clipboard → failure branch) ──
    h.send_cmd(&["paste"]);
    assert_alive(&mut h, "app after paste with empty clipboard");

    // ── P5: window.open popup (related-view path) ───────────────
    h.send_cmd(&["js", "window.open('https://web.whatsapp.com')"]);
    h.session.sleep(Duration::from_millis(1500));
    assert_alive(&mut h, "app after window.open popup");
}
