# Flatpak packaging

deckcheck packaged as a Flatpak (`io.github.eagredev.deckcheck`), built on the KDE
6.10 runtime with PySide6 bundled as pinned, offline wheels.

## Build & install locally

```bash
flatpak run org.flatpak.Builder --force-clean --user --install \
  build-dir flatpak/io.github.eagredev.deckcheck.yml
flatpak run io.github.eagredev.deckcheck
```

(Requires `org.kde.Platform//6.10`, `org.kde.Sdk//6.10`, and `org.flatpak.Builder`.)

## Permissions

deckcheck requests one privileged permission: `--talk-name=org.freedesktop.Flatpak`,
which enables `flatpak-spawn --host`.

`flatpak-spawn --host` runs arbitrary commands on the host, so the permission is in effect
a full sandbox escape. The limited set of commands deckcheck runs is a property of its
code, not a constraint the sandbox imposes. It's requested because a system diagnostic has
no sandboxed alternative, the same grounds on which the system monitor
[Mission Center](https://flathub.org/apps/io.missioncenter.MissionCenter)
(`io.missioncenter.MissionCenter`) carries the identical flag:

- **No portal covers it.** A sandbox has its own PID namespace, so host processes, and
  host daemons like the KDE file indexer, are invisible from inside it. No XDG portal
  exposes host process information or controls the file indexer, so the data deckcheck
  exists to report is unreachable from within the sandbox.
- **The usage is small and auditable.** Every host call is a fixed command: `ps`, `df`, a
  no-input `/proc` read for per-process writes, and (only on explicit per-action
  confirmation) the user's own tools (`balooctl`, `kill -15`, `xdg-open`). deckcheck
  bundles and installs none of them, and interpolates no untrusted input into any shell.
  The call sites are all in `gui/checks.py` (`_run` / `_run_full` / `_scan_top_writer`).
- **Nothing else broad is requested.** No `--filesystem` of any kind (temperature, fan,
  battery and disk-rate come from the `/sys` and `/proc` read-only mounts), no
  `--device=all`, no broad `--socket=session-bus`. This is tighter than Mission Center,
  which carries both `--device=all` and several filesystem mounts.

**Safety model:** every action that changes the system shows a plain-English
confirmation first, states how to undo it, and only performs safe/reversible operations
(the file-indexer card even ships a one-click "restore defaults"). The read-only checks
change nothing.

**Graceful degradation:** if a user revokes the talk-name in Flatseal, deckcheck doesn't
break. The five read-only checks (temperature, CPU load, disk rate, storage, battery)
still work from `/sys`+`/proc`, and the process-aware checks/fixes explain how to re-enable
access *and* print the exact terminal command to run instead. Nothing dead-ends.

Inside the sandbox, host commands are transparently routed through `flatpak-spawn --host`
(see `_IN_FLATPAK` / `_wrap` / `_host_reachable` in `gui/checks.py`); the same code runs
unwrapped natively.

## Validation

```bash
# Metainfo (also run in CI):
appstreamcli validate flatpak/io.github.eagredev.deckcheck.metainfo.xml

# Manifest lint:
flatpak run --command=flatpak-builder-lint org.flatpak.Builder \
  manifest flatpak/io.github.eagredev.deckcheck.yml
```

`flatpak-builder-lint` reports one finding, `finish-args-flatpak-spawn-access`: the
`--talk-name=org.freedesktop.Flatpak` permission documented above. A builddir/repo lint
also reports `appstream-external-screenshot-url`; that one is expected and resolves
automatically, as the Flathub build pipeline mirrors screenshots to `dl.flathub.org`
(the metainfo keeps the upstream URL, so don't rewrite it).
