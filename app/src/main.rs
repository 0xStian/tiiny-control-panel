// The Tiiny Control Panel as a desktop app.
//
// The panel is a Python web server (tiiny_ui.py) that you can already run in a
// terminal; it signs your browser in with a one-time link. This app is a thin
// native window around that same server:
//
//   * it starts the server next to the app (the bundled `tiiny-panel` sidecar,
//     a frozen build of the panel) unless a panel is already running — then it
//     just signs into that one, so the app and a terminal copy coexist;
//   * it reads the sign-in link the server prints at start-up and points the
//     window at it;
//   * closing the app stops the server only if the app started it.
//
// Everything else — pages, chat, models — is the panel's own web UI, so the app
// and the browser version stay in lockstep by construction.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, WebviewWindow};

const BASE_PORT: u16 = 8765;
const PORT_SPAN: u16 = 10; // the panel tries the base port, then the next nine (tiiny_ui.py)
const LINK_WAIT: u64 = 45; // seconds to wait for the sign-in link before giving up

/// What the app remembers about the panel server process it may have started.
struct State {
    child: Mutex<Option<Child>>,
    log: Mutex<Option<PathBuf>>,
}

fn port_open(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

fn any_panel_port_open() -> Option<u16> {
    (BASE_PORT..BASE_PORT + PORT_SPAN).find(|port: &u16| port_open(*port))
}

fn which(cmd: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let cand = dir.join(cmd);
        if cand.is_file() {
            return Some(cand.to_string_lossy().into_owned());
        }
    }
    None
}

/// The panel server: the bundled frozen sidecar if it's there, otherwise a
/// system Python with the panel's sources (a dev convenience, so `tauri dev`
/// works before the freeze step in the README has been run).
fn find_command(exe_dir: &Path) -> Option<(String, Vec<String>)> {
    let triple = match std::env::consts::ARCH {
        "aarch64" => "aarch64-apple-darwin",
        "x86_64" => "x86_64-apple-darwin",
        _ => return None, // a Windows build comes later (the panel needs a Unix terminal)
    };
    // The bundler keeps the base name next to the executable; other layouts may keep the triple suffix.
    let names = [String::from("tiiny-panel"), format!("tiiny-panel-{triple}")];
    let mut bases = vec![exe_dir.to_path_buf()];
    if let Some(parent) = exe_dir.parent() {
        bases.push(parent.join("Resources"));
    }
    for name in &names {
        for base in &bases {
            let p = base.join(name);
            if p.is_file() {
                return Some((p.to_string_lossy().into_owned(), vec!["--no-browser".into()]));
            }
        }
    }
    // Dev fallback: system Python + the panel's sources next to this crate.
    let py = std::env::var("TIINY_PYTHON").ok().or_else(|| which("python3")).or_else(|| {
        ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
            .iter()
            .find(|p| Path::new(p).is_file())
            .map(|s| (*s).to_string())
    })?;
    let panel_dir = match std::env::var("TIINY_PANEL_DIR") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().ok()?,
    };
    let script = panel_dir.join("tiiny_ui.py");
    if !script.is_file() {
        return None;
    }
    Some((py, vec![script.to_string_lossy().into_owned(), "--no-browser".into()]))
}

/// The panel prints its sign-in link at start-up — on a `sign in   :` line for
/// a fresh start, on a line of its own when it notices another copy already
/// running. Either way the URL is the only http token on the line.
fn take_link(line: &str) -> Option<String> {
    for tok in line.split_whitespace() {
        if (tok.starts_with("http://") || tok.starts_with("https://")) && tok.contains("/auth?code=") {
            return Some(tok.to_string());
        }
    }
    None
}

fn handle_line(line: &str, log: &mut Option<File>, win: &WebviewWindow, got: &AtomicBool) {
    if let Some(f) = log {
        let _ = writeln!(f, "{line}");
    }
    if got.load(Ordering::SeqCst) {
        return;
    }
    if let Some(url) = take_link(line) {
        if got.swap(true, Ordering::SeqCst) {
            return;
        }
        match tauri::Url::parse(&url) {
            Ok(u) => {
                if win.navigate(u).is_ok() {
                    return;
                }
            }
            Err(_) => {}
        }
        fail(win, "The panel started, but the window couldn't follow its sign-in link.");
        return;
    }
    // No link yet: mirror the panel's start-up lines, so the window shows the progress.
    let _ = win.eval(&format!(
        r#"var m=document.getElementById('msg');if(m)m.textContent={:?};"#,
        line.trim()
    ));
}

/// Stop the panel the app started. A second-start probe (the panel was already
/// running in a terminal) exits on its own, so a child that's gone by now was
/// never the server — its owner is whatever started it.
fn stop_panel(app: &tauri::AppHandle) {
    let state = app.state::<State>();
    let Some(mut child) = state.child.lock().unwrap().take() else {
        return;
    };
    if child.try_wait().ok().flatten().is_some() {
        return; // it already exited: it only signed us in, it isn't the server
    }
    let pid = child.id();
    unsafe {
        libc::kill(pid as i32, 15); // SIGTERM: the panel shuts down gracefully
    }
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(5) {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(_) => break,
        }
    }
    let _ = child.kill(); // only if it's still alive after five seconds
}

