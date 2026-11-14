"""CleanView Play: play the original video with the mutes and skips applied, no copy saved.

The owner, 2026-11-13: "this will be for the play only clean view no saving", then "keeping
clean view as a save program only add a play now feature for now" (in CleanView Play). Nothing
here writes a video: `play_list` works out the same mutes, cuts and quieter music that
CleanView's `render()` would put in a clean copy, and `play()` hands them to the mpv player
(`cvplay.lua`), which applies them while the untouched original plays.

- Mutes and quieter music: ffmpeg filters inside mpv (`af`), the very filters `render()` uses
  (`engine._mute_filters`, `engine._quieter`), timed by the sound itself, so they're as exact
  as in a saved copy.
- Cuts: the picture goes black and the sound silent across each cut (`vf`/`af`, the same
  timing), and the player jumps past it (`skips`). Dragging the seek bar into a cut jumps past
  it too.
- A long muted stretch is skipped instead of left silent, as `render()` cuts it (1.18.60).

Times count from 0 = the start of the file, the clock both ffmpeg's filters and mpv use
(mpv rebases a .ts file's own start time to 0 too)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import cleanview as engine

HERE = Path(__file__).resolve().parent
APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "CleanView Play"
SCRIPT = HERE / "cvplay.lua"
PLAYER_DIR = HERE / "bin" / "mpv"
LIST_ENV = "CVPLAY_LIST"            # where the player script finds the list (cvplay.lua)

# mpv for Windows: the builds mpv's own site points to. The newest is looked up; if GitHub
# won't say, this one is used.
PLAYER_RELEASES = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
PLAYER_FALLBACK = ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261111/"
                   "mpv-x86_64-20261111-git-bcde6b93cc.7z")

BLACK = "drawbox=color=black:t=fill"


class PlayError(RuntimeError):
    """Something that keeps it from playing, said plainly enough to show as it is."""


class Moved(PlayError, FileNotFoundError):
    """The video isn't where it was when it was checked."""


def play_list(st, cuts=None, lines=None, words=None, quiet=None):
    """What to do while this video plays: {"skips": [[s, e]...], "af": ..., "vf": ...,
    "mutes": n, "long": n}. Same choices and defaults as `engine.render`."""
    dur = st.get("duration") or 0
    if cuts is None:
        cuts = [c for c in st.get("scenes", []) if c.get("cut")]
    if lines is None:
        lines = [l for l in st.get("lines", []) if l.get("mute")]
    if words is None:
        words = st.get("mutes", [])
    if quiet is None:
        quiet = [q for q in st.get("quiet", []) if q.get("lower", True)]
    end = dur if dur > 0 else float("inf")
    cut_rs = engine._merge((max(0.0, c["s"]), min(end, c["e"])) for c in cuts if c["e"] > c["s"])
    line_rs = engine._merge((l["s"], l["e"]) for l in lines)
    mute_rs = engine._merge([(m["s"], m["e"]) for m in words] + line_rs)
    long_rs = [(max(0.0, s), min(end, e)) for s, e in engine.long_mutes(mute_rs)
               if not any(a <= s and e <= b for a, b in cut_rs)]
    if long_rs:                     # skipped instead of a long silence, as render() cuts it
        mute_rs = [r for r in mute_rs if not any(a <= r[0] and r[1] <= b for a, b in long_rs)]
        cut_rs = engine._merge(list(cut_rs) + long_rs)
    quiet_rs = engine._merge((max(0.0, q["s"]), min(end, q["e"])) for q in quiet if q["e"] > q["s"])
    af = [f"volume=volume='{engine._quieter(c)}':eval=frame" for c in engine._chunks(quiet_rs)]
    # silent across the cuts too, so nothing of them is heard before the jump past them
    af += engine._mute_filters(engine._merge(mute_rs + cut_rs), 0.0)
    vf = [f"{BLACK}:enable='{engine._ranges(c, 0.0)}'" for c in engine._chunks(cut_rs)]
    return {"skips": [[round(s, 3), round(e, 3)] for s, e in cut_rs],
            "af": _lavfi(af), "vf": _lavfi(vf),
            "mutes": len(mute_rs), "long": len(long_rs),
            "cut_seconds": round(sum(e - s for s, e in cut_rs), 1)}


def _lavfi(filters):
    """mpv's way of giving it ffmpeg filters (empty: none)."""
    return f"lavfi=[{','.join(filters)}]" if filters else ""


def entry(st, cuts=None, lines=None, words=None, quiet=None):
    """One video's part of the list the player reads."""
    e = play_list(st, cuts, lines, words, quiet)
    audio = (st.get("info") or {}).get("audio") or {}
    e.update({"path": str(st["path"]), "title": Path(st["path"]).stem,
              "audio_index": audio.get("index"), "duration": st.get("duration") or 0})
    return e


