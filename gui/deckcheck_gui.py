#!/usr/bin/env python3
"""
deckcheck, a friendly health check for your Steam Deck.

A native Qt window that runs deckcheck's health checks and explains the results in
plain English, colour-coded so anyone can read them at a glance, no terminal needed.

Run via the launcher script (run_gui.sh) which uses the bundled venv's PySide6.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import checks  # noqa: E402  (same dir; launcher sets cwd/path)

# Colour-blind-friendly status palette (not red/green alone, paired with icons + text).
COLORS = {
    checks.OK:      ("#2e7d32", "#e8f5e9", "✓"),
    checks.WARN:    ("#e65100", "#fff3e0", "!"),
    checks.PROBLEM: ("#c62828", "#ffebee", "✕"),
    checks.UNKNOWN: ("#546e7a", "#eceff1", "?"),
}
def _darken(hex_color: str, factor: float = 0.82) -> str:
    """Return a slightly darker shade of a #rrggbb colour, for hover states."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(
        int(r * factor), int(g * factor), int(b * factor))


OVERALL_TEXT = {
    checks.OK:      "Your Deck looks healthy",
    checks.WARN:    "A couple of things worth a look",
    checks.PROBLEM: "Something needs attention",
    checks.UNKNOWN: "Couldn't complete all checks",
}


class CheckWorker(QThread):
    """Runs the checks off the UI thread so the window stays responsive."""
    done = Signal(list)

    def run(self):
        self.done.emit(checks.run_all())


