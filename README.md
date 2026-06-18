# Debian Updater

A simple Qt desktop updater for Debian 13 trixie (Stable) and KDE Plasma 6. The app runs
a conservative APT and Flatpak update workflow, writes a timestamped log file
under `.logs/`, and lets you switch between a graphical progress view and a
terminal-style output view.

## Features

- KDE Plasma friendly Qt 6 interface
- `Start System Update` button for the update workflow
- `Exit` button for closing the app when no update is running
- switch button for `GUI Progress` and `Terminal Output` views
- live progress percentage, current step, elapsed time, and ETA
- live terminal-style output inside the app
- automatic log file under `.logs/` using `yyyy-mm-dd-hh-mm.log`
- conservative APT upgrade path using `apt-get upgrade`
- one graphical authentication prompt for the complete APT workflow
- automatic Flatpak update and unused runtime cleanup when Flatpak is installed
- graphical privilege elevation through `pkexec`, with `sudo -A` askpass fallback

## Requirements / Dependencies

Debian 13 trixie packages:

```bash
sudo apt install python3 python3-pyside6.qtwidgets pkexec ksshaskpass
```

Optional, for Flatpak updates:

```bash
sudo apt install flatpak
```

Python virtual environment option:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The Python package requirement is listed in [requirements.txt](requirements.txt):

```text
PySide6>=6.7
```

## Files

- [debian_updater.py](debian_updater.py): the Qt desktop updater
- [debian-updater.desktop](debian-updater.desktop): KDE/desktop launcher file
- [requirements.txt](requirements.txt): Python package requirement for virtual environments
- `.logs/yyyy-mm-dd-hh-mm.log`: generated automatically when an update starts

## Running The App

From this directory:

```bash
./debian_updater.py
```

You can also run it through Python:

```bash
python3 debian_updater.py
```

The app opens a small desktop window with:

- `Start System Update`
- `Show Terminal Output` / `Show GUI Progress`
- `Exit`

Press `Start System Update` to begin. The button is disabled while the update is
running so the workflow cannot be started twice at the same time.

## Building and Installing as a Debian Package (.deb)

You can package this application into a native Debian `.deb` package to install it system-wide with all dependencies handled.

### Method 1: Using the Convenience Script (Fast & Simple)

This method does not require installing any package development tools. Run the helper script:

```bash
./build_deb.sh
```

This generates `debian-updater_1.0_all.deb` in the project root.

### Method 2: Standard Debian Packaging Tools

If you have or want to install the canonical Debian build helper tool suite (`debhelper` and `dh-python`), install them and build the package:

```bash
sudo apt install debhelper dh-python
dpkg-buildpackage -us -uc -b
```

This builds the package and leaves the `.deb` file in the parent directory of the repository.

### Installing the Package

Install the generated `.deb` package using `apt` (which will automatically resolve and install all Python and system dependencies like `python3-pyside6.qtwidgets`, `pkexec`, and `ksshaskpass`):

```bash
sudo apt install ./debian-updater_1.0_all.deb
```

Once installed:
- The app is available system-wide as `debian-updater`.
- The desktop launcher is registered automatically.
- Logs are written to the user-writable directory `~/.local/share/debian-updater/logs/` instead of `/usr/bin/`.

To uninstall the package:

```bash
sudo apt purge debian-updater
```

## KDE Plasma Launcher

The repo includes [debian-updater.desktop](debian-updater.desktop). It is already
pointed at this workspace path:

```text
Exec=/home/gnu/Git/debian-updater/debian_updater.py
```

To make it appear in the KDE Plasma application launcher, copy it to your local
applications directory:

```bash
cp debian-updater.desktop ~/.local/share/applications/
```

If you move this repo, update the `Exec=` path in the desktop file.

## Update Workflow

When `Start System Update` is pressed, the app runs these steps:

1. start one privileged APT helper process
2. run `apt-get update`
3. run `apt-get upgrade -y`
4. run `apt-get autoremove -y`
5. run `apt-get autoclean`
6. run `flatpak update -y`, only when Flatpak is installed
7. run `flatpak uninstall --unused -y`, only when Flatpak is installed

APT steps are required. Flatpak steps are optional: a Flatpak failure is shown
and logged, but it does not make the whole run fail after APT has succeeded.

## Privilege Handling

APT commands require root privileges. The app uses this order:

1. run the command directly when the app is already running as root
2. use `pkexec` when available
3. fall back to `sudo -A` when a graphical askpass helper is available

On KDE Plasma 6, `pkexec` should trigger the normal graphical authentication
dialog through the desktop's PolicyKit agent. If `pkexec` is not installed,
`ksshaskpass` provides a KDE-friendly password dialog for `sudo -A`.

The app elevates one internal APT helper process instead of elevating each
`apt-get` command separately. That means the complete APT workflow should need
one authentication prompt, not one prompt per command.

The app intentionally does not use plain `sudo` from the GUI. Plain `sudo`
expects a terminal password prompt, which causes this error in desktop apps:

```text
sudo: a terminal is required to read the password
```

Flatpak commands run without privilege elevation because user Flatpak
installations are common.

## Progress View

The default view shows:

- progress percentage
- current update step
- exact elapsed time in `minutes:seconds`
- estimated time remaining in `minutes:seconds`

The ETA is based on the average duration of completed steps. Package upgrades
can vary a lot, so the estimate may shift while the app runs.

## Terminal Output View

Press `Show Terminal Output` to switch from the progress bar to the live
terminal-style output. This view shows the same command output that is written to
the log file.

Press `Show GUI Progress` to return to the progress bar.

## Logging

Every update run creates or appends a log file under `.logs/`.

Filename format:

```text
yyyy-mm-dd-hh-mm.log
```

Example:

```text
.logs/2026-06-19-01-09.log
```

The log includes:

- log file path
- Debian version warning when the host does not look like Debian 13 trixie
- every command that is executed
- APT and Flatpak command output
- step completion status and exit codes
- elapsed time
- failure messages

## Safety Notes

- The app uses `apt-get upgrade`, not `dist-upgrade` or `full-upgrade`.
- Cleanup is limited to `apt-get autoremove`, `apt-get autoclean`, and unused
  Flatpak runtimes.
- The app prevents closing while an update is running.
- If Flatpak is not installed, Flatpak steps are skipped automatically.
- If the host does not look like Debian 13 trixie, the app logs and displays a
  warning in the terminal output view.

## Development Check

Verify Python syntax without opening the GUI:

```bash
python3 -m py_compile debian_updater.py
```

Check whether the Qt dependency is available:

```bash
python3 -c "from PySide6.QtWidgets import QApplication; print('PySide6 OK')"
```

Check whether graphical privilege helpers are available:

```bash
command -v pkexec || command -v ksshaskpass
```
