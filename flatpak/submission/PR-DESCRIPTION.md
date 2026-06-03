<!--
Draft PR description for the flathub/flathub submission ("Add io.github.eagredev.deckcheck").
NOT part of the build; this is the text to paste into the PR. Kept in-repo so the public
wording is reviewable before it goes out. Delete or keep as you like; it's harmless.
-->

deckcheck is a system health-check tool for the Steam Deck (and most Linux desktops): a
native Qt (PySide6) app that reports temperature, CPU load, disk activity, free storage,
battery health, the heaviest disk-writer, and the status of KDE's file indexer (baloo),
each in plain English, colour-coded. Where a problem is found and a fix is safe and
reversible, it offers a confirmed, undoable one-click fix.

### Permissions

deckcheck requests a single privileged permission: `--talk-name=org.freedesktop.Flatpak`
(which enables `flatpak-spawn --host`).

`flatpak-spawn --host` runs arbitrary host commands, so the permission is in effect a full
sandbox escape. The limited set of commands deckcheck runs is a property of the code, not
a constraint the sandbox imposes. It's requested because a host diagnostic has no
sandboxed alternative, the same grounds on which Mission Center
(`io.missioncenter.MissionCenter`) carries the identical flag:

- **No portal covers it.** A sandbox's PID namespace hides host processes, and host
  daemons like baloo aren't reachable either. No XDG portal exposes host process
  information or controls the file indexer, so the data the app exists to report is
  unreachable from within the sandbox.
- **The usage is small and auditable.** Every host call is a fixed command: `ps`, `df`, a
  no-input `/proc` read for per-process writes, and (only on explicit per-action
  confirmation) the user's own tools `balooctl` / `kill -15` / `xdg-open`. It bundles and
  installs none of them and interpolates no untrusted input into any shell. The call sites
  are all in `gui/checks.py` (`_run` / `_run_full` / `_scan_top_writer`).
- **Nothing else broad is requested.** No `--filesystem` of any kind (temps/fan/battery/
  disk come from the `/sys` and `/proc` read-only mounts), no `--device=all`, no broad
  `--socket=session-bus`. This is tighter than Mission Center, which carries the same
  talk-name plus `--device=all` and several filesystem mounts.

If a user revokes the permission in Flatseal the app degrades gracefully rather than
breaking: the read-only checks keep working and the rest explains how to re-enable it.

### Other notes

- Builds reproducibly from a pinned git tag.
- The verdict logic is unit-tested (CI runs the tests, a complexity check, AppStream
  validation, and a manifest lint on every push).
- The `appstream-external-screenshot-url` lint finding is expected (the screenshot URL is
  mirrored by the build pipeline per the docs; the metainfo keeps the external URL).
