#!/usr/bin/env python3
"""
Qt desktop updater for Debian 13 (trixie).

The app runs a conservative APT and Flatpak update workflow, writes a
timestamped log next to this file, and shows either a simple GUI progress view
or a live terminal-style output view.

Copyright (C) 2026 Debian Updater Developers

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    print("PySide6 is required to run the graphical updater.")
    print("Install it with: sudo apt install python3-pyside6.qtwidgets policykit-1 ksshaskpass")
    print("Or with pip in a virtual environment: pip install -r requirements.txt")
    raise SystemExit(1) from exc


SCRIPT_DIR = Path(__file__).resolve().parent


def _get_log_dir() -> Path:
    # If SCRIPT_DIR is writeable and not a standard system bin/sbin directory,
    # use SCRIPT_DIR / ".logs". Otherwise, write logs to the user's data directory.
    if os.access(SCRIPT_DIR, os.W_OK) and SCRIPT_DIR.name not in ("bin", "sbin"):
        return SCRIPT_DIR / ".logs"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        base_dir = Path(xdg_data)
    else:
        base_dir = Path.home() / ".local" / "share"
    return base_dir / "debian-updater" / "logs"


LOG_DIR = _get_log_dir()
LOG_TIME_FORMAT = "%Y-%m-%d-%H-%M"
APT_HELPER_ARG = "--apt-helper"


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    requires_root: bool = False
    optional: bool = False


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ProgressSnapshot:
    percent: int
    step_name: str
    elapsed: str
    eta: str


class FileLogger:
    def __init__(self) -> None:
        timestamp = dt.datetime.now().strftime(LOG_TIME_FORMAT)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"{timestamp}.log"
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, level: str, message: str) -> None:
        timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for line in message.splitlines() or [""]:
            self._handle.write(f"{timestamp} | {level.upper():7} | {line}\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class UpdateWorker(QObject):
    output = Signal(str)
    progress = Signal(object)
    log_path = Signal(str)
    finished = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self.started_at = time.monotonic()
        self.failures: list[str] = []

    @Slot()
    def run(self) -> None:
        logger = FileLogger()
        self.log_path.emit(str(logger.path))
        self._emit_output(logger, f"Writing log to {logger.path}")

        exit_code = 0
        summary = "Completed successfully."

        try:
            warning = trixie_warning()
            if warning:
                self._emit_output(logger, warning, "warning")

            steps = build_steps()
            if not steps:
                summary = "No steps to run."
                self._emit_output(logger, summary)
                self._emit_progress(0, 0, "idle")
            else:
                self._emit_progress(0, len(steps), "starting")
                for index, step in enumerate(steps, start=1):
                    self._emit_progress(index - 1, len(steps), step.name)
                    try:
                        self._run_step(step, logger)
                    except subprocess.CalledProcessError as exc:
                        message = f"{step.name} failed with exit code {exc.returncode}"
                        self._emit_output(logger, message, "error")
                        if step.optional:
                            self.failures.append(f"optional: {message}")
                            self._emit_progress(index, len(steps), step.name)
                            continue
                        exit_code = exc.returncode
                        summary = f"Stopped: {message}"
                        self._emit_progress(index, len(steps), step.name)
                        break
                    except RuntimeError as exc:
                        exit_code = 1
                        summary = f"Stopped: {step.name} could not start: {exc}"
                        self._emit_output(logger, summary, "error")
                        self._emit_progress(index, len(steps), step.name)
                        break
                    self._emit_progress(index, len(steps), step.name)

            if exit_code == 0 and self.failures:
                summary = "Completed with optional failures:\n" + "\n".join(
                    f"- {failure}" for failure in self.failures
                )
                self._emit_output(logger, summary, "warning")
            elif exit_code == 0:
                self._emit_output(logger, summary)

            elapsed = format_duration(time.monotonic() - self.started_at)
            self._emit_output(logger, f"Elapsed: {elapsed}")
            self._emit_output(logger, f"Log: {logger.path}")
        finally:
            logger.close()

        self.finished.emit(exit_code, summary)

    def _run_step(self, step: Step, logger: FileLogger) -> None:
        command = command_with_privilege(step.command, step.requires_root)
        self._emit_output(logger, f"Starting step: {step.name}")
        self._emit_output(logger, f"Command: {printable_command(command.argv)}")

        process = subprocess.Popen(
            command.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=command.env,
        )

        assert process.stdout is not None
        for line in process.stdout:
            self._emit_output(logger, line.rstrip())

        return_code = process.wait()
        self._emit_output(logger, f"Finished step: {step.name} with exit code {return_code}")
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command.argv)

    def _emit_output(self, logger: FileLogger, message: str, level: str = "info") -> None:
        logger.write(level, message)
        self.output.emit(message)

    def _emit_progress(self, done: int, total: int, step_name: str) -> None:
        percent = 100 if total == 0 else int((max(0, min(done, total)) / total) * 100)
        elapsed_seconds = time.monotonic() - self.started_at
        eta_seconds = estimate_eta(done, total, elapsed_seconds)
        self.progress.emit(
            ProgressSnapshot(
                percent=percent,
                step_name=step_name,
                elapsed=format_duration(elapsed_seconds),
                eta=format_duration(eta_seconds),
            )
        )


class UpdaterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: UpdateWorker | None = None
        self.showing_output = False

        self.setWindowTitle("Debian Updater")
        self.setMinimumSize(620, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel("Debian 13 System Update")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 4)
        title_font.setBold(True)
        title.setFont(title_font)

        self.status_label = QLabel("Ready")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_progress_view())
        self.stack.addWidget(self._build_output_view())

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start System Update")
        self.start_button.clicked.connect(self.start_update)

        self.switch_button = QPushButton("Show Terminal Output")
        self.switch_button.clicked.connect(self.toggle_view)

        self.exit_button = QPushButton("Exit")
        self.exit_button.clicked.connect(self.close)

        controls.addWidget(self.start_button)
        controls.addWidget(self.switch_button)
        controls.addStretch(1)
        controls.addWidget(self.exit_button)

        root.addWidget(title)
        root.addWidget(self.status_label)
        root.addWidget(self.stack, 1)
        root.addLayout(controls)

        self.setCentralWidget(central)

    def _build_progress_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        self.step_label = QLabel("Current step: idle")
        self.time_label = QLabel("Elapsed 00:00 | ETA 00:00")

        layout.addStretch(1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.step_label)
        layout.addWidget(self.time_label)
        layout.addStretch(1)
        return widget

    def _build_output_view(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        self.output_view.setFont(font)

        layout.addWidget(self.output_view)
        return widget

    @Slot()
    def start_update(self) -> None:
        self.start_button.setEnabled(False)
        self.output_view.clear()
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting update...")

        self.thread = QThread(self)
        self.worker = UpdateWorker()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.output.connect(self.append_output)
        self.worker.progress.connect(self.update_progress)
        self.worker.log_path.connect(self.show_log_path)
        self.worker.finished.connect(self.update_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._clear_thread)

        self.thread.start()

    @Slot()
    def toggle_view(self) -> None:
        self.showing_output = not self.showing_output
        self.stack.setCurrentIndex(1 if self.showing_output else 0)
        self.switch_button.setText("Show GUI Progress" if self.showing_output else "Show Terminal Output")

    @Slot(str)
    def append_output(self, text: str) -> None:
        self.output_view.appendPlainText(text)
        scrollbar = self.output_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @Slot(object)
    def update_progress(self, snapshot: ProgressSnapshot) -> None:
        self.progress_bar.setValue(snapshot.percent)
        self.step_label.setText(f"Current step: {snapshot.step_name}")
        self.time_label.setText(f"Elapsed {snapshot.elapsed} | ETA {snapshot.eta}")

    @Slot(str)
    def show_log_path(self, path: str) -> None:
        self.status_label.setText(f"Log file: {path}")

    @Slot(int, str)
    def update_finished(self, exit_code: int, summary: str) -> None:
        self.start_button.setEnabled(True)
        if exit_code == 0:
            self.status_label.setText(summary)
            self.progress_bar.setValue(100)
        else:
            self.status_label.setText(summary)
            QMessageBox.warning(self, "Update Failed", summary)

    @Slot()
    def _clear_thread(self) -> None:
        self.thread = None
        self.worker = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(self, "Update Running", "Wait for the update to finish before exiting.")
            event.ignore()
            return
        event.accept()


def build_steps() -> list[Step]:
    yes = ("-y",)
    flatpak_available = shutil.which("flatpak") is not None
    helper_command = (sys.executable, str(Path(__file__).resolve()), APT_HELPER_ARG)

    steps = [
        Step("APT update, upgrade, and cleanup", helper_command, requires_root=True),
    ]

    if flatpak_available:
        steps.append(Step("Flatpak update", ("flatpak", "update", *yes), optional=True))
        steps.append(
            Step("Flatpak remove unused runtimes", ("flatpak", "uninstall", "--unused", *yes), optional=True)
        )

    return steps


def build_apt_commands() -> list[tuple[str, tuple[str, ...]]]:
    return [
        ("APT package index update", ("apt-get", "update")),
        ("APT upgrade", ("apt-get", "upgrade", "-y")),
        ("APT autoremove unused packages", ("apt-get", "autoremove", "-y")),
        ("APT autoclean package cache", ("apt-get", "autoclean")),
    ]


def run_apt_helper() -> int:
    if os.geteuid() != 0:
        print("APT helper must run as root.", flush=True)
        return 1

    for name, command in build_apt_commands():
        print(f"Starting step: {name}", flush=True)
        print(f"Command: {printable_command(command)}", flush=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert process.stdout is not None
        for line in process.stdout:
            print(line.rstrip(), flush=True)

        return_code = process.wait()
        print(f"Finished step: {name} with exit code {return_code}", flush=True)
        if return_code != 0:
            return return_code

    return 0


def trixie_warning() -> str | None:
    os_release = read_os_release()
    name = os_release.get("ID", "").lower()
    version_id = os_release.get("VERSION_ID", "")
    codename = os_release.get("VERSION_CODENAME", "").lower()

    if name == "debian" and version_id == "13" and codename == "trixie":
        return None

    pretty = os_release.get("PRETTY_NAME") or platform.platform()
    return (
        "Warning: this app is intended for Debian 13 trixie, but detected "
        f"{pretty!r}. Continue only if that is expected."
    )


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def command_with_privilege(command: Sequence[str], requires_root: bool) -> CommandSpec:
    if not requires_root or os.geteuid() == 0:
        return CommandSpec(tuple(command))

    pkexec = shutil.which("pkexec")
    if pkexec is not None:
        return CommandSpec((pkexec, *command))

    sudo = shutil.which("sudo")
    askpass = find_askpass()
    if sudo is not None and askpass is not None:
        env = os.environ.copy()
        env["SUDO_ASKPASS"] = askpass
        return CommandSpec((sudo, "-A", *command), env)

    if sudo is not None:
        raise RuntimeError(
            "This app cannot use plain sudo because it has no terminal for a password prompt. "
            "Install pkexec/polkit for graphical authentication, or install ksshaskpass for sudo -A."
        )

    raise RuntimeError(
        "This command requires root privileges, but neither pkexec nor sudo was found. "
        "Install polkit/pkexec for graphical authentication."
    )


def find_askpass() -> str | None:
    configured = os.environ.get("SUDO_ASKPASS")
    if configured and Path(configured).exists():
        return configured

    for candidate in (
        "ksshaskpass",
        "ssh-askpass",
        "x11-ssh-askpass",
        "gnome-ssh-askpass",
    ):
        path = shutil.which(candidate)
        if path is not None:
            return path

    for path in (
        "/usr/bin/ksshaskpass",
        "/usr/lib/ssh/ssh-askpass",
        "/usr/lib/openssh/gnome-ssh-askpass",
    ):
        if Path(path).exists():
            return path

    return None


def printable_command(command: Iterable[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in command)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def estimate_eta(done: int, total: int, elapsed: float) -> float:
    if done <= 0 or done >= total:
        return 0.0
    return (elapsed / done) * (total - done)


def main() -> int:
    if APT_HELPER_ARG in sys.argv:
        return run_apt_helper()

    app = QApplication(sys.argv)
    app.setApplicationName("Debian Updater")
    app.setDesktopFileName("debian-updater")

    window = UpdaterWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
