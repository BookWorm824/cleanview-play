"""CleanView Play in File Explorer's "Open with" list (1.0.9; the owner, 2026-11-14: "could it
also be like vlc where it plays many types of files?", then yes to "right-click a movie in File
Explorer → Open with → CleanView Play").

For this Windows user only (HKEY_CURRENT_USER\\Software\\Classes): a "CleanViewPlay.Media" type
whose open command is launcher.pyw with the file, and that type added to each video and music
kind's OpenWithProgids. It never makes CleanView Play the default for anything: what opens a
file on a double-click stays as it was. The launcher hands the file to the window
(launcher.pyw: OPEN_THESE), which plays it as it is (CleanView.pyw: watch_files).

Done each time the window opens (the updater can't run the installer), only from an installed
copy (never from a test or the source folder), and only when something has changed."""
import os
import sys
from pathlib import Path

import cvplay

HERE = Path(__file__).resolve().parent
APP_NAME = "CleanView Play"
PROGID = "CleanViewPlay.Media"
KINDS = cvplay.VIDEO_TYPES + cvplay.AUDIO_TYPES


def installed_root(here=HERE):
    """The install folder (with app\\ and venv\\) if this is an installed copy, else None."""
    root = here.parent
    if here.name.lower() != "app" or not (root / "venv" / "Scripts" / "pythonw.exe").exists():
        return None
    return root


def command(root):
    """What runs for "Open with": the app's own exe if it has one (so the list says
    "CleanView Play"), else the venv's pythonw, with launcher.pyw and the file."""
    own = root / "venv" / APP_NAME / f"{APP_NAME}.exe"
    exe = own if own.exists() and not (own.parent / "failed.txt").exists() \
        else root / "venv" / "Scripts" / "pythonw.exe"
    return f'"{exe}" "{root / "app" / "launcher.pyw"}" "%1"'


def icon_file(app):
    """The icon "Open with" shows: a copy of cleanview.ico named by its contents
    (openwith-<mark>.ico). Windows keeps a picture of an icon by its file's name, so a new
    cleanview.ico (CleanView 1.18.107's slight 3D look) needs a new name to be shown. Older
    copies are removed. Falls back to cleanview.ico itself."""
    import hashlib
    import shutil
    icon = app / "cleanview.ico"
    try:
        mine = app / f"openwith-{hashlib.sha1(icon.read_bytes()).hexdigest()[:12]}.ico"
        if not mine.exists():
            shutil.copy2(icon, mine)
        for old in app.glob("openwith-*.ico"):
            if old != mine:
                old.unlink(missing_ok=True)
        return mine
    except OSError:
        return icon


def register(here=HERE, reg=None):
    """Add CleanView Play to "Open with" for the video and music kinds. True if anything was
    written. Quiet: a problem here never stops the app."""
    if os.name != "nt" and reg is None:
        return False
    root = installed_root(here)
    if root is None:
        return False
    try:
        if reg is None:
            import winreg as reg
        cmd = command(root)
        icon = str(icon_file(root / "app"))
        base = r"Software\Classes"
        want = {(PROGID, ""): f"{APP_NAME} video",
                (PROGID, "FriendlyTypeName"): f"{APP_NAME} video",
                (PROGID + r"\DefaultIcon", ""): icon,
                (PROGID + r"\shell\open", "FriendlyAppName"): APP_NAME,
                (PROGID + r"\shell\open\command", ""): cmd}
        changed = False
        for (key, name), value in want.items():
            if _get(reg, base + "\\" + key, name) != value:
                _set(reg, base + "\\" + key, name, value)
                changed = True
        for kind in KINDS:
            key = base + "\\" + kind + r"\OpenWithProgids"
            if _get(reg, key, PROGID) is None:
                _set(reg, key, PROGID, "")
                changed = True
        if changed and reg.__name__ == "winreg":
            import ctypes          # tell File Explorer, so it shows up without a restart
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        return changed
    except Exception:
        return False


def _get(reg, key, name):
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key) as k:
            return reg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _set(reg, key, name, value):
    with reg.CreateKey(reg.HKEY_CURRENT_USER, key) as k:
        reg.SetValueEx(k, name, 0, reg.REG_SZ, value)


if __name__ == "__main__":
    print("registered" if register() else "nothing to do (or not an installed copy)",
          file=sys.stderr)
