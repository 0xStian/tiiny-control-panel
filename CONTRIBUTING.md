# Contributing

Thanks for helping make the Tiiny Control Panel better! Bug reports, ideas, fixes and translations
of confusing wording are all welcome.

## Reporting a bug

[Open an issue](https://github.com/0xStian/tiiny-control-panel/issues/new/choose) and include:

- what you did, what you expected, and what happened instead;
- your Linux distribution and `python3 --version`;
- the panel version (shown when you start it) and the output of `tiiny cli version`;
- the related entries from **Activity** (top right of the panel). They show the device's exact answers.
  Check that they don't contain anything private before you paste them.

Security problems: please don't open an issue. See [SECURITY.md](SECURITY.md).

## Running it from source

There's nothing to build or install:

```bash
git clone https://github.com/0xStian/tiiny-control-panel.git
cd tiiny-control-panel
python3 tiiny_ui.py --verbose
```

Edit a file in `static/` and reload the page. Edit a Python file and restart the panel (Ctrl+C, then start it again).

## How the code is organised

| Path | What's there |
|---|---|
| `tiiny_ui.py` | The web server: routes (the `ROUTES` table), security checks, the device proxy, start-up. |
| `tiinyui/device.py` | Talking to the device's HTTP API (`device_open`, `call`, `chat`, `ensure_loaded`, …). |
| `tiinyui/clitools.py`, `tiinyui/pty.py` | Running the `tiiny` command-line tool, including in a real terminal. |
| `tiinyui/store.py`, `tiinyui/jobs.py` | Saved data (JSON files in `~/.local/share/tiiny-ui/`) and background jobs. |
| `tiinyui/projects.py`, `rag.py`, `textextract.py`, `webfetch.py` | Projects, their search index, reading documents and web pages. |
| `tiinyui/review.py`, `studio.py`, `meetings.py`, `metrics.py`, `pairing.py` | Code review, studio jobs, meeting notes, performance history and unloading models (idle ones, and the ones 🎤 and 🔊 borrowed: `Borrowed` in `common.py`), phone pairing. |
| `tiinyui/browserauth.py` | Signing this computer's browser in (one-time links, the stored credential). Read it before touching security checks. |
| `tiinyui/decide.py` | Decision models (`/v1/systemone`): checks questions, picks and loads the model only when it fits, normalises answers. Every caller falls back when it's `Unavailable`. |
| `tiinyui/firmware.py` | TiinyOS updates through the device's upgrade service (the v2 API its own dashboard uses). |
| `tiinyui/activity.py` | What the device is doing right now (the dashboard's Right now card). It keeps a note of the panel's own model requests while they run (`track`; `device.call` and the chat and proxy routes do this) and pairs them with the device's task list, where every unnamed program shows as "API". |
| `tiinyui/npu.py` | The NPU planner: whether a model fits (by the card's real numbers and by the device's own count of listed shares, at most 100%), what may be unloaded to make room, and loading, one at a time under one lock. Everything on the panel's side that loads models uses it (`ensure_ready`, `use`). |
| `tiinyui/gateway.py` | The gateway other apps use (`127.0.0.1:8766`): per-app keys (`apps.json`, hashed), scopes, model names, on-demand loading and forwarding, with streams kept as streams. |
| `tiinyui/mcp.py`, `tools.py` | MCP for AI assistants: the protocol (the stateless 2026-07-28 style and the older `initialize` handshake), the stdio bridge, and the tools. |
| `tiinyui/turns.py`, `agenttools.py` | Chat replies written by the panel (a `chat-turn` job per reply): the context, streaming, the agent loop with its approvals, saving with the chat's `rev`, and the stream pages follow (`/api/turns/{id}/stream`). The agent's tools live in `agenttools.py`; `static/js/agent.js` only lists them for System → Agent tools. |
| `tiinyui/routines.py` | Routines: schedules, the runner thread, agent prompts (unattended turns) and folder reviews that say what's new. |
| `tiinyui/selfupdate.py`, `backup.py` | Updating the panel itself from GitHub releases (git pull or the release ZIP, then a restart), and the ZIP backup of the panel's own data. |
| `tiinyui/websearch.py`, `commands.py`, `history.py` | Agent web search (DuckDuckGo, Brave Search, SearXNG); commands and files on this computer (`--allow-commands` only); searching saved chats, meetings and the Gallery. |
| `static/index.html`, `static/app.css` | The page and all of its styles (desktop, then phones at the end). |
| `static/app.js` | The core: helpers like `h()`, `dev()`, `api()`, the UI kit (`card`, `btn`, `modal`…), most pages. |
| `static/js/*.js` | One file per area: chat, agent, projects, review, gallery, studio, meetings, perf, devices, integrations, routines… They're plain scripts that share one scope, loaded in the order listed in `index.html`; `boot.js` starts the app last. (`signin.js` is the exception: only the sign-in page loads it.) |
| `static/vendor/` | Third-party libraries, unchanged (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). |

## Ground rules

- **No dependencies.** The server uses Python's standard library only, and runs on Python 3.8 and newer.
  The page uses no framework and no build step. Please keep it that way, so anyone can start it with one command.
- **Private by default.** Nothing may be sent anywhere except the user's own Tiiny, unless the user asks for it
  (like reading a web page). No analytics, no fonts or scripts from other sites.
- **Keep it safe.** Every new API route goes through the existing token and Host checks. Anything that changes the
  device or deletes data asks first. In agent mode, actions that change something need the user's approval.
- **Plain language.** The panel is used by people who aren't developers. Write buttons and messages the way you'd
  explain it to a friend, say what happened and what to do next, and avoid jargon.
- **Works on phones.** Check new screens at about 390 px wide (the phone styles are at the end of `app.css`).

## Before you open a pull request

Run the same checks as the automatic CI:

```bash
python3 -m compileall -q tiiny_ui.py tiinyui
for f in static/*.js static/js/*.js; do node --check "$f"; done
python3 tiiny_ui.py --no-browser &   # then open http://127.0.0.1:8765 and try your change
```

If you can, try your change with a real Tiiny, and say in the pull request what you tested and how.
Keep pull requests focused on one thing, and describe the change from the user's point of view.

## Making a release

Releases are what the panel's **Check for updates** (System → Tiiny Control Panel) offers people.

1. Set the new version in `tiinyui/__init__.py` (`VERSION = "1.2.0"`).
2. In `CHANGELOG.md`, rename **Unreleased** to the version and date (`## [1.2.0] - 2026-12-01`), add a new empty
   **Unreleased** above it, and update the links at the bottom.
3. Commit, then tag and push: `git tag v1.2.0 && git push && git push --tags`.

The **Release** workflow checks that the tag, `VERSION` and the changelog agree, and publishes the GitHub release
with that version's changelog section as its notes.

An update only replaces the panel's own files: `tiiny_ui.py`, `tiinyui/`, `static/`, `docs/`, `.github/` and the
documents at the top level. If a release needs a new top-level folder, add it to `MANAGED_DIRS` in
`tiinyui/selfupdate.py` one release before it's used.

By contributing, you agree that your work is released under the [MIT License](LICENSE).
