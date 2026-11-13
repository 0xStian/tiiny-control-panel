# Security

The Tiiny Control Panel can control a device on your network, so security reports are taken seriously.

## Reporting a problem

Please **don't open a public issue** for a security problem. Instead, report it privately through
GitHub: open the repository's **Security** tab and press **Report a vulnerability**
([direct link](https://github.com/0xStian/tiiny-control-panel/security/advisories/new)).

Include what an attacker could do, the steps to reproduce it, and the panel version. You'll get an answer as
soon as possible. Once a fix is released, you'll be credited if you'd like to be.

Only the latest release gets security fixes.

## How the panel protects you

- **Only this computer.** It listens on `127.0.0.1` unless you change that with `--host`, which shows a warning, or
  start it in server mode (below).
- **Only a browser it signed in.** Every program and user account on the computer can reach `127.0.0.1`, so the page
  carries no secrets. When the panel starts, it opens your browser with a one-time sign-in link (it expires after 10
  minutes). That link gives the browser a credential, which it keeps in its storage for the panel's exact address
  (port included). Each time the page loads, it swaps that credential for the session token, which is new every time
  the panel starts. A cookie isn't used, because browsers send cookies for `127.0.0.1` to every port, including
  other programs' servers. The links and credentials are made from `~/.config/tiiny-ui/browser-key` (readable only by
  you). A second start only opens a link on a panel that proves it knows that key, so a program that grabbed the port
  first never gets one.
- **Only its own pages.** API requests must carry the panel's own `Host` and `Origin` and the session token. Other
  websites open in your browser can't use it.
- **The page can't make it run other programs.** Which `tiiny` program it runs, and with which config file, can only
  be set when starting it (`--tiiny`, `--cli-config`). The same goes for letting the agent run commands
  (`--allow-commands`, below).
- **The API key stays on the server.** It's read from the `tiiny` command-line tool's config. It's sent to the page
  only when you press **Reveal**, and it's never written to the panel's own files.
- **Logging in sends the password only to the Tiiny.** **Log in** asks for the Tiiny's password and sends it to the
  Tiiny's account service (`/api/v1/account/auth`), the same request `tiiny login` makes. The panel doesn't keep or log
  it. The key the Tiiny answers with is saved where `tiiny login` keeps it, in the tool's config, readable only by you.
  Only this computer and devices with full access can log in.
- **Phones must be paired, and get less.** The phone listener is off by default. Each pairing link works once, for 5
  minutes. Paired devices still need the session token. Only fingerprints of their logins are stored, and you can
  remove a device at any time. Phones can't change panel settings, browse or read the computer's folders, run the
  `tiiny` tool or a terminal, reveal the API key, update the panel, back up or restore its data, or make the panel
  fetch addresses on your local network. They can't allow the agent's steps that run commands, change files or open
  things on the computer (they can decline them), replies they start never get those tools, and they can't set up
  routines that review a folder or may read or change the computer's files. Phones connect over HTTPS, with your
  certificate (`--tls-cert`) or one the panel makes itself (below); `--no-tls` switches that off.
- **The agent asks first.** Agent actions that change something or go to the internet need your approval. Text from
  web pages and search results is marked as untrusted for the model. Web searches send only the search words to the
  chosen engine (DuckDuckGo by default), and a Brave Search key stays on the server.
- **Commands on your computer are opt-in.** The agent can only run commands, or read and write files, when the panel
  was started with `--allow-commands` (the page can't switch it on). Even then, every command and every file change
  needs your approval, shown in full, with no way to allow them for a whole chat. Commands run as you, with no
  terminal or input (so sudo can't work), a time limit, and Stop. Opening a file never runs programs or scripts.
  Phones never get these tools, and approving them only works on the computer.
- **Routines can only do what you allowed.** A routine runs the agent with nobody watching: tools that only look run,
  the tools you ticked for it run, any other tool is refused for that run, and commands and file changes still wait
  for your approval in the routine's chat. Routines never unload your models.
- **You choose the agent's tools.** Each tool can be switched off under System → Agent tools. A switched-off tool is
  never offered to the model, and the panel's endpoints that exist only for the agent (history, commands, files) refuse
  it too. The switches can only take tools away, never add the computer tools without `--allow-commands`.
- **Updates only when you ask, and only from this repository.**
  - **Checking:** "Check for updates" (or the daily check, if you switch it on) asks GitHub's API for the latest
    release of `0xStian/tiiny-control-panel`, with a generic User-Agent and nothing about you.
  - **A git copy:** it runs `git pull --ff-only` with fixed arguments, and refuses if the panel's files were changed
    on this computer.
  - **Any other copy:** it downloads the release ZIP over HTTPS and checks it before using it. The ZIP must contain only
    the panel's own files: no paths outside its folder, no links, and the version the release says it is. The old
    files are kept in `.previous/`.
  - **What it never touches:** your data, and any other file in the panel's folder.
  - **Who can do it:** only this computer; phones can't.
- **Backups without secrets.**
  - **What's left out:** a backup of the panel's data never contains the browser key, paired phones, the device's API
    key or a Brave Search key.
  - **Restoring:** unsafe file names are refused, so nothing is written outside the data folder. What a restore
    replaces is set aside first.
  - **Who can do it:** only this computer.
- **Apps use their own keys, through the gateway.**
  - **Where:** apps connect to the panel at `127.0.0.1:8766`, not to the device. Only requests with this computer's
    `Host` are answered (against DNS rebinding), and requests from web pages (an `http(s)` Origin) are refused, with no
    CORS.
  - **Keys:** each app has its own key, made under Integrations and shown once. Only a fingerprint (SHA-256) of each key
    is stored, in `~/.config/tiiny-ui/apps.json` (readable only by you). A revoked key stops working at once. Repeated
    wrong keys from one address make the gateway stop answering it for a while.
  - **What a key can do:** only what you ticked for that app (chat, embeddings, pictures, speech, OCR, decisions, Vault
    search, Vault saving, tools). Only a fixed list of the device's endpoints is passed on, so no key can load, unload or
    delete models directly, restart the device, change its Wi-Fi or account, or reach connectors. When an app asks for a
    model that isn't loaded, its loading policy decides: by default the model is loaded only if it fits, and nothing is
    unloaded for it.
  - **The device's key stays on the server.** Apps no longer need it.
  - **Other computers:** a second listener on your network address is off unless you switch it on. Its requests may
    name any IP address in `Host` (port mapping and NAT, as for a container or a VM, change the address an app
    connects to), or in server mode a name the panel answers. Other names are refused, against DNS rebinding. Without
    `--tls-cert` it uses plain HTTP, so keys travel unencrypted through your network. Ollama apps that can't send a key
    can be allowed, but only from this computer, and only for chat and embeddings.
  - **Who can manage apps:** only this computer; phones can't.
- **Tools for AI assistants (MCP)** use the same app keys and need the "Tools (MCP)" scope, and each tool also needs its
  own scope. The stdio bridge (`python3 tiiny_ui.py mcp`) reads the files an assistant names on behalf of the assistant
  that started it; the panel itself never opens a path an app gives it.
- **Server mode** (`--server`, and the Docker image) is for an always-on machine that your devices reach over the
  network. It's off unless you start the panel that way.
  - **Every device signs in.** A one-time link signs a device in. Links are signed with the browser key, expire after
    10 minutes and work once: used ones are remembered in `devices.json` until they expire, so a restart can't make
    one work again. A link comes from a signed-in device with full access, from `python3 tiiny_ui.py pair` on the
    server (which needs the panel's config folder), or from the panel's log when it starts while no device has full
    access. Treat that log like a password while the link is valid. Too many wrong links or codes pause signing in.
  - **The first device can use the setup code** instead. While no device has full access, the panel's page asks for
    it. The code is 8 random characters (about 8.5 × 10^11 possibilities). It's kept in `setup-code.txt`, readable only
    by the panel's user (in Docker, at the top of the data folder), and `python3 tiiny_ui.py code` shows it. It works
    only until a device has full access, then the file is removed. The form only takes requests from the panel's own
    pages (`Origin`), and wrong codes count toward the same pause.
  - **No trust by address.** Requests from the server itself (like a reverse proxy's) must sign in too, and the
    `127.0.0.1` sign-in links and `POST /api/session` aren't served in server mode.
  - **Idle connections are closed** after two minutes (the gateway's too), so connections that are opened and left
    unused can't hold the panel's threads. An answer, a stream or an upload can take as long as it needs.
  - **Sessions:** an `HttpOnly`, `SameSite=Lax` cookie (also `Secure` when it's set over HTTPS, or behind a proxy that
    says the browser used HTTPS), plus the page's session token, which is new every time the panel starts. Only
    fingerprints of the cookies are stored. A removed device is signed out at once.
  - **Two kinds of access:** full access can do everything this computer's browser can; limited access gets the phone
    rules above. Only full access can add or remove devices, change settings, see the server's folders, use the
    terminal, or allow commands and file changes.
  - **Against DNS rebinding,** only IP addresses, the server's own name (`nas` and `nas.local`, which only resolve on
    the local network) and the names listed in `--public-name` (`TIINY_UI_PUBLIC_NAMES`) are answered, and requests
    that change something must come from such an origin. The gateway's listener for other computers answers the same
    addresses and names.
  - **HTTPS by default:** with your certificate (`--tls-cert`, `--tls-key`), or else one the panel makes itself (below).
    `TIINY_UI_TLS=off` (`--no-tls`) switches to plain HTTP.
- **The panel's own certificate** (`tiinyui/tlscert.py`) is for server mode and the phone listener when you don't give
  one:
  - **What it is:** RSA-2048 with SHA-256, self-signed, not a CA, made with Python's standard library. It covers
    `localhost`, this machine's name, `name.local` and its network address. Its key is in `tls/` in the config
    folder, readable only by you.
  - **Why browsers warn:** no public authority vouches for home addresses, so each browser warns once. The page you
    get over `http://` shows the certificate's SHA-256 fingerprint, so you can compare it with what the browser shows.
  - **What it isn't used for:** the gateway's listener for apps on other computers, because apps can't accept a
    self-made certificate. That one uses only yours.
  - **One port, both ways:** each connection is HTTPS or plain HTTP. Plain HTTP from another device only gets a page
    pointing at the `https://` address (other methods are refused), after the Host check. Plain HTTP from the machine
    itself is served as before (health checks, a local browser, reverse proxies), and still needs a sign-in.
- **In the Docker image:**
  - **Not as root:** the panel runs as the user that owns its data folder (`PUID` and `PGID`, by default 1000).
  - **Only what you give it:** the container sees only its own files and the folders you mount. With
    `--allow-commands`, the agent's commands run inside the container, each after your OK.
  - **Tiiny's tool isn't bundled:** on its first start, the container installs it into the data folder with Tiiny's
    official installer, from `tiiny-cli.tiiny.cloud`.
  - **Updates:** the panel doesn't replace its own files there. New versions come as new images, built from this
    repository by its Release workflow and published at `ghcr.io/0xstian/tiiny-control-panel`.
- **Private files.** Saved data is stored in `~/.local/share/tiiny-ui/` and `~/.config/tiiny-ui/`, readable only by you.

Things that are **out of scope**: the Tiiny device and its firmware, the official `tiiny` command-line tool, and
attacks that need someone who can already run programs as your user on your computer (they can read your files,
including the browser key). In server mode, that includes anyone who can use Docker on the server or read the panel's
data folder.

Known limit: like any web page on `127.0.0.1`, the panel can't protect you from software that takes its port while
the panel isn't running, then gets your browser to open that address. That software could read the panel's stored
sign-in. If you suspect that happened, delete `~/.config/tiiny-ui/browser-key` and restart the panel to sign every
browser out.
