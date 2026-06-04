# Contributing to deckcheck

Thanks for looking under the hood. This doc explains how deckcheck is put together and,
most usefully, **how to add a new health check**, which is the most common change.

## Architecture in one picture

```
gui/checks.py        <- the engine. Pure-stdlib functions that gather a reading and
                       return a Verdict. No Qt, no GUI deps. Runs standalone as a CLI.
gui/deckcheck_gui.py <- the Qt window. A thin presentation layer that calls the engine
                       and renders Verdict objects as cards. Knows nothing about how a
                       check works, only how to display one.
scripts/*.sh         <- independent command-line tools (snapshot, thermal_log, diskhog,
                       wakeups). The deeper-dive layer; not used by the GUI.
flatpak/             <- packaging (manifest, metainfo, desktop file, launcher).
```

The key design rule: **all check logic lives in `checks.py`.** The GUI never parses bash
output or duplicates logic. This means a check written once works in the GUI, in the CLI
(`python gui/checks.py`), and in any future reporter that reuses the engine.

## The data model (`checks.py`)

- **`Verdict`** is the result of one check. Fields:
  - `title`: short label (e.g. `"Temperature"`)
  - `status`: one of `OK` / `WARN` / `PROBLEM` / `UNKNOWN`
  - `headline`: one plain-English sentence a non-technical user understands
  - `detail`: raw numbers, shown behind a "technical details" toggle
  - `advice`: one-line next step (used in the CLI summary)
  - `what_it_is`, `steps`, `action`, `actions`: the GUI's "What can I do?" content
- **`Action`** is a safe, reversible one-click fix. It carries `label`, `confirm` (what it
  will do), `undo_note` (how to reverse it), and `run()`, which returns `(success, message)`.
  Use `verdict.all_actions()` to get the single + extra actions in display order.

## How to add a new check

1. **Write a function** in `checks.py` named `check_<thing>()` that returns a `Verdict`.
   Gather your reading (read `/proc`/`/sys`, or call a tool via the helpers below), then
   return a `Verdict` with the right status and a plain-English `headline`.

2. **Register it** by adding the function to the `ALL_CHECKS` list. That's it: the GUI
   picks it up automatically and renders a card; the CLI includes it in the summary.

3. **Write for the audience.** The `headline` must make sense to someone who doesn't
   know what `/proc` is. Put numbers in `detail`, not the headline. If `status` isn't
   `OK`, add `what_it_is` + `steps` so the user knows what to *do*, and only add an
   `action` button for something **safe and reversible** (see the safety rules below).

Minimal example:

```python
def check_example() -> Verdict:
    value = _read_int("/sys/.../something")
    if value is None:
        return Verdict("Example", UNKNOWN, "Couldn't read the sensor.")
    if value < 100:
        return Verdict("Example", OK, f"All good ({value}).", detail=f"raw={value}")
    return Verdict("Example", WARN, f"A bit high ({value}).", detail=f"raw={value}",
                   what_it_is="Plain explanation of what this is.",
                   steps=["Step one.", "Step two."])

# then add `check_example` to ALL_CHECKS
```

## Running external commands (and the sandbox)

Don't call `subprocess.run` directly. Use the helpers, which are **sandbox-aware**:

- `_run(cmd)` returns the stdout string
- `_run_full(cmd)` returns the full `CompletedProcess` (for return codes / stderr)

Inside a Flatpak, the sandbox has no access to host binaries or the host's `/proc` PID
namespace. These helpers detect `/.flatpak-info` (`_IN_FLATPAK`) and transparently route
commands through `flatpak-spawn --host`, so the same code works natively and packaged.
If your check scans host `/proc` per-process (like `check_top_writer`), do the scan on
the host when `_IN_FLATPAK` (see `_scan_top_writer` for the pattern).

Two rules that keep the sandbox tight (and the Flathub submission defensible):

- **Prefer reading `/sys` and system-wide `/proc` directly** over shelling out. Both are
  mounted read-only into the sandbox by default, so no permission is needed. The
  temperature/fan check reads `/sys/class/hwmon` via `_hwmon_by_label` rather than running
  `sensors`; `check_cpu_idle`/`check_disk_activity` read `/proc/stat` and `/proc/diskstats`
  directly. Only *per-process* `/proc/<pid>/*` and host daemons need `flatpak-spawn`.
- **Degrade gracefully if the host is unreachable.** The one privileged permission can be
  revoked in Flatseal. Guard host-dependent work with `_host_reachable()`: a read-only
  check should still report what it can (`check_cpu_idle` drops the process name but keeps
  the load figure), and an `Action` should return `_needs_host_msg(<exact terminal command>)`
  so the user is never dead-ended. See `_host_blocked_verdict` for the verdict-level pattern.

## Safety rules for `Action` buttons

deckcheck can change system state, so actions follow strict rules:

- **Only safe, reversible operations get a button.** No destructive or hard-to-undo
  actions. (Example: we *explain* a busy Steam/Proton process but offer no "kill" button.)
- **Always offer an undo.** If an action changes a setting, provide a paired action that
  restores the default (see the file-indexer card's "Put file search back to normal").
- **Confirm first.** The GUI always shows `confirm` + `undo_note` in a dialog before
  running anything, so write those fields clearly.

## Tests

The verdict logic is unit-tested. Each check is deliberately split into a thin **reader**
(`_read_*` / `_scan_*`, does the I/O) and a pure **judge** (`_judge_*`, turns a reading
into a `Verdict`), so the judges can be tested with synthetic readings instead of a real
Steam Deck:

```bash
pip install pytest          # into your venv
python -m pytest tests/      # or just: pytest
```

`tests/test_checks.py` covers every status threshold/boundary (e.g. temperature
59/60/74/75, the four battery-health tiers, the disk and storage cut-offs), the
`overall()` worst-case roll-up, the per-program writer guidance, the sysfs parser,
the graceful-degradation paths (host access off), and that one crashing check can't
sink `run_all()`.

**What is *not* unit-tested, on purpose** (testing it would mostly test mocks):

- The **reader** layer (reading `/sys`, `/proc`, calling `ps`/`balooctl`) is verified
  by running `python gui/checks.py` on a real machine.
- The **GUI** is verified by eye (`./gui/run_gui.sh`). Visual correctness is a human
  check here, not an automated one.

When you add a check, put all the decision logic in the pure `_judge_*` function and
add a test for its boundaries. That's where regressions hide.

## Before you submit

- `python -m pytest tests/` (must pass).
- `python -m py_compile gui/checks.py gui/deckcheck_gui.py` (must pass).
- `lizard gui/` to keep cyclomatic complexity sane (CI fails on CCN > 15).
- `python gui/checks.py` to run the CLI and sanity-check your new verdict reads well.
- If you touched the GUI, run it (`./gui/run_gui.sh`) and look at it; visual checks are
  done by eye, not automated.
- If you touched packaging, rebuild the Flatpak and confirm it still runs (see
  `flatpak/README.md`).
