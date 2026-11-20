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

## Download a prebuilt app

GitHub Actions (`.github/workflows/app.yml`) builds the app on every push and
publishes it on the repository's [Releases](https://github.com/0xStian/tiiny-control-panel/releases) page:

- **Linux:** `Tiiny-Control-Panel_<version>_amd64.AppImage` (x86-64) or
  `…_aarch64.AppImage` (64-bit ARM) — `chmod +x` it, then run it. The builds
  target Ubuntu 24.04, so a fairly recent distro (roughly 2024 or newer) is
  needed to run them.

Every push updates a rolling "Latest from \<branch\>" release; pushing a `v*`
tag adds the same assets to that version's release, next to the Docker image.

The app opens a window, so it wants a screen: on a headless machine use the
browser or the Docker install instead. The recipe below builds the same app
yourself.

## What you need

- A Linux desktop (x86-64 or 64-bit ARM, Ubuntu 24.04-class), with the
  libraries a window needs: `libwebkit2gtk-4.1-dev`, `build-essential`,
  `file`, `libxdo-dev`, `libssl-dev`, `libayatana-appindicator3-dev`,
  `librsvg2-dev`, and `python3-venv`.
- Rust (via `rustup` or your distro) and the npm Tauri CLI
  (`npm i -g @tauri-apps/cli`).
- The panel itself, in this folder's parent — this app is built from a clone
  of the panel with this `app/` folder in it.
- The official `tiiny` CLI for the panel's interactive features (scan,
  connect, the in-page terminal, firmware). Chat, models and the rest use
  the device's HTTP API and work without it.

## Build it

```sh
# 1. Toolchain (once)
sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev python3-venv
npm i -g @tauri-apps/cli

# 2. The sidecar: a frozen build of the panel (needs a venv with PyInstaller)
python3 -m venv .venv && .venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --onefile --name tiiny-panel \
  --add-data "$PWD/static:static" \
  --distpath app/dist tiiny_ui.py
chmod +x app/dist/tiiny-panel
mkdir -p app/binaries
cp app/dist/tiiny-panel app/binaries/tiiny-panel-$(rustc -vV | grep host | cut -d' ' -f2)

# 3. The app
cd app
tauri icon --generate ../static/icons/icon-512.png   # once
tauri build --bundles appimage
```

The finished app is `target/release/bundle/appimage/Tiiny Control
Panel_<version>_<arch>.AppImage` — `chmod +x` it and run it.

The same steps run in GitHub Actions on every push (rustup instead of the
distro's Rust, and the Tauri CLI version pinned). That's how the prebuilt
apps above are made.

## Notes and limits

- **Updating:** the panel's in-app update button is for git and Docker
  installs; this app updates by downloading a new release. The app and the
  panel share one data folder, so an update from a terminal or a new app is
  the same data.
- **Windows** is not supported yet, for the same reason the panel isn't: the
  in-page terminal needs a Unix pseudo-terminal, and Tiiny's CLI is a Linux
  tool. A Windows build is on the way once that's true for the panel.
- **macOS** isn't built in CI for now (the app code is the same; in place of
  the Linux recipe it wants Xcode CLT + `brew install rust`, then a plain
  `tauri build`, and you get a `Tiiny Control Panel.app` you can run locally).
- **Phone pairing, routines, gateway keys and everything else in the panel's
  System pages work unchanged** — they're the panel's, not the window's.

## Development

`cargo tauri dev` in this folder runs the app in dev mode. If the frozen
sidecar isn't built yet, it falls back to a system `python3` and the panel's
sources in the parent folder (set `TIINY_PANEL_DIR` to point at another
clone). The panel's start-up output is mirrored to
`~/.local/share/ai.tiiny.control-panel/panel.log` — that's the place to look
when something doesn't come up.