/// The window is still on the stub page: tell it what went wrong.
fn fail(win: &WebviewWindow, msg: &str) {
    let _ = win.eval(&format!(
        r#"var s=document.getElementById('spin');if(s)s.style.display='none';var m=document.getElementById('msg');if(m)m.textContent={:?};"#,
        msg
    ));
}

fn log_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("panel.log"))
}

fn start_panel(app: &tauri::AppHandle, win: &WebviewWindow) {
    let state = app.state::<State>();
    let log = log_path(app);
    if let Some(p) = &log {
        if let Some(dir) = p.parent() {
            let _ = fs::create_dir_all(dir);
        }
    }
    *state.log.lock().unwrap() = log.clone();

    // If a panel is already serving on one of its ports, the sidecar signs into
    // that one and exits — the app never starts a second copy.

    let exe_dir = match app.path().executable_dir() {
        Ok(d) => d,
        Err(e) => {
            fail(win, &format!("Can't find the app's own folder: {e}"));
            return;
        }
    };
    let (bin, args) = match find_command(&exe_dir) {
        Some(c) => c,
        None => {
            fail(win, "Can't start the panel: this app ships it as the `tiiny-panel` sidecar next to the app \
                       binary. Rebuild it (app/README.md, step 2), or run the panel from a terminal and \
                       `cargo tauri dev` with TIINY_PANEL_DIR set to a clone of the panel.");
            return;
        }
    };
    // BROWSER points at a no-op: with --no-browser the panel never opens a browser, but if a
    // future version ever did, we'd rather it fail quietly than pop the user's default browser.
    let mut child = match Command::new(&bin)
        .args(&args)
        .env("BROWSER", "/bin/true")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            fail(win, &format!("Couldn't start the panel: {e}"));
            return;
        }
    };
    // Take the pipes before moving the process into the app's state; each
    // thread gets its own clones, so nothing is shared by move.
    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");
    state.child.lock().unwrap().replace(child);

    let win = win.clone();
    let win_watch = win.clone();
    let got = Arc::new(AtomicBool::new(false));
    let got_watch = got.clone();
    let log_file = log.as_ref().and_then(|p| OpenOptions::new().create(true).append(true).open(p).ok());
    let log_for_errors = log;

    thread::spawn(move || {
        let mut logf = log_file;
        for line in BufReader::new(stdout).lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            handle_line(&line, &mut logf, &win, &got);
        }
        for line in BufReader::new(stderr).lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            if let Some(f) = &mut logf {
                let _ = writeln!(f, "[stderr] {line}");
            }
        }
        // The panel exited without ever handing over a link: it failed to start.
        if !got.load(Ordering::SeqCst) {
            let tail = log_for_errors
                .and_then(|p| std::fs::read_to_string(p).ok())
                .map(|t| {
                    t.lines()
                        .rev()
                        .take(3)
                        .filter(|l| !l.trim().is_empty())
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect::<Vec<_>>()
                        .join(" · ")
                })
                .unwrap_or_default();
            fail(&win, &format!("The panel stopped before it finished starting. {tail}"));
        }
    });

    // Watchdog: if the server is up but no link ever came, at least show the panel's front door.
    thread::spawn(move || {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(LINK_WAIT) {
            thread::sleep(Duration::from_secs(2));
            if got_watch.load(Ordering::SeqCst) {
                return;
            }
            if let Some(port) = any_panel_port_open() {
                if let Ok(u) = tauri::Url::parse(&format!("http://127.0.0.1:{port}/")) {
                    let _ = win_watch.navigate(u);
                }
                return;
            }
        }
        if !got_watch.load(Ordering::SeqCst) {
            fail(&win_watch, "The panel didn't answer in time. Start it from a terminal \
                            (`python3 tiiny_ui.py`) to see why, or open this app again.");
        }
    });
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let show = tauri::menu::MenuItem::with_id(app, "show", "Open the panel", true, None::<&str>)?;
    let quit = tauri::menu::MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = tauri::menu::Menu::with_items(
        app,
        &[&show, &tauri::menu::PredefinedMenuItem::separator(app)?, &quit],
    )?;
    let icon = app
        .default_window_icon()
        .cloned()
        .unwrap_or_else(|| tauri::image::Image::new(&[0, 0, 0, 0], 1, 1));
    let _tray = tauri::tray::TrayIconBuilder::new()
        .icon(icon)
        .tooltip("Tiiny Control Panel")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.unminimize();
                    let _ = w.set_focus();
                }
            }
            "quit" => {
                stop_panel(&app.app_handle());
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(State {
            child: Mutex::new(None),
            log: Mutex::new(None),
        })
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .expect("the app has one window");
            start_panel(app.handle(), &window);
            build_tray(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_panel(&window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the app");
}
