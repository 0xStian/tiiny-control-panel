# Tiiny Control Panel — desktop app

A native window for the panel: one app that starts the panel's own server next
to it and shows it in a window, with a menu-bar icon. The app and the browser
version are the same panel — same pages, same data (`~/.local/share/tiiny-ui`),
same sign-in — so nothing here reimplements a feature.

How it works:

- The app runs the panel's server as a bundled sidecar (`binaries/tiiny-panel`,
  a PyInstaller build of `tiiny_ui.py`) with `--no-browser`, unless a panel is
  already running on one of its ports — then it just signs into that one.
- The panel prints its one-time sign-in link at start-up; the app reads it and
  points the window at it. The link works the same way it does for a browser.
- Closing the app stops the server **only if the app started it**. A copy you
  started in a terminal keeps running, and the two coexist.

## What you need

- macOS (Apple Silicon or Intel), Xcode Command Line Tools, Rust
  (`brew install rust`), and the npm Tauri CLI (`npm i -g @tauri-apps/cli`).
- The panel itself, in this folder's parent — this app is built from a clone
  of the panel with this `app/` folder in it.
- The official `tiiny` CLI for the panel's interactive features (scan, connect,
  the in-page terminal, firmware). Chat, models and the rest use the device's
  HTTP API and work without it.

## Build it

```sh
# 1. Toolchain (once)
brew install rust
npm i -g @tauri-apps/cli

# 2. The sidecar: a frozen build of the panel (needs a venv with PyInstaller)
python3 -m venv .venv && .venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --onefile --name tiiny-panel \
  --add-data "$PWD/static:static" \
  --distpath app/dist tiiny_ui.py
# (cp drops the executable bit; the sidecar must be runnable)
chmod +x app/dist/tiiny-panel
cp app/dist/tiiny-panel app/binaries/tiiny-panel-$(rustc -vV | grep host | cut -d' ' -f2)

# 3. The app
cd app
tauri icon --generate ../static/icons/icon-512.png   # once
tauri build
```

The finished app is `target/release/bundle/macos/Tiiny Control Panel.app`.

> The app is built, not code-signed: first launch on a *downloaded* copy needs
> right-click → Open (or `xattr -cr "/Applications/Tiiny Control Panel.app"`).
> A copy you built yourself just opens.

## Notes and limits

- **Updating:** the panel's in-app update button is for git and Docker installs;
  this app updates by downloading a new release. The app and the panel share
  one data folder, so an update from a terminal or a new app is the same data.
- **Windows** is not supported yet, for the same reason the panel isn't: the
  in-page terminal needs a Unix pseudo-terminal, and Tiiny's CLI is a Linux
  tool. A Windows build is on the way once that's true for the panel.
- **Microphone** (voice messages, meetings) asks macOS for permission the first
  time you use it, like any app.
- **Phone pairing, routines, gateway keys and everything else in the panel's
  System pages work unchanged** — they're the panel's, not the window's.

## Development

`cargo tauri dev` in this folder runs the app in dev mode. If the frozen
sidecar isn't built yet, it falls back to a system `python3` and the panel's
sources in the parent folder (set `TIINY_PANEL_DIR` to point at another clone).
The panel's start-up output is mirrored to `~/Library/Application Support/ai.tiiny.control-panel/panel.log`
— that's the place to look when something doesn't come up.