class CheckCard(QFrame):
    """One check result: headline, an expandable 'What can I do?' help section
    (explanation + plain steps + an optional safe one-click fix), and raw details."""

    INDENT = "margin-left: 40px;"

    def __init__(self, verdict: checks.Verdict):
        super().__init__()
        self.verdict = verdict
        self.fg, bg, icon = COLORS.get(verdict.status, COLORS[checks.UNKNOWN])
        # Scope the background to THIS card by object name. An unscoped `QFrame{...}`
        # rule cascades into every child QFrame, including pop-up dialogs, which is
        # what hid the confirmation-dialog text. `#deckcheckCard` keeps it contained.
        self.setObjectName("deckcheckCard")
        self.setStyleSheet(
            f"QFrame#deckcheckCard {{ background: {bg}; border-radius: 10px; }}")
        # Fill the available cell width (so two-column cards use the landscape width
        # instead of collapsing to their text's minimum); height tracks content.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        outer.addLayout(self._build_header(verdict, icon))
        # The "What can I do?" help section only appears when there's guidance to give.
        if verdict.what_it_is or verdict.steps or verdict.action:
            self._build_help(verdict, outer)
        if verdict.detail:
            self._build_details(verdict, outer)

    def _build_header(self, verdict, icon) -> QHBoxLayout:
        """Status badge + title + plain-English headline."""
        row = QHBoxLayout()
        badge = QLabel(icon)
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background: {self.fg}; color: white; border-radius: 14px;"
            f"font-weight: bold; font-size: 16px;"
        )
        row.addWidget(badge, 0, Qt.AlignTop)

        text = QVBoxLayout()
        title = QLabel(verdict.title)
        title.setStyleSheet(f"color: {self.fg}; font-weight: bold; font-size: 13px;")
        headline = QLabel(verdict.headline)
        headline.setWordWrap(True)
        headline.setStyleSheet("color: #222; font-size: 14px;")
        text.addWidget(title)
        text.addWidget(headline)
        row.addLayout(text, 1)
        return row

    def _build_help(self, verdict, outer):
        """The collapsible 'What can I do?' section: explanation, steps, fix buttons.
        This is the part that turns 'investigate' into real guidance."""
        self.help_btn = QPushButton("What can I do?  ▸")
        self.help_btn.setCheckable(True)
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {self.fg}; color: {self.fg};"
            " border-radius: 8px; padding: 6px 10px; font-size: 12px; font-weight: bold;"
            " text-align: left; }"
            f"QPushButton:checked {{ background: {self.fg}; color: white; }}"
        )
        outer.addLayout(self._indented(self.help_btn))

        self.help_box = QWidget()
        hb = QVBoxLayout(self.help_box)
        hb.setContentsMargins(40, 4, 0, 4)
        hb.setSpacing(8)

        if verdict.what_it_is:
            what = QLabel(verdict.what_it_is)
            what.setWordWrap(True)
            what.setStyleSheet("color: #37474f; font-size: 12px;")
            hb.addWidget(what)

        if verdict.steps:
            steps_lbl = QLabel("\n".join(
                f"{i}.  {s}" for i, s in enumerate(verdict.steps, 1)))
            steps_lbl.setWordWrap(True)
            steps_lbl.setStyleSheet(
                "color: #263238; font-size: 12px; background: rgba(255,255,255,0.6);"
                " border-radius: 6px; padding: 8px;")
            hb.addWidget(steps_lbl)

        for act_btn in self._action_buttons(verdict.all_actions()):
            hb.addWidget(act_btn)

        self.help_box.hide()
        outer.addWidget(self.help_box)
        self.help_btn.toggled.connect(self._toggle_help)

    def _action_buttons(self, actions) -> list:
        """Build a button per offered action. The last of several is conventionally an
        'undo' (e.g. 'Put file search back to normal'), styled quieter so the
        recommended fix reads as primary and the undo as a reassuring fallback."""
        buttons = []
        for idx, action in enumerate(actions):
            is_undo = (len(actions) > 1 and idx == len(actions) - 1)
            act_btn = QPushButton(action.label)
            act_btn.setCursor(Qt.PointingHandCursor)
            if is_undo:
                act_btn.setStyleSheet(
                    f"QPushButton {{ background: white; color: {self.fg};"
                    f" border: 1px solid {self.fg}; border-radius: 8px; padding: 9px;"
                    " font-size: 12px; }"
                    "QPushButton:hover { background: #f5f5f5; }")
            else:
                act_btn.setStyleSheet(
                    f"QPushButton {{ background: {self.fg}; color: white; border: none;"
                    " border-radius: 8px; padding: 10px; font-size: 13px; font-weight: bold; }"
                    f"QPushButton:hover {{ background: {_darken(self.fg)}; }}")
            act_btn.clicked.connect(lambda _checked=False, a=action: self._do_action(a))
            buttons.append(act_btn)
        return buttons

    def _build_details(self, verdict, outer):
        """Raw numbers behind a small 'technical details' toggle, for the curious."""
        self._detail = QLabel(verdict.detail)
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color: #607d8b; font-size: 11px; {self.INDENT}")
        self._detail.hide()
        dtoggle = QPushButton("technical details")
        dtoggle.setCheckable(True)
        dtoggle.setCursor(Qt.PointingHandCursor)
        dtoggle.setStyleSheet(
            "QPushButton { border: none; color: #607d8b; font-size: 11px; text-align: left; }"
            " QPushButton:checked { color: #222; }")
        dtoggle.toggled.connect(self._detail.setVisible)
        outer.addLayout(self._indented(dtoggle))
        outer.addWidget(self._detail)

    @staticmethod
    def _indented(widget, px: int = 40):
        """Wrap a widget in a layout with a left indent, Qt stylesheets don't reliably
        support margin-left on bordered buttons, so we indent via layout instead."""
        row = QHBoxLayout()
        row.setContentsMargins(px, 0, 0, 0)
        row.addWidget(widget)
        return row

    def _toggle_help(self, on: bool):
        self.help_box.setVisible(on)
        self.help_btn.setText("What can I do?  ▾" if on else "What can I do?  ▸")

    def _make_dialog(self, icon, title, text, info):
        """Build a message box whose text is readable under ANY system theme.

        The app forces a light window background, but on a dark Plasma theme the system
        text colour is near-white, so dialog text came out white-on-white (invisible).
        We pin both the background and a dark text colour on the dialog explicitly, so it
        never depends on the user's theme palette.
        """
        box = QMessageBox(self.window())
        box.setStyleSheet(
            "QMessageBox { background: #fafafa; }"
            " QMessageBox QLabel { color: #1a1a1a; background: transparent; }")
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        if info:
            box.setInformativeText(info)
        return box

    def _do_action(self, a):
        # Always confirm first, in plain English, with the undo note visible.
        box = self._make_dialog(QMessageBox.Question, "Just checking...",
                                 a.confirm, f"{a.undo_note}\n\nGo ahead?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        ok, msg = a.run()
        done = self._make_dialog(
            QMessageBox.Information if ok else QMessageBox.Warning,
            "Done" if ok else "Couldn't do that",
            msg,
            "Run the health check again to see the updated result." if ok else "")
        done.exec()


class DeckCheckWindow(QWidget):
    """The main window: a run button, an overall verdict banner, and a scrollable
    column of CheckCards populated each time the health check is run."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("deckcheck: Steam Deck health")
        icon_path = os.path.join(os.path.dirname(__file__), "deckcheck.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1180, 760)
        self.setStyleSheet("background: #fafafa;")

        # A single content column that uses the full (landscape) window width, with
        # comfortable page margins. The two-column card grid below fills this width.
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        heading = QLabel("deckcheck")
        heading.setStyleSheet("font-size: 24px; font-weight: bold; color: #263238;")
        sub = QLabel("A quick, friendly health check for your Steam Deck.")
        sub.setStyleSheet("color: #607d8b; font-size: 13px;")
        root.addWidget(heading)
        root.addWidget(sub)

        # Header strip: the overall verdict banner stretches across, with the run
        # button as a fixed-width control beside it, a compact dashboard header that
        # uses the landscape width without stretching either element awkwardly.
        header = QHBoxLayout()
        header.setSpacing(14)

        self.banner = QLabel("Press “Run health check” to begin.")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background: #eceff1; color: #455a64; border-radius: 12px;"
            "padding: 18px; font-size: 16px; font-weight: bold;"
        )
        header.addWidget(self.banner, 1)

        self.run_btn = QPushButton("Run health check")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setMinimumWidth(240)
        self.run_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border: none;"
            " border-radius: 10px; padding: 14px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1976d2; }"
            "QPushButton:disabled { background: #90a4ae; }"
        )
        self.run_btn.clicked.connect(self.run_checks)
        header.addWidget(self.run_btn, 0)

        root.addLayout(header)

        # Scrollable results area. Cards lay out in two columns so the landscape
        # window width is used (and all checks fit without scrolling on a typical run).
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.results_host = QWidget()
        self.results_grid = QGridLayout(self.results_host)
        self.results_grid.setSpacing(10)
        self.results_grid.setContentsMargins(0, 0, 0, 0)
        self.results_grid.setColumnStretch(0, 1)
        self.results_grid.setColumnStretch(1, 1)
        # Keep cards anchored to the top, not vertically centred/stretched.
        self.results_grid.setRowStretch(99, 1)
        self.scroll.setWidget(self.results_host)
        root.addWidget(self.scroll, 1)

        self.worker = None

    def run_checks(self):
        """Kick off the checks on a worker thread, keeping the UI responsive."""
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Checking...")
        self.banner.setText("Checking your Deck...")
        self.banner.setStyleSheet(
            "background: #e3f2fd; color: #1565c0; border-radius: 12px;"
            "padding: 18px; font-size: 16px; font-weight: bold;"
        )
        self._clear_results()
        self.worker = CheckWorker()
        self.worker.done.connect(self._show_results)
        self.worker.start()

    def _clear_results(self):
        while self.results_grid.count():
            item = self.results_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_results(self, verdicts):
        # Fill the two columns top-to-bottom, left then right, row by row. Cards align
        # to the top of their cell so a short card next to a tall one doesn't stretch.
        for i, v in enumerate(verdicts):
            row, col = divmod(i, 2)
            self.results_grid.addWidget(CheckCard(v), row, col, Qt.AlignTop)

        status = checks.overall(verdicts)
        fg, bg, icon = COLORS.get(status, COLORS[checks.UNKNOWN])
        self.banner.setText(f"{icon}  {OVERALL_TEXT.get(status, '')}")
        self.banner.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 12px;"
            "padding: 18px; font-size: 16px; font-weight: bold;"
        )
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run health check again")


def _apply_light_palette(app):
    """Pin a consistent light palette so deckcheck looks identical under any KDE theme.

    The app is designed light (light cards, dark text). Without this, a user on a dark
    Plasma theme keeps the theme's light text colour, which then collides with our light
    backgrounds, e.g. the dialog text that came out white-on-white. Setting a full
    palette (not just a background) makes the look theme-independent.
    """
    from PySide6.QtGui import QColor, QPalette
    pal = QPalette()
    text = QColor("#1a1a1a")
    base = QColor("#ffffff")
    window = QColor("#fafafa")
    pal.setColor(QPalette.Window, window)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, window)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.ToolTipBase, base)
    pal.setColor(QPalette.ToolTipText, text)
    app.setPalette(pal)


def main():
    """Entry point: set up the app (icon, theme-independent palette) and show the window."""
    app = QApplication(sys.argv)
    app.setApplicationName("deckcheck")
    app.setDesktopFileName("deckcheck")  # lets the WM match our installed .desktop/icon
    icon_path = os.path.join(os.path.dirname(__file__), "deckcheck.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    _apply_light_palette(app)
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    win = DeckCheckWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
