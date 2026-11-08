# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-08

### Added

- **Panel updates.** **System → Tiiny Control Panel** shows which version you have and checks GitHub for a new one when
  you ask, or once a day if you switch that on. It shows what's new, then updates itself:
  - **A git copy:** it runs `git pull`, the same as you would.
  - **Any other copy:** it downloads the release, checks it and swaps the files, keeping the old version in `.previous/`.

  Then **Restart now**, and the page reloads by itself. Your chats, projects and settings aren't touched.
- **Backups of the panel's own data.** **System → Your panel's data** downloads your chats, projects, meetings, Gallery
  and code reviews as one ZIP file, optionally with the performance history. Restoring one either adds what's missing
  or replaces everything; the current data is set aside first. API keys and sign-ins are never in a backup.
- **Voice in chat.**
  - **🎤 in the message box:** say your message, and a speech recognition model on your Tiiny writes it down in the box
    for you to check and send.
  - **🔊 Read aloud under each answer:** a voice model on your Tiiny speaks the answer, leaving out code and Markdown.
  - **Models are unloaded again afterwards.** When 🎤 or 🔊 had to load its model, the panel unloads it 2 minutes
    after its last use, even if you close the page. A model that was already loaded stays loaded.
  - **Chat ⋮ → Voice…** picks the models and the voice, and how long to wait before unloading (or not to).
- **Releases on GitHub.** Each version is published as a GitHub release from the changelog. That's what the update
  check looks at.
- **Background cut-out for 3D.** Studio → 3D cuts the object out of your picture with FLUX.2-klein Background-Remove
  on your Tiiny, shows the cut-out before the 3D run, and sends it with a transparent background, so the background
  stays out of the model. The device's own background step needs a model that TiinyOS 0.1.39 doesn't have
  (BiRefNet). Without the add-on, the 3D page links to it in the model store.
- **Download counts in the model store.** Every store model shows how often it's been downloaded, and the store can be
  sorted by **Most downloaded**.

### Changed

- **System → Device backup** is the new name of the device's backup card. It sits next to **Your panel's data**.

### Fixed

- Image add-ons made for one job (Background-Remove, Object-Remove, Outpaint) are no longer picked for general image
  edits by the agent, and the Playground says what they're for.
- Following a link to a page of the panel while it was still signing the browser in no longer sends a burst of
  refused requests.

## [1.0.0] - 2026-11-06

The first public release.

### Added

- **Dashboard.** **Right now**: what the device is working on and for whom. The panel's own chats, pages and background
  jobs are named, with a link and their progress; other apps show by name, and models loading or downloading are
  listed too. Each task has Stop or Cancel. Also: live NPU, CPU, memory and disk use (the NPU numbers read from the
  NPU card itself); the loaded models with an NPU-memory bar; history charts from 15 minutes to 7 days; speed per
  model; quick actions. The agent's device check says what's running as well.
- **Models.** Load and unload, download from the model store, import from Hugging Face, benchmark and delete.
  The panel measures how much NPU memory each model really uses, offers to make room when a model doesn't fit, and can
  unload idle models (with pins for the ones to keep).
- **Chat.**
  - Streaming answers with a live, scrollable thought process.
  - Images and documents as attachments.
  - Files pinned to a chat, with clickable citations.
  - Answers from the Vault and your profile.
  - Instructions per chat.
- **Agent mode.**
  - Tools: search the web, the Vault and project files, read web pages, check the device, load and unload models,
    make images, calculate.
  - Approvals before anything changes.
  - Never unloads the model it runs on; swaps out a big chat model temporarily to make an image.
  - Web search (DuckDuckGo with no account, or Brave Search or your own SearXNG), so it reads real pages
    instead of guessing addresses; it can also save a web page to the Vault.
  - Searches your past chats, meeting notes and transcripts, and the Gallery, and opens what it finds.
  - Edits images ("make it night time"): the last one in the chat, an attached picture, or one from the Gallery.
  - A switch for every tool under System → Agent tools; switched-off tools are never offered to the model.
  - Optional, with `--allow-commands`: run commands, and find, open, read and write files on your computer,
    asking before every command, file change and file it opens.
- **Projects.** Workspaces with their own instructions, model, files, web pages, a linked folder and an editable memory.
- **Vault.** Upload and index files, search by meaning, save web pages (also with a bookmark button).
- **Meetings.** Live transcripts from the microphone or a recording, and notes written by a model on your device:
  overview, decisions, action items and open questions.
- **Code review.**
  - Security and bug review of a folder, ZIP or project by a model on your device.
  - A pattern scan for secrets and risky calls.
  - Findings with suggested fixes.
  - Exports as Markdown, SARIF and JSON.
  - Pauses and resumes if the device goes away.
- **Playground.** Make and edit images, text to speech (describe a voice, copy a voice from a recording, or a
  built-in voice, whichever the model supports), transcription, OCR, embeddings and reranking.
- **Studio.** Songs (preview, full song, separate tracks), image to 3D with a 3D viewer, and video when a video
  model is installed.
- **Gallery.** Everything the device made, kept on your computer, with hand-offs to editing, chat, 3D and video.
  Select several items (click, Shift-click or Select all) to delete them or add them to your favorites together.
- **Integrations.** Ready-to-copy setups for apps and code that speak the OpenAI, Anthropic or Ollama API, with a
  test for each.
- **Decision models (KnowLine, d3).** Models that answer yes/no, pick-one and score questions with a probability for
  each answer (`/v1/systemone`, TiinyOS 0.1.39). The panel uses one, when it fits next to the loaded models, to:
  - offer the agent only the tools a message needs, and add risk notes to commands it wants to run;
  - decide whether to think (chat's **Auto** thinking);
  - leave out Vault passages that don't fit the question;
  - double-check code-review findings (*Likely real*, *Hide unlikely*);
  - tag Gallery pictures;
  - skip project-memory updates with nothing to remember;
  - list a meeting's action items, decisions and open questions.
  A Playground **Decide** tab, a **System → Decision models** card with a switch per feature, and Integrations
  examples. Without a decision model everything works as before.
- **Device management.** Connectors, Wi-Fi, finding devices, account and login, backup and restore, firmware
  updates, power, and a real terminal for the `tiiny` command-line tool.
- **Firmware updates without the CLI.** System → Firmware talks to the device's upgrade service directly: what's new,
  Install (the device downloads and checks the update, then installs it) with step-by-step progress through the
  restart, Stop while downloading, and Restart now when an update needs one more restart. `tiiny upgrade` (CLI v0.0.5) fails with HTTP 404 on TiinyOS 0.1.38's new upgrade service.
- **Clear device states.** The panel tells you when the device is locked after a restart, starting up, or offline.
- **Phones.**
  - A phone layout.
  - Pairing a phone with a QR code.
  - Installing as an app.
  - Desktop notifications.
  - An app-menu entry on Linux.
- **Runs anywhere Python does.** It works on any common Linux distribution with Python 3.8 or newer, without
  installing anything else.

### Security

- **Browser sign-in.** The panel only works in a browser it signed in with a one-time link, so other programs and
  user accounts on the computer can't use it. See [SECURITY.md](SECURITY.md).
- **Start-up options for programs.** The `tiiny` program and its config file can only be set when starting the panel
  (`--tiiny`, `--cli-config`).
- **A safer subset for phones.** No panel settings, terminals, folder browsing, local folders in reviews and projects,
  API key, or fetching local-network addresses.

[Unreleased]: https://github.com/0xStian/tiiny-control-panel/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/0xStian/tiiny-control-panel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/0xStian/tiiny-control-panel/releases/tag/v1.0.0
