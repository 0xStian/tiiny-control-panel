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

- **Only this computer.** It listens on `127.0.0.1` unless you change that with `--host`, which shows a warning.
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
- **Phones must be paired, and get less.** The phone listener is off by default. Each pairing link works once, for 5
  minutes. Paired devices still need the session token. Only fingerprints of their logins are stored, and you can
  remove a device at any time. Phones can't change panel settings, browse or read the computer's folders, run the
  `tiiny` tool or a terminal, reveal the API key, or make the panel fetch addresses on your local network. Without
  `--tls-cert`, phones use plain HTTP, so only use it on a network you trust.
- **The agent asks first.** Agent actions that change something or go to the internet need your approval. Text from
  web pages and search results is marked as untrusted for the model. Web searches send only the search words to the
  chosen engine (DuckDuckGo by default), and a Brave Search key stays on the server.
- **Commands on your computer are opt-in.** The agent can only run commands, or read and write files, when the panel
  was started with `--allow-commands` (the page can't switch it on). Even then, every command and every file change
  needs your approval, shown in full, with no way to allow them for a whole chat. Commands run as you, with no
  terminal or input (so sudo can't work), a time limit, and Stop. Opening a file never runs programs or scripts.
  Phones never get these tools.
- **You choose the agent's tools.** Each tool can be switched off under System → Agent tools. A switched-off tool is
  never offered to the model, and the panel's endpoints that exist only for the agent (history, commands, files) refuse
  it too. The switches can only take tools away, never add the computer tools without `--allow-commands`.
- **Private files.** Saved data is stored in `~/.local/share/tiiny-ui/` and `~/.config/tiiny-ui/`, readable only by you.

Things that are **out of scope**: the Tiiny device and its firmware, the official `tiiny` command-line tool, and
attacks that need someone who can already run programs as your user on your computer (they can read your files,
including the browser key).

Known limit: like any web page on `127.0.0.1`, the panel can't protect you from software that takes its port while
the panel isn't running, then gets your browser to open that address. That software could read the panel's stored
sign-in. If you suspect that happened, delete `~/.config/tiiny-ui/browser-key` and restart the panel to sign every
browser out.