def write_list(entries, folder=None):
    """The list for the player, in the app's own folder. Only times and file names."""
    folder = Path(folder or APP_DATA / "play")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "now.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"made": time.time(), "videos": entries}, indent=1), encoding="utf8")
    os.replace(tmp, path)
    return path


# ---------- the player ----------
def player():
    """mpv.exe if it's here, else None."""
    exe = PLAYER_DIR / ("mpv.exe" if os.name == "nt" else "mpv")
    if exe.exists():
        return exe
    found = shutil.which("mpv") if os.name != "nt" else None
    return Path(found) if found else None


def _player_url():
    try:
        req = urllib.request.Request(PLAYER_RELEASES, headers={"User-Agent": "CleanView Play"})
        with urllib.request.urlopen(req, timeout=15) as r:
            assets = json.load(r).get("assets", [])
        for a in assets:            # the build for every 64-bit PC (not the newer-only "v3")
            n = a.get("name", "")
            if n.startswith("mpv-x86_64-2") and n.endswith(".7z"):
                return a["browser_download_url"]
    except Exception:
        pass
    return PLAYER_FALLBACK


def get_player(progress=None):
    """Download mpv once (about 35 MB) into bin\\mpv. Unpacked with Windows' own tar.exe,
    which reads .7z files. Returns mpv.exe's path."""
    say = progress or (lambda f, m: None)
    have = player()
    if have:
        return have
    if os.name != "nt":
        raise PlayError("The video player (mpv) isn't installed.")
    url = _player_url()
    down = APP_DATA / "downloads"
    down.mkdir(parents=True, exist_ok=True)
    arc = down / "mpv.7z"
    say(0.0, "Getting the video player (about 35 MB, only this once)…")
    req = urllib.request.Request(url, headers={"User-Agent": "CleanView Play"})
    with urllib.request.urlopen(req, timeout=60) as r, open(arc, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            got += len(b)
            if total:
                say(min(0.95, got / total), "Getting the video player (about 35 MB, only this once)…")
    tmp = Path(tempfile.mkdtemp(prefix="mpv-", dir=down))
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    res = subprocess.run([str(tar), "-xf", str(arc), "-C", str(tmp)], capture_output=True,
                         text=True, creationflags=0x08000000)
    exe = next(tmp.rglob("mpv.exe"), None)
    if res.returncode or exe is None:
        shutil.rmtree(tmp, ignore_errors=True)
        raise PlayError("Couldn't unpack the video player. " + (res.stderr or "")[-300:])
    PLAYER_DIR.parent.mkdir(parents=True, exist_ok=True)
    if PLAYER_DIR.exists():
        shutil.rmtree(PLAYER_DIR, ignore_errors=True)
    shutil.move(str(exe.parent), str(PLAYER_DIR))
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        arc.unlink()
    except OSError:
        pass
    say(1.0, "The video player is ready.")
    return PLAYER_DIR / "mpv.exe"


_playing = []            # the player started last, closed when Play now is pressed again


def command(exe, list_path, paths):
    return [str(exe), "--no-config", f"--script={SCRIPT}",
            "--force-window=yes", "--keep-open=no", "--idle=no", "--osc=yes",
            "--hwdec=auto-copy",            # the graphics card decodes; filters still work
            "--sid=no",                     # no captions (as clean copies have none showing)
            "--ytdl=no", "--save-position-on-quit=no", "--autofit=85%",
            "--title=${media-title} - CleanView Play", "--"] + [str(p) for p in paths]


def play(entries, exe=None):
    """Play these videos one after another with their mutes and skips. Returns the process."""
    exe = exe or player()
    if exe is None:
        raise PlayError("The video player isn't here yet.")
    missing = [e["path"] for e in entries if not Path(e["path"]).exists()]
    if missing:
        raise Moved(f"Can't find {Path(missing[0]).name}. Was it moved or renamed? Check it "
                    "again from where it is now.")
    list_path = write_list(entries)
    stop()
    env = dict(os.environ, **{LIST_ENV: str(list_path)})
    proc = subprocess.Popen(command(exe, list_path, [e["path"] for e in entries]), env=env,
                            creationflags=0x08000000 if os.name == "nt" else 0)
    _playing.append(proc)
    return proc


def stop():
    """Close the player started before, if it's still open."""
    while _playing:
        p = _playing.pop()
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


if __name__ == "__main__":          # play a saved review's work folder: cvplay.py <work folder>
    st = json.loads((Path(sys.argv[1]) / "analysis.json").read_text())
    play([entry(st)], get_player(lambda f, m: print(m)))
