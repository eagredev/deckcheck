"""Unit tests for deckcheck's verdict logic.

These test the *judging* layer, the pure functions that turn a reading into a
Verdict, not the system-reading layer (which would just test mocks) or the GUI
(verified by eye, per the project's testing philosophy; see CONTRIBUTING.md).

The refactor that made this possible: every check is split into a thin reader
(_read_*/_scan_*) and a pure judge (_judge_*). We feed the judges synthetic
readings and assert the status + that the headline mentions the right thing.

Run with:  python -m pytest tests/        (or just: pytest)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gui"))

import checks  # noqa: E402
from checks import OK, WARN, PROBLEM, UNKNOWN  # noqa: E402


# --- temperature: boundaries at 60 (OK/WARN) and 75 (WARN/PROBLEM) -------------

@pytest.mark.parametrize("temp,expected", [
    (45.0, OK),
    (59.9, OK),
    (60.0, WARN),     # boundary: 60 is the first non-OK
    (74.9, WARN),
    (75.0, PROBLEM),  # boundary: 75 flips to PROBLEM
    (88.0, PROBLEM),
])
def test_temperature_thresholds(temp, expected):
    assert checks._judge_temperature(temp, None).status == expected


def test_temperature_no_sensor_is_unknown():
    v = checks._judge_temperature(None, None)
    assert v.status == UNKNOWN


def test_temperature_detail_includes_fan_when_present():
    assert "RPM" in checks._judge_temperature(50.0, 2400).detail
    assert "RPM" not in checks._judge_temperature(50.0, None).detail


def test_temperature_warm_gives_guidance():
    v = checks._judge_temperature(70.0, None)
    assert v.what_it_is and v.steps  # a warning must tell the user what to do


# --- cpu: idle thresholds 80 (OK) and 50 (WARN/PROBLEM) ------------------------

@pytest.mark.parametrize("idle,expected", [
    (95.0, OK),
    (80.0, OK),       # boundary: >=80 is OK
    (79.9, WARN),
    (50.0, WARN),     # boundary: >=50 is WARN
    (49.9, PROBLEM),
    (5.0, PROBLEM),
])
def test_cpu_thresholds(idle, expected):
    assert checks._judge_cpu(idle, "123", "ksysguard").status == expected


def test_cpu_busy_names_process_and_offers_kill():
    v = checks._judge_cpu(20.0, "4242", "runaway")
    assert "runaway" in v.headline
    assert v.action is not None              # a numeric pid -> a Close button
    assert v.action.label == "Close “runaway”"


def test_cpu_busy_unknown_process_degrades_gracefully():
    # top_proc '?' = host process access is off; still report the load honestly,
    # don't fabricate a name, and don't offer a kill button for an unknown pid.
    v = checks._judge_cpu(20.0, "?", "?")
    assert v.status == PROBLEM
    assert "?" not in v.headline
    assert v.action is None
    assert any("Flatseal" in s for s in v.steps)  # explains how to re-enable


def test_cpu_idle_has_no_action():
    assert checks._judge_cpu(95.0, "1", "init").action is None


# --- disk activity: 200 KB/s (OK) and 2000 (WARN/PROBLEM) ----------------------

@pytest.mark.parametrize("kbs,expected", [
    (0, OK),
    (199, OK),
    (200, WARN),      # boundary
    (1999, WARN),
    (2000, PROBLEM),  # boundary
    (50000, PROBLEM),
])
def test_disk_thresholds(kbs, expected):
    assert checks._judge_disk_activity(kbs).status == expected


def test_disk_unreadable_is_unknown():
    assert checks._judge_disk_activity(None).status == UNKNOWN


# --- storage: 85% (OK/WARN) and 95% (WARN/PROBLEM) -----------------------------

@pytest.mark.parametrize("used_pct,expected", [
    (10.0, OK),
    (84.9, OK),
    (85.0, WARN),     # boundary
    (94.9, WARN),
    (95.0, PROBLEM),  # boundary
    (99.0, PROBLEM),
])
def test_storage_thresholds(used_pct, expected):
    # _home_space returns (free_gb, used_pct); free_gb only affects wording.
    assert checks._judge_storage((100.0, used_pct)).status == expected


def test_storage_unreadable_is_unknown():
    assert checks._judge_storage(None).status == UNKNOWN


def test_storage_full_offers_disk_usage_viewer():
    v = checks._judge_storage((2.0, 96.0))
    assert v.action is not None
    assert "space" in v.action.label.lower()


# --- battery: the 4-tier ladder at 90 / 80 / 65 --------------------------------

@pytest.mark.parametrize("health_pct,expected", [
    (100.0, OK),      # great shape
    (90.0, OK),       # boundary: >=90 OK ("great")
    (89.9, OK),       # 80-90 still OK ("healthy")
    (80.0, OK),       # boundary: >=80 OK
    (79.9, WARN),
    (65.0, WARN),     # boundary: >=65 WARN
    (64.9, PROBLEM),
    (40.0, PROBLEM),
])
def test_battery_ladder(health_pct, expected):
    # full/design ratio = health %. Use design=1000 so full=health*10.
    design = 1000
    full = int(health_pct * 10)
    assert checks._judge_battery(full, design, 250, True).status == expected


def test_battery_over_100_is_capped_not_wear():
    # A fresh/recalibrating battery can read slightly above design; that's not wear.
    v = checks._judge_battery(1010, 1000, 0, True)
    assert v.status == OK
    assert "101" not in v.headline  # display capped at 100%


def test_battery_missing_is_unknown():
    assert checks._judge_battery(None, None, None, False).status == UNKNOWN
    assert checks._judge_battery(None, 1000, 5, True).status == UNKNOWN  # present but unreadable


def test_battery_cycle_count_in_detail_when_present():
    assert "300 charge cycles" in checks._judge_battery(900, 1000, 300, True).detail
    assert "not reported" in checks._judge_battery(900, 1000, 0, True).detail


# --- heaviest writer: 20 GB (OK) and 100 GB (WARN/PROBLEM) ---------------------

@pytest.mark.parametrize("mb,expected", [
    (5_000, OK),
    (19_999, OK),
    (20_000, WARN),    # boundary (~20 GB)
    (99_999, WARN),
    (100_000, PROBLEM),  # boundary (~100 GB)
    (500_000, PROBLEM),
])
def test_top_writer_thresholds(mb, expected):
    assert checks._judge_top_writer("999", mb, "someproc").status == expected


def test_top_writer_host_blocked_is_graceful():
    v = checks._judge_top_writer(None, 0, "", host_blocked=True)
    assert v.status == UNKNOWN
    assert any("Flatseal" in s for s in v.steps)


def test_top_writer_unreadable_is_unknown():
    assert checks._judge_top_writer(None, 0, "").status == UNKNOWN


# --- writer guidance: tailored advice per known program ------------------------

@pytest.mark.parametrize("name,needle", [
    ("plasma-discover", "Discover"),
    ("baloo_file", "indexer"),
    ("steam", "Steam"),
    ("proton", "Steam"),
    ("totally-unknown-thing", "written to storage"),  # generic fallback
])
def test_writer_guidance_recognises_programs(name, needle):
    g = checks._writer_guidance(name, "123", 50.0)
    assert needle.lower() in (g["what_it_is"] + g["advice"]).lower()


def test_writer_guidance_steam_offers_no_kill_button():
    # We explain busy Steam/Proton but deliberately don't offer to force-close it.
    assert checks._writer_guidance("steam", "5", 80.0)["action"] is None


# --- baloo: parse balooctl status text -----------------------------------------

def test_baloo_not_installed_is_ok():
    v = checks._judge_baloo(False, "")
    assert v.status == OK
    assert "isn't installed" in v.headline


def test_baloo_disabled_offers_only_restore():
    v = checks._judge_baloo(True, "Baloo is currently disabled")
    assert v.status == OK
    assert v.action is not None        # the restore/undo button
    assert not v.actions               # and only that one


def test_baloo_idle_is_ok_with_full_options():
    v = checks._judge_baloo(True, "Indexer state: Idle\nTotal files indexed: 12,345")
    assert v.status == OK
    assert len(v.actions) == 3         # gentle / disable / restore
    assert "12,345" in v.detail


def test_baloo_busy_is_warn():
    v = checks._judge_baloo(True, "Indexer state: Indexing file content\nTotal files indexed: 860,000")
    assert v.status == WARN
    assert len(v.actions) == 3


# --- overall(): worst-case roll-up ---------------------------------------------

def _v(status):
    return checks.Verdict("t", status, "h")


def test_overall_worst_case_wins():
    assert checks.overall([_v(OK), _v(WARN), _v(PROBLEM)]) == PROBLEM
    assert checks.overall([_v(OK), _v(WARN)]) == WARN
    assert checks.overall([_v(OK), _v(OK)]) == OK


def test_overall_unknown_does_not_mask_ok():
    # All-OK-or-UNKNOWN with at least one OK reads as OK, not UNKNOWN.
    assert checks.overall([_v(OK), _v(UNKNOWN)]) == OK


def test_overall_all_unknown_is_unknown():
    assert checks.overall([_v(UNKNOWN), _v(UNKNOWN)]) == UNKNOWN


# --- _hwmon_by_label: sysfs parsing via a fake /sys tree -----------------------

def test_hwmon_by_label_reads_matching_chip(tmp_path, monkeypatch):
    # Build a fake hwmon tree: hwmon0=amdgpu with temp1 labelled 'edge' = 54000.
    chip = tmp_path / "hwmon0"
    chip.mkdir()
    (chip / "name").write_text("amdgpu\n")
    (chip / "temp1_input").write_text("54000\n")
    (chip / "temp1_label").write_text("edge\n")

    real_glob = checks.glob.glob

    # Redirect only the top-level /sys/class/hwmon scan into our fake tree; the
    # per-chip input glob runs for real against the (real) tmp_path directory.
    def fake_glob(pat):
        if pat == "/sys/class/hwmon/hwmon*":
            return [str(chip)]
        return real_glob(pat)

    monkeypatch.setattr(checks.glob, "glob", fake_glob)
    assert checks._hwmon_by_label("amdgpu", "edge", "temp") == 54000
    assert checks._hwmon_by_label("amdgpu", "nonexistent", "temp") is None
    assert checks._hwmon_by_label("othername", "edge", "temp") is None


# --- run_all robustness: one failing check doesn't sink the rest ---------------

def test_run_all_isolates_failures(monkeypatch):
    def boom():
        raise RuntimeError("sensor exploded")

    monkeypatch.setattr(checks, "ALL_CHECKS", [boom, lambda: checks.Verdict("ok-one", OK, "fine")])
    results = checks.run_all()
    assert len(results) == 2
    assert results[0].status == UNKNOWN        # the crash became an UNKNOWN verdict
    assert results[1].status == OK             # the healthy check still ran
