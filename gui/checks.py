"""
deckcheck health checks, the brain behind the GUI.

Each check gathers a reading and returns a Verdict: a green/amber/red status plus a
plain-English sentence a non-technical user can act on. The raw numbers ride along in
`detail` for the curious, but the headline is always something anyone understands.

Pure stdlib + reading /proc and the `sensors` command, no root needed for any of the
default checks. Designed so the GUI never has to parse bash output: it calls these
functions and renders the Verdict objects directly.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

OK = "ok"
WARN = "warn"
PROBLEM = "problem"
UNKNOWN = "unknown"


@dataclass
class Action:
    """A safe, reversible thing the GUI can offer to do on the user's behalf.

    Every action carries the plain-English confirmation text and an undo note, so the
    GUI can always show 'this will do X; you can undo it by Y' before running anything.
    `run` returns (success, message). Only safe/reversible operations get an Action;
    risky or judgement-dependent items stay explain-only.
    """
    label: str                         # button text, e.g. "Turn off file indexer"
    confirm: str                       # what it will do, plain English
    undo_note: str                     # how to reverse it, plain English
    run: Callable[[], "tuple[bool, str]"]
    needs_root: bool = False           # GUI should expect a sudo/polkit prompt


@dataclass
class Verdict:
    """The result of one health check: a status, a plain-English headline, and
    optional 'what can I do?' guidance (explanation, steps, and safe fix actions)."""
    title: str            # short label, e.g. "Temperature"
    status: str           # OK / WARN / PROBLEM / UNKNOWN
    headline: str         # plain-English sentence for a non-technical user
    detail: str = ""      # raw numbers / extra context, shown behind a toggle
    advice: str = ""      # one-line next-step (kept for the CLI summary)
    # Richer guidance for the GUI, answers "what do I actually do?":
    what_it_is: str = ""  # plain explanation of what this thing even is
    steps: list = field(default_factory=list)  # numbered plain-English how-to steps
    action: Optional[Action] = None            # optional one-click safe fix
    actions: list = field(default_factory=list)  # extra actions (e.g. gentle fix + undo)

    def all_actions(self) -> list:
        """Every offered action, single + extras, in display order."""
        out = []
        if self.action:
            out.append(self.action)
        out.extend(self.actions)
        return out


# --- small helpers -------------------------------------------------------------

# When running inside a Flatpak sandbox, host tools (sensors, balooctl, etc.) aren't
# on our PATH, they must be invoked on the host via flatpak-spawn. We detect the
# sandbox once and transparently wrap every external command, so the check/action code
# elsewhere stays identical whether run natively or packaged.
_IN_FLATPAK = os.path.exists("/.flatpak-info")


def _wrap(cmd: list[str]) -> list[str]:
    """Prefix a command so it runs on the host when we're sandboxed."""
    if _IN_FLATPAK:
        return ["flatpak-spawn", "--host", *cmd]
    return cmd


# Cache the host-reachability probe: True if we can actually run host commands.
# Natively that's always true; in the sandbox it depends on the flatpak-spawn
# permission (--talk-name=org.freedesktop.Flatpak) still being granted, a user
# could strip it in Flatseal, so we verify rather than assume.
_host_ok: "Optional[bool]" = None


def _host_reachable() -> bool:
    """Can we run commands on the host? Always yes natively; in the sandbox, only
    if flatpak-spawn works (the permission is present and the portal is up)."""
    global _host_ok
    if _host_ok is not None:
        return _host_ok
    if not _IN_FLATPAK:
        _host_ok = True
        return True
    try:
        r = subprocess.run(["flatpak-spawn", "--host", "true"],
                           capture_output=True, timeout=5)
        _host_ok = (r.returncode == 0)
    except Exception:
        _host_ok = False
    return _host_ok


def _host_blocked_verdict(title: str, headline: str) -> "Verdict":
    """A verdict for a check that needs host process access it can't get (sandbox with
    the flatpak-spawn permission turned off). Honest, with the fix in plain English."""
    return Verdict(
        title, UNKNOWN, headline,
        detail="Host access (flatpak-spawn / org.freedesktop.Flatpak) is unavailable.",
        what_it_is=(
            "This check needs to look at the other programs running on your Deck, which "
            "lives outside deckcheck's sandbox. That access is currently switched off."),
        steps=[
            "Open Flatseal (install it from Discover if you don't have it).",
            "Select deckcheck in the list on the left.",
            "Under “Session Bus > Talk”, make sure org.freedesktop.Flatpak is on.",
            "Close and reopen deckcheck, then run the health check again.",
            "Prefer the terminal? The command-line tools in scripts/ do the same checks with no sandbox.",
        ])


def _needs_host_msg(manual_cmd: str) -> str:
    """The honest fallback when an action can't reach the host: tell the user how to
    enable one-click fixes (Flatseal) AND give the exact command they can run now."""
    return (
        "deckcheck can't reach your system to do this automatically, the permission "
        "that lets it run system commands has been turned off.\n\n"
        "To enable one-click fixes: open Flatseal, select deckcheck, and turn on "
        "“Session Bus > Talk > org.freedesktop.Flatpak”.\n\n"
        f"Or run this in a terminal (Konsole) to do the same thing now:\n    {manual_cmd}")


def _run(cmd: list[str], timeout: int = 8) -> str:
    """Run an external command, capturing stdout (host-aware)."""
    try:
        return subprocess.run(
            _wrap(cmd), capture_output=True, text=True, timeout=timeout
        ).stdout
    except Exception:
        return ""


def _run_full(cmd: list[str], timeout: int = 20):
    """Run an external command, returning the full CompletedProcess (host-aware).

    Used by the action helpers that need return codes / stderr. Centralises the
    flatpak-spawn wrapping so every system call goes through one place.
    """
    return subprocess.run(_wrap(cmd), capture_output=True, text=True, timeout=timeout)


def _read_int(path: str):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _read_str(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _hwmon_by_label(want_name: str, want_label: str, kind: str):
    """Read a hwmon input by chip name + sensor label, e.g. ('amdgpu','edge','temp')
    or ('steamdeck_hwmon','System Fan','fan'). Returns the raw int, or None.

    /sys is mounted read-only into the Flatpak sandbox by default, so this works
    both natively and packaged with no filesystem permission, which is why the
    temperature/fan checks read sysfs directly instead of shelling out to `sensors`.
    """
    for chip in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read_str(f"{chip}/name") != want_name:
            continue
        for inp in glob.glob(f"{chip}/{kind}*_input"):
            label_path = inp[: -len("_input")] + "_label"
            if _read_str(label_path) == want_label:
                return _read_int(inp)
    return None


def _open_app(desktop_or_cmd: str) -> "tuple[bool, str]":
    """Launch a desktop app for the user (e.g. the software store, system settings).

    Launching another desktop app from a sandbox legitimately runs on the host (there
    is no XDG portal for "open this app by id"), so this goes through flatpak-spawn,
    the same single permission everything else uses. Natively it runs directly.
    """
    if not _host_reachable():
        return False, _needs_host_msg(f"kde-open {desktop_or_cmd}")
    for launcher in (["kde-open", desktop_or_cmd], ["xdg-open", desktop_or_cmd],
                     [desktop_or_cmd]):
        try:
            subprocess.Popen(_wrap(launcher), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True, "Opened, look for the window that just appeared."
        except Exception:
            continue
    return False, "Couldn't open it automatically."


# --- safe, reversible actions the GUI can offer ---------------------------------

def _balootool() -> "Optional[str]":
    """Return whichever baloo control binary exists (KDE 6 first), or None."""
    for tool in ("balooctl6", "balooctl"):
        if _run_full(["which", tool]).returncode == 0:
            return tool
    return None


# Folders that are pure churn for an indexer on a Deck, games, caches, app sandboxes.
# Excluding these is the *gentle* fix: it stops the thrashing but keeps file search
# working for documents etc. (This is the real fix from deckcheck's origin story.)
_BALOO_EXCLUDE = [
    os.path.expanduser("~/.local/share/Steam"),
    os.path.expanduser("~/.cache"),
    os.path.expanduser("~/.var"),          # Flatpak app data
    os.path.expanduser("~/.local/share/flatpak"),
]


def _action_baloo_exclude_games() -> "tuple[bool, str]":
    """Gentle fix: stop baloo indexing games/caches but keep desktop search working."""
    if not _host_reachable():
        return False, _needs_host_msg(
            "balooctl6 config add excludeFolders ~/.local/share/Steam ~/.cache && balooctl6 purge")
    tool = _balootool()
    if not tool:
        return False, "The file indexer doesn't seem to be installed."
    added = 0
    for folder in _BALOO_EXCLUDE:
        if os.path.isdir(folder):
            r = _run_full([tool, "config", "add", "excludeFolders", folder])
            if r.returncode == 0:
                added += 1
    # Apply the new rules by rebuilding the (now much smaller) index.
    _run_full([tool, "purge"])
    if added:
        return True, ("Done. The file indexer will now skip your games and caches (the "
                      "usual cause of it working hard) while still letting you search "
                      "your documents. Your Deck should run cooler and quieter at idle.")
    return False, "Couldn't update the indexer's settings."


def _action_disable_baloo() -> "tuple[bool, str]":
    """Stronger fix: turn the KDE file indexer off entirely. Reversible."""
    if not _host_reachable():
        return False, _needs_host_msg("balooctl6 disable")
    tool = _balootool()
    if not tool:
        return False, "The file indexer doesn't seem to be installed."
    if _run_full([tool, "disable"]).returncode == 0:
        return True, ("File indexer turned off. Your Deck won't index files in the "
                      "background anymore. You can turn it back on any time using the "
                      "“Undo” option on this check.")
    return False, "Couldn't turn off the file indexer."


def _action_restore_baloo() -> "tuple[bool, str]":
    """Undo: re-enable the indexer AND clear any exclusions we added, back to default.

    This is the safety net: if turning baloo off (or excluding folders) made the user
    feel something 'broke', this one button puts everything back the way KDE ships it.
    """
    if not _host_reachable():
        return False, _needs_host_msg("balooctl6 enable")
    tool = _balootool()
    if not tool:
        return False, "The file indexer doesn't seem to be installed."
    # Re-enable (harmless if already enabled).
    _run_full([tool, "enable"])
    # Remove the exclusions we may have added.
    for folder in _BALOO_EXCLUDE:
        _run_full([tool, "config", "remove", "excludeFolders", folder])
    return True, ("File search is fully back on and set to its normal defaults. It may "
                  "be busy for a little while as it rebuilds. That's expected, and it "
                  "will settle down on its own.")


def _action_open_discover() -> "tuple[bool, str]":
    return _open_app("org.kde.discover.desktop")


def _action_open_disk_usage() -> "tuple[bool, str]":
    # Filelight is KDE's visual disk-usage tool; fall back to the file manager.
    for app in ("org.kde.filelight.desktop", "org.kde.dolphin.desktop"):
        ok, msg = _open_app(app)
        if ok:
            return ok, msg
    return False, "Couldn't open a disk-usage tool."


# Outcome messages for closing a process, shared by the native and sandboxed paths
# so the wording stays in one place.
def _kill_outcomes(name: str) -> dict:
    return {
        "sent": (True, f"Asked “{name}” to close. If it was something you didn't "
                       "start, it should stay closed; apps you use will reopen normally."),
        "gone": (True, f"“{name}” had already closed."),
        "denied": (False, f"Couldn't close “{name}”, it may belong to the system. "
                          "Safer to leave it unless you're sure."),
    }


def _kill_pid_action(pid: str, name: str) -> Action:
    def _run():
        msg = _kill_outcomes(name)
        # Inside the sandbox the target PID lives in the host's PID namespace, so a
        # direct os.kill can't see it, route the signal through the host via the same
        # flatpak-spawn path everything else uses. Natively, os.kill is used directly.
        if _IN_FLATPAK:
            if not _host_reachable():
                return False, _needs_host_msg(f"kill {pid}")
            r = _run_full(["kill", "-15", pid])
            if r.returncode == 0:
                return msg["sent"]
            err = (r.stderr or "").lower()
            if "no such process" in err:
                return msg["gone"]
            if "not permitted" in err or "permission" in err:
                return msg["denied"]
            return False, f"Couldn't close it: {r.stderr.strip() or 'unknown error'}"
        try:
            os.kill(int(pid), 15)  # SIGTERM, polite, the program can clean up
            return msg["sent"]
        except ProcessLookupError:
            return msg["gone"]
        except PermissionError:
            return msg["denied"]
        except Exception as e:
            return False, f"Couldn't close it: {e}"
    return Action(
        label=f"Close “{name}”",
        confirm=f"This will ask the program “{name}” to close.",
        undo_note="Nothing is deleted, if it's an app you use, just open it again.",
        run=_run,
    )


# --- the checks ----------------------------------------------------------------

def _read_temperature() -> "tuple[Optional[float], Optional[int]]":
    """Read APU edge temperature (°C) and fan RPM from sysfs. Returns (None, _) if no
    temperature sensor is found. /sys is a read-only sandbox mount, so no host call or
    `sensors` binary is needed; fall back across chip names for non-Deck Linux."""
    raw = (_hwmon_by_label("amdgpu", "edge", "temp")
           or _hwmon_by_label("k10temp", "Tctl", "temp")
           or _hwmon_by_label("acpitz", "", "temp"))
    fan_rpm = _hwmon_by_label("steamdeck_hwmon", "System Fan", "fan")
    return (raw / 1000.0 if raw is not None else None), fan_rpm


def _judge_temperature(temp: "Optional[float]", fan_rpm: "Optional[int]") -> Verdict:
    """Pure: turn a temperature (°C) + optional fan RPM into a Verdict."""
    if temp is None:
        return Verdict("Temperature", UNKNOWN,
                       "Couldn't read the temperature sensor.",
                       advice="No readable hwmon temperature sensor was found under /sys.")
    detail = f"APU edge: {temp:.0f}°C" + (f", fan: {fan_rpm} RPM" if fan_rpm is not None else "")

    if temp < 60:
        return Verdict("Temperature", OK,
                       f"Your Deck is running cool ({temp:.0f}°C). Nothing to worry about.",
                       detail)
    what = ("This is the temperature of your Deck's main chip. It's normal for it to be "
            "warm, even up to the 80s°C while playing a demanding game. It's only worth "
            "a look if it's hot while you're *not* doing anything, which points to a "
            "background program keeping it busy.")
    steps = [
        "If you're playing a game right now, this is completely normal and no action is needed.",
        "If the Deck is just sitting idle, look at the “CPU activity” and “Heaviest writer” checks above.",
        "Whichever program those name is the cause, follow the advice on that check to deal with it.",
        "Over the long term, if idle temperatures slowly creep up over months, it can mean dust buildup (a question for a repair guide).",
    ]
    status = WARN if temp < 75 else PROBLEM
    return Verdict("Temperature", status,
                   (f"A little warm ({temp:.0f}°C). Fine under load; worth a look if it's idle."
                    if status == WARN else
                    f"Running hot ({temp:.0f}°C). Something is working the chip hard."),
                   detail,
                   advice="If the Deck is idle, the CPU/writer checks point to the cause.",
                   what_it_is=what, steps=steps)


def check_temperature() -> Verdict:
    """Is the chip running hot? Healthy Deck idle is ~48-55°C with the fan often off."""
    temp, fan_rpm = _read_temperature()
    return _judge_temperature(temp, fan_rpm)


def _cpu_idle_pct() -> float:
    """Sample /proc/stat over a short window and return the idle %."""
    def snap():
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(int, parts))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle

    t0, i0 = snap()
    time.sleep(1.0)
    t1, i1 = snap()
    dt, di = (t1 - t0), (i1 - i0)
    return (100.0 * di / dt) if dt else 0.0


def _busiest_process() -> "tuple[str, str]":
    """Return (pid, name) of the busiest process, or ('?', '?') if we can't see it.

    The system-wide idle % reads fine inside the sandbox, but naming a process needs
    host process visibility, unavailable if the flatpak-spawn permission is revoked.
    """
    if _IN_FLATPAK and not _host_reachable():
        return "?", "?"
    top = _run(["ps", "-eo", "pid,comm,%cpu", "--sort=-%cpu"]).splitlines()
    if len(top) > 1:
        f = top[1].split()
        return f[0], f[1]
    return "?", "?"


def _judge_cpu(idle_pct: float, top_pid: str, top_proc: str) -> Verdict:
    """Pure: turn an idle % and the busiest (pid, name) into a Verdict.
    top_proc == '?' means we couldn't see host processes (sandbox access off)."""
    detail = f"CPU idle: {idle_pct:.0f}%" + (
        f"  |  busiest process: {top_proc} (pid {top_pid})" if top_proc != "?" else "")

    if idle_pct >= 80:
        return Verdict("CPU activity", OK,
                       f"The processor is mostly idle ({idle_pct:.0f}% free). All good.",
                       detail)

    status = WARN if idle_pct >= 50 else PROBLEM
    used = 100 - idle_pct
    named = top_proc != "?"

    if named:
        what = (f"“{top_proc}” is the program using your processor most right now. "
                "If it's a game or app you opened, that's expected. If your Deck is "
                "supposed to be sitting idle, a program working hard in the background "
                "is what makes it warm and drains the battery.")
        steps = [
            "Think about whether you recognise this program or recently opened it.",
            "If it's something you're using (a game, browser, etc.), this is normal and no action is needed.",
            "If you don't recognise it and the Deck should be idle, you can close it with the button below.",
            "Closing it is safe: nothing is deleted, and anything you actually use will just reopen when you need it.",
        ]
        action = _kill_pid_action(top_pid, top_proc) if top_pid.isdigit() else None
        return Verdict("CPU activity", status,
                       f"The processor is {'fairly' if status==WARN else 'very'} busy "
                       f"({used:.0f}% in use), led by “{top_proc}”.",
                       detail,
                       advice=f"“{top_proc}” is the main user of your processor.",
                       what_it_is=what, steps=steps, action=action)

    # Busy, but we couldn't name the culprit (host process access is off). Report the
    # load honestly and point at how to get the program name back.
    return Verdict("CPU activity", status,
                   f"The processor is {'fairly' if status==WARN else 'very'} busy "
                   f"({used:.0f}% in use).",
                   detail,
                   advice="Turn on system access (see steps) to see which program is responsible.",
                   what_it_is=(
                       "Something is using your processor, but deckcheck can't see which "
                       "program, that needs access to the other apps running on your Deck, "
                       "which is currently switched off."),
                   steps=[
                       "Open Flatseal, select deckcheck, and turn on Session Bus > Talk > org.freedesktop.Flatpak.",
                       "Reopen deckcheck and run the check again to see the program's name.",
                       "Or use the command-line tools in scripts/ (e.g. ./snapshot.sh), which need no sandbox.",
                   ])


def check_cpu_idle() -> Verdict:
    """How busy is the CPU, and what's the top process? Healthy idle is ~90%+ idle."""
    return _judge_cpu(_cpu_idle_pct(), *_busiest_process())


def _read_disk_write_rate() -> "Optional[int]":
    """Sample nvme0n1 sectors-written over 2s; return the rate in KB/s, or None."""
    def writes():
        try:
            with open("/proc/diskstats") as f:
                for line in f:
                    p = line.split()
                    if len(p) > 9 and p[2] == "nvme0n1":
                        return int(p[9])  # sectors written
        except Exception:
            return None
        return None

    a = writes()
    time.sleep(2.0)
    b = writes()
    if a is None or b is None:
        return None
    return (b - a) // 2 // 2  # sectors->KB over 2s


def _judge_disk_activity(kbs: "Optional[int]") -> Verdict:
    """Pure: turn a write-rate (KB/s, or None) into a Verdict."""
    if kbs is None:
        return Verdict("Disk activity", UNKNOWN, "Couldn't read disk statistics.")
    detail = f"Writing ~{kbs} KB/s right now"

    if kbs < 200:
        return Verdict("Disk activity", OK,
                       "The disk is quiet. Almost no writing is happening.", detail)

    what = ("This measures how much your Deck is writing to its storage right now. "
            "A lot of writing while you're not doing anything usually means a "
            "background program is busy, which adds heat and, over time, wears the "
            "storage chip.")
    steps = [
        "If you're installing or updating a game, or downloading something, this is normal.",
        "If the Deck is idle, look at the “Heaviest writer” check below; it names the program responsible.",
        "Follow the advice on that check to deal with the program causing it.",
    ]
    status = WARN if kbs < 2000 else PROBLEM
    return Verdict("Disk activity", status,
                   (f"Some disk writing ({kbs} KB/s), normal if you're doing something."
                    if status == WARN else
                    f"Heavy disk writing ({kbs} KB/s) while you'd expect it to be quiet."),
                   detail,
                   advice="The “Heaviest writer” check below names the likely program.",
                   what_it_is=what, steps=steps)


def check_disk_activity() -> Verdict:
    """Is the disk being written to right now? Idle should be near-zero."""
    return _judge_disk_activity(_read_disk_write_rate())


def _home_space():
    """Return (free_gb, used_pct) for the user's home storage, host-aware.

    Natively we statvfs("~") directly. Inside the sandbox the home dir isn't mounted
    (deckcheck ships no --filesystem permission), so we ask the host via the one
    flatpak-spawn read instead of widening the sandbox, `df` in POSIX (-P) mode
    gives a stable, parseable layout in 1K blocks.
    """
    if _IN_FLATPAK:
        out = _run(["df", "-P", "-k", os.path.expanduser("~")])
        lines = out.strip().splitlines()
        if len(lines) < 2:
            return None
        f = lines[-1].split()
        if len(f) < 5:
            return None
        try:
            avail_k = int(f[3])
            used_pct = float(f[4].rstrip("%"))
        except ValueError:
            return None
        return avail_k / 1e6, used_pct  # 1K blocks -> GB
    try:
        st = os.statvfs(os.path.expanduser("~"))
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return free / 1e9, 100.0 * (total - free) / total
    except Exception:
        return None


def _judge_storage(space: "Optional[tuple]") -> Verdict:
    """Pure: turn (free_gb, used_pct), or None, into a Verdict."""
    if space is None:
        return Verdict("Storage space", UNKNOWN, "Couldn't read disk space.")
    free_gb, used_pct = space
    detail = f"{free_gb:.0f} GB free, {used_pct:.0f}% used (home storage)"

    if used_pct < 85:
        return Verdict("Storage space", OK,
                       f"Plenty of room, {free_gb:.0f} GB free.", detail)

    what = ("This is how full your Deck's main storage is. When it gets nearly full, "
            "games and system updates can fail, and the whole Deck can feel slower.")
    steps = [
        "The button below opens a visual map of what's using your space (big games show as big blocks).",
        "Uninstall games you've finished from your Library; that frees the most space fastest.",
        "If you have a microSD card, you can move games onto it from Settings > Storage.",
    ]
    action = Action(
        label="Show me what's using space",
        confirm="This opens a disk-usage viewer so you can see what's taking up room.",
        undo_note="It only shows information, it doesn't delete anything.",
        run=_action_open_disk_usage,
    )
    status = WARN if used_pct < 95 else PROBLEM
    return Verdict("Storage space", status,
                   (f"Getting full ({used_pct:.0f}% used, {free_gb:.0f} GB free)."
                    if status == WARN else
                    f"Nearly full ({used_pct:.0f}% used, only {free_gb:.0f} GB free)."),
                   detail,
                   advice="Free some space, a full drive causes slowdowns and failed updates.",
                   what_it_is=what, steps=steps, action=action)


def check_storage_space() -> Verdict:
    """How full is the drive? Getting full causes slowdowns and failed updates."""
    return _judge_storage(_home_space())


def _read_battery() -> "tuple[Optional[int], Optional[int], Optional[int], bool]":
    """Read battery (full, design, cycles, found) from sysfs. `found` is False when no
    battery directory exists; full/design may be None even when a battery is present."""
    bat = next(iter(glob.glob("/sys/class/power_supply/BAT*")), None)
    if not bat:
        return None, None, None, False
    # Prefer charge_full*, fall back to energy_full*, either ratio gives wear.
    full = _read_int(f"{bat}/charge_full") or _read_int(f"{bat}/energy_full")
    design = _read_int(f"{bat}/charge_full_design") or _read_int(f"{bat}/energy_full_design")
    cycles = _read_int(f"{bat}/cycle_count")
    return full, design, cycles, True


def _judge_battery(full, design, cycles, found) -> Verdict:
    """Pure: turn battery capacity figures into a 4-tier health Verdict."""
    if not found:
        return Verdict("Battery health", UNKNOWN, "Couldn't find a battery to check.")
    if not full or not design:
        return Verdict("Battery health", UNKNOWN, "Couldn't read battery capacity figures.")

    # Health = how much of the original capacity remains. A fresh/recalibrating battery
    # can read slightly above design (>100%); that's not wear, so we cap the display.
    health = 100.0 * full / design
    health_shown = min(health, 100.0)
    wear = max(0.0, 100.0 - health)

    cyc_txt = (f"{cycles} charge cycles" if cycles and cycles > 0
               else "charge-cycle count not reported by this Deck")
    detail = (f"Capacity now {health:.1f}% of original "
              f"({full/1000:.0f} vs {design/1000:.0f} design units); {cyc_txt}")

    what = (
        "Every rechargeable battery slowly loses capacity as it ages, this shows how "
        "much of your Deck's original battery life remains. A few years of normal use "
        "might bring it down to the 80s%, which is expected and not a fault. It only "
        "matters if it drops a lot, which would mean noticeably shorter playtime.")
    steps = [
        "There's nothing to “fix” here, battery wear is normal ageing, not a bug.",
        "To slow it down: avoid leaving the Deck at 100% charge for days, and avoid letting it sit fully empty.",
        "Storing it around half-charged in a cool place is kindest if you won't use it for a while.",
        "If health is very low and playtime is poor, a battery replacement is the only real remedy.",
    ]

    if health_shown >= 90:
        return Verdict("Battery health", OK,
                       f"Your battery is in great shape, about {health_shown:.0f}% of its original capacity.",
                       detail)
    if health_shown >= 80:
        return Verdict("Battery health", OK,
                       f"Your battery is healthy, about {health_shown:.0f}% of original capacity "
                       f"({wear:.0f}% wear, normal for some use).",
                       detail)
    if health_shown >= 65:
        return Verdict("Battery health", WARN,
                       f"Your battery has aged a bit, about {health_shown:.0f}% of original capacity "
                       f"({wear:.0f}% lost).",
                       detail,
                       advice="Normal ageing. Nothing to fix, but expect somewhat shorter playtime.",
                       what_it_is=what, steps=steps)
    return Verdict("Battery health", PROBLEM,
                   f"Your battery has worn down, about {health_shown:.0f}% of original capacity "
                   f"({wear:.0f}% lost).",
                   detail,
                   advice="Significant wear. Playtime will be noticeably shorter; a replacement helps.",
                   what_it_is=what, steps=steps)


def check_battery() -> Verdict:
    """Battery health, how much capacity it's lost to age/wear. The #1 thing handheld
    owners worry about, and there's no clean built-in for it on the Deck."""
    return _judge_battery(*_read_battery())


def _scan_top_writer() -> "tuple[Optional[str], int, str]":
    """Find the process with the most lifetime disk writes -> (pid, MB, name).

    Inside a Flatpak the sandbox has its own PID namespace, so a direct /proc scan only
    sees our own processes. When sandboxed we run the scan on the host via flatpak-spawn
    (a tiny shell one-liner over the host's real /proc) and parse the result.
    """
    if _IN_FLATPAK:
        # Host-side: for each pid, print "<write_bytes> <pid> <comm>" and pick the max.
        script = (
            'for p in /proc/[0-9]*; do '
            ' w=$(awk "/^write_bytes:/{print \\$2}" "$p/io" 2>/dev/null); '
            ' [ -n "$w" ] && echo "$w ${p##*/} $(cat "$p/comm" 2>/dev/null)"; '
            'done | sort -rn | head -1'
        )
        out = _run(["sh", "-c", script]).strip()
        if not out:
            return None, 0, ""
        parts = out.split(None, 2)
        if len(parts) < 2:
            return None, 0, ""
        wb = int(parts[0]); pid = parts[1]; name = parts[2] if len(parts) > 2 else "?"
        return pid, wb // 1048576, name

    # Native: read /proc directly.
    best_pid, best_mb, best_name = None, 0, ""
    for io in glob.glob("/proc/[0-9]*/io"):
        try:
            with open(io) as f:
                for line in f:
                    if line.startswith("write_bytes:"):
                        wb = int(line.split()[1])
                        if wb > best_mb * 1048576:
                            pid = io.split("/")[2]
                            try:
                                name = open(f"/proc/{pid}/comm").read().strip()
                            except Exception:
                                name = "?"
                            best_pid, best_mb, best_name = pid, wb // 1048576, name
                        break
        except Exception:
            continue
    return best_pid, best_mb, best_name


def _judge_top_writer(best_pid, best_mb, best_name, host_blocked=False) -> Verdict:
    """Pure: turn the heaviest writer (pid, MB, name) into a Verdict.
    host_blocked=True means we couldn't see host processes (sandbox access off)."""
    if host_blocked:
        return _host_blocked_verdict(
            "Heaviest writer",
            "Can't see your programs' disk activity, system access is turned off.")
    if best_pid is None:
        return Verdict("Heaviest writer", UNKNOWN, "Couldn't read per-process write data.")
    gb = best_mb / 1024
    detail = f"“{best_name}” has written {gb:.1f} GB since it started (pid {best_pid})"

    if best_mb < 20_000:
        return Verdict("Heaviest writer", OK,
                       f"The biggest writer (“{best_name}”) looks reasonable.", detail)

    guide = _writer_guidance(best_name, best_pid, gb)
    status = WARN if best_mb < 100_000 else PROBLEM
    headline = (f"“{best_name}” has written a lot ({gb:.0f} GB)."
                if status == WARN else
                f"“{best_name}” has written a huge amount ({gb:.0f} GB), which is unusually high.")
    return Verdict("Heaviest writer", status, headline, detail,
                   advice=guide["advice"],
                   what_it_is=guide["what_it_is"],
                   steps=guide["steps"],
                   action=guide["action"])


def check_top_writer() -> Verdict:
    """Which process has written the most since it started? Catches runaway writers."""
    if _IN_FLATPAK and not _host_reachable():
        return _judge_top_writer(None, 0, "", host_blocked=True)
    return _judge_top_writer(*_scan_top_writer())


# Tailored, plain-English guidance for the programs that commonly top the write list
# on a Steam Deck. The whole point: tell the user *what the thing is* and *what to do*,
# not just "investigate".
def _writer_guidance(name: str, pid: str, gb: float) -> dict:
    n = name.lower()

    if "discover" in n:
        return {
            "what_it_is": (
                "“Discover” is the Steam Deck's app store (for non-Steam apps, in desktop "
                "mode). It checks for and downloads app updates in the background. Writing "
                "this much usually means it's been left running and repeatedly updating its "
                "list of available software, harmless, but wasteful."),
            "advice": "Open Discover, let any updates finish, then close it fully.",
            "steps": [
                "Press the button below to open Discover.",
                "If it shows updates, click “Update All” and let them finish.",
                "Then close Discover completely (don't just minimise it).",
                "If it keeps doing this, you can simply leave Discover closed, your games "
                "update through Steam separately and aren't affected.",
            ],
            "action": Action(
                label="Open Discover",
                confirm="This opens the Discover app store so you can finish its updates.",
                undo_note="It just opens an app, nothing is changed or deleted.",
                run=_action_open_discover,
            ),
        }

    if "baloo" in n:
        return {
            "what_it_is": (
                "This is KDE's file-search indexer. It reads your files to make desktop "
                "search instant. On a Deck it often has little to gain and can write a lot."),
            "advice": "The “File indexer” check below has the full set of options for this.",
            "steps": [
                "This is the same file indexer covered by the “File indexer (baloo)” check below.",
                "The recommended fix keeps search working but stops it indexing your games.",
                "See that check for the recommended fix, the full off-switch, and a one-click undo.",
            ],
            # Offer the recommended gentle fix right here too, for convenience.
            "action": _baloo_gentle_action(),
        }

    if any(k in n for k in ("steam", "proton", "wine", "pressure-vessel")):
        return {
            "what_it_is": (
                f"“{name}” is part of Steam or its game-compatibility system. Heavy writing "
                "here is usually normal: installing/updating games, building shader caches, "
                "or saving game data."),
            "advice": "Usually normal. Only a concern if it never stops while nothing's installing.",
            "steps": [
                "If you've recently installed or updated games, this is expected and no action is needed.",
                "If it writes constantly even when idle, fully quit and reopen Steam.",
                "A Deck restart clears any stuck Steam background task.",
            ],
            "action": None,
        }

    # Unknown program, give a safe, generic path plus the option to close it.
    return {
        "what_it_is": (
            f"“{name}” is the program that has written to storage the most. It might be "
            "something normal, but writing this much in the background isn't typical, and "
            "it adds wear to the storage over time."),
        "advice": f"If you don't recognise “{name}” and didn't start it, you can close it.",
        "steps": [
            f"Consider whether you recognise “{name}” or recently installed it.",
            "If it's part of something you use, leave it alone.",
            "If you don't recognise it, you can close it with the button below, nothing is deleted.",
            "If it comes straight back after closing, note its name and ask for help online with that name.",
        ],
        "action": _kill_pid_action(pid, name) if pid.isdigit() else None,
    }


# The three baloo actions, offered together: the gentle fix first (recommended), the
# full off-switch second, and an always-available undo so a nervous user is never stuck.
def _baloo_gentle_action() -> Action:
    return Action(
        label="Stop it indexing games (recommended)",
        confirm=("This keeps file search working, but tells the indexer to skip your "
                 "games and caches, the usual reason it works hard."),
        undo_note="Reversible with the “Put file search back to normal” button below.",
        run=_action_baloo_exclude_games,
    )


def _baloo_disable_action() -> Action:
    return Action(
        label="Turn file search off completely",
        confirm="This turns off KDE's background file indexer (baloo) entirely.",
        undo_note="Reversible with the “Put file search back to normal” button below.",
        run=_action_disable_baloo,
    )


def _baloo_restore_action() -> Action:
    return Action(
        label="Put file search back to normal (undo)",
        confirm=("This re-enables file search and clears any changes deckcheck made, "
                 "putting it back exactly the way your Deck shipped."),
        undo_note="This IS the undo. It restores the default behaviour; nothing is deleted.",
        run=_action_restore_baloo,
    )


def _baloo_status_text() -> "tuple[bool, str]":
    """Return (tool_exists, status_text). balooctl prints 'disabled' to stderr with a
    non-zero exit, so we must capture both streams to tell 'off' from 'not installed'."""
    tool = _balootool()
    if not tool:
        return False, ""
    r = _run_full([tool, "status"])
    return True, (r.stdout + r.stderr)


def _judge_baloo(exists: bool, out: str) -> Verdict:
    """Pure: turn balooctl status text (exists flag + combined stdout/stderr) into a
    Verdict. Parses indexer state and file count; the action buttons live in helpers."""
    if not exists:
        return Verdict("File indexer (baloo)", OK,
                       "KDE's file indexer isn't installed, so there's nothing to worry about.")

    what = (
        "This is KDE's file-search indexer. It reads through your files so the desktop "
        "search box can find them instantly. It's a convenience feature, and on a Steam "
        "Deck, one most people never actually use. When it indexes your games it can work "
        "hard for nothing, making the Deck warm and busy at idle.")

    if "disabled" in out.lower():
        # Off already, the only useful action here is the undo, in case someone turned
        # it off (maybe with this very tool) and now wants their file search back.
        return Verdict("File indexer (baloo)", OK,
                       "KDE's file indexer is turned off, so it won't cause background activity.",
                       detail="Indexer disabled.",
                       what_it_is=what,
                       steps=[
                           "This is fine, a disabled indexer just means desktop file-search is off.",
                           "If you actually want to search for files from the desktop and it isn't working, "
                           "the button below turns it back on.",
                       ],
                       action=_baloo_restore_action())

    state = re.search(r"Indexer state:\s*(.+)", out)
    files = re.search(r"Total files indexed:\s*([\d,]+)", out)
    state_s = state.group(1).strip() if state else "running"
    nfiles = files.group(1) if files else "?"
    detail = f"State: {state_s}, files indexed: {nfiles}"

    steps = [
        "Do you ever search for files from the desktop search box? Most Deck owners don't.",
        "Recommended: “Stop it indexing games” keeps search working but ends the wasteful part.",
        "Or turn file search off completely if you never use it, browsing files still works either way.",
        "Changed your mind? “Put file search back to normal” undoes everything in one click.",
    ]
    actions = [_baloo_gentle_action(), _baloo_disable_action(), _baloo_restore_action()]

    if "idle" in state_s.lower():
        return Verdict("File indexer (baloo)", OK,
                       "KDE's file indexer is on but idle. Fine for now.",
                       detail,
                       advice="It's quiet right now. If it makes your Deck warm at idle later, you have options.",
                       what_it_is=what, steps=steps, actions=actions)
    return Verdict("File indexer (baloo)", WARN,
                   f"KDE's file indexer is busy ({state_s}).",
                   detail,
                   advice="If your Deck feels warm at idle, this is a known cause, and easy to tame.",
                   what_it_is=what, steps=steps, actions=actions)


def check_baloo() -> Verdict:
    """KDE's file indexer, the original culprit. Flag it if it's churning."""
    return _judge_baloo(*_baloo_status_text())


ALL_CHECKS = [
    check_temperature,
    check_cpu_idle,
    check_disk_activity,
    check_storage_space,
    check_battery,
    check_top_writer,
    check_baloo,
]


def run_all() -> list[Verdict]:
    """Run every registered check and return their Verdicts. A check that raises is
    caught and reported as an UNKNOWN verdict, so one failure never sinks the rest."""
    results = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            results.append(Verdict(fn.__name__, UNKNOWN, f"Check failed: {e}"))
    return results


def overall(verdicts: list[Verdict]) -> str:
    """Roll a list of verdicts into a single worst-case status for the summary banner
    (PROBLEM > WARN > OK), returning UNKNOWN if nothing concrete was determined."""
    statuses = {v.status for v in verdicts}
    if PROBLEM in statuses:
        return PROBLEM
    if WARN in statuses:
        return WARN
    if all(s in (OK, UNKNOWN) for s in statuses) and OK in statuses:
        return OK
    return UNKNOWN


if __name__ == "__main__":
    # CLI fallback so the engine is usable without the GUI.
    vs = run_all()
    icons = {OK: "[OK]", WARN: "[!]", PROBLEM: "[X]", UNKNOWN: "[?]"}
    print(f"\nOverall: {overall(vs).upper()}\n")
    for v in vs:
        print(f"{icons.get(v.status,'[?]')} {v.title}: {v.headline}")
        if v.detail:
            print(f"      {v.detail}")
        if v.advice:
            print(f"      -> {v.advice}")
    print()
