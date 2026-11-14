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
- It plays inside the CleanView Play window (the owner, 2026-11-14: "Play inside CleanView
  Play", "Simpler, bigger buttons", and no info screens or subtitle button): mpv draws into
  the window's video area (`--wid`) with none of its own controls or keys; the window's big
  buttons drive it through `Remote` (mpv's JSON messages over a named pipe), and a key or click
  on the picture comes back to the window as a "cvplay-key" message (cvplay.lua).

Times count from 0 = the start of the file, the clock both ffmpeg's filters and mpv use
(mpv rebases a .ts file's own start time to 0 too)."""
import asyncio
import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import cleanview as engine

HERE = Path(__file__).resolve().parent
APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "CleanView Play"
SCRIPT = HERE / "cvplay.lua"
PLAYER_DIR = HERE / "bin" / "mpv"
LIST_ENV = "CVPLAY_LIST"            # where the player script finds the list (cvplay.lua)
REMOTE_ENV = "CVPLAY_REMOTE"        # set: keys and clicks go to the window (cvplay.lua)

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
_count = itertools.count(1)


def pipe_name():
    """A new name for the line between the window and the player."""
    n = f"cvplay-{os.getpid()}-{next(_count)}"
    return "\\\\.\\pipe\\" + n if os.name == "nt" else str(Path(tempfile.gettempdir()) / f"{n}.sock")


def command(exe, list_path, paths, wid=None, pipe=None):
    cmd = [str(exe), "--no-config", f"--script={SCRIPT}",
           # none of mpv's own controls, info screens, menus or keys: the window's big buttons
           # instead (the owner, 2026-11-14: "less ability to get info on the screen", and the
           # subtitle button "should be gone")
           "--osc=no", "--load-stats-overlay=no", "--load-console=no", "--load-select=no",
           "--load-commands=no", "--load-context-menu=no", "--load-positioning=no",
           "--load-auto-profiles=no", "--input-default-bindings=no",
           "--input-builtin-bindings=no", "--input-builtin-drag-and-drop=no",
           "--osd-level=0", "--osd-bar=no", "--osd-on-seek=no",
           "--force-window=yes", "--keep-open=no", "--idle=no",
           "--hwdec=auto-copy",            # the graphics card decodes; filters still work
           "--sid=no",                     # no captions (as clean copies have none showing)
           "--ytdl=no", "--save-position-on-quit=no", "--cursor-autohide=1500",
           "--msg-level=all=warn", "--title=${media-title} - CleanView Play"]
    cmd.append(f"--wid={wid}" if wid is not None else "--autofit=85%")
    if pipe:
        cmd.append(f"--input-ipc-server={pipe}")
    return cmd + ["--"] + [str(p) for p in paths]


def play(entries, exe=None, wid=None, extra=()):
    """Play these videos one after another with their mutes and skips, inside the window `wid`
    if given (extra: more mpv options, for tests). Returns the process; `proc.pipe` is the
    name to reach it by (see Remote)."""
    exe = exe or player()
    if exe is None:
        raise PlayError("The video player isn't here yet.")
    missing = [e["path"] for e in entries if not Path(e["path"]).exists()]
    if missing:
        raise Moved(f"Can't find {Path(missing[0]).name}. Was it moved or renamed? Check it "
                    "again from where it is now.")
    list_path = write_list(entries)
    stop()
    pipe = pipe_name()
    env = dict(os.environ, **{LIST_ENV: str(list_path), REMOTE_ENV: "1"})
    cmd = command(exe, list_path, [e["path"] for e in entries], wid, pipe)
    at = cmd.index("--")
    cmd[at:at] = list(extra)
    log = open(list_path.parent / "player.txt", "w", encoding="utf8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT,
                                creationflags=0x08000000 if os.name == "nt" else 0)
    finally:
        log.close()
    proc.pipe = pipe
    _playing.append(proc)
    return proc


def player_said():
    """What the player wrote last time (its warnings and errors), for the details file."""
    try:
        return (APP_DATA / "play" / "player.txt").read_text(encoding="utf8")[-3000:]
    except OSError:
        return ""


def stop():
    """Close the player started before, if it's still open."""
    while _playing:
        p = _playing.pop()
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


# ---------- watched time: the clock of what's seen, cuts left out ----------
def watched(skips, t):
    """Where t (the file's own clock) is in what's seen: the cuts before it left out."""
    return max(0.0, t - sum(min(e, t) - s for s, e in skips if s < t))


def original(skips, w):
    """The file's own clock for w seconds into what's seen (never inside a cut)."""
    t = max(0.0, w)
    for s, e in sorted(skips):
        if s <= t:
            t += e - s
        else:
            break
    return t


def watch_length(e, duration=None):
    """How long this video is once its cuts are skipped."""
    dur = duration or e.get("duration") or 0
    return max(0.0, dur - sum(min(b, dur) - a for a, b in e.get("skips", []) if a < dur))


class _Line(asyncio.Protocol):
    def __init__(self, remote):
        self.remote, self.buf = remote, b""

    def connection_made(self, transport):
        self.remote._tr = transport

    def data_received(self, data):
        *lines, self.buf = (self.buf + data).split(b"\n")
        for line in lines:
            self.remote._got(line)

    def connection_lost(self, exc):
        self.remote._lost()


class Remote:
    """The window's line to the player while it plays: mpv's JSON messages over the named pipe
    it was started with (asyncio's Windows pipes, which can read and write at once). What the
    player reports lands in `state` (pause, time-pos, duration, playlist-pos, volume...); the
    keys and clicks on the picture (cvplay.lua) come in on `keys`. `gone` is set once the
    player has closed, or couldn't be reached."""
    WATCH = ("pause", "time-pos", "duration", "playlist-pos", "playlist-count", "volume",
             "media-title")

    def __init__(self, pipe, proc=None, wait=20.0):
        self.pipe, self.proc = pipe, proc
        self.state, self.keys = {}, queue.Queue()
        self.ready, self.gone = threading.Event(), threading.Event()
        self._tr = None
        self._loop = asyncio.ProactorEventLoop() if os.name == "nt" else asyncio.new_event_loop()
        self._closed = None
        threading.Thread(target=self._run, args=(wait,), daemon=True,
                         name="player remote").start()

    def _run(self, wait):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main(wait))
        except Exception:
            pass
        finally:
            self.gone.set()
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self, wait):
        self._closed = asyncio.Event()
        until = time.monotonic() + wait
        while True:                         # the player makes the pipe a moment after it starts
            try:
                if os.name == "nt":
                    tr, _ = await self._loop.create_pipe_connection(lambda: _Line(self), self.pipe)
                else:
                    tr, _ = await self._loop.create_unix_connection(lambda: _Line(self), self.pipe)
                self._tr = tr               # (connection_made may come a moment later)
                break
            except OSError:
                if time.monotonic() > until or (self.proc and self.proc.poll() is not None):
                    return
                await asyncio.sleep(0.1)
        for i, name in enumerate(self.WATCH, 1):
            self._write(["observe_property", i, name])
        self.ready.set()
        await self._closed.wait()
        if self._tr is not None:
            self._tr.close()

    def _got(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        ev = msg.get("event")
        if ev == "property-change":
            self.state[msg.get("name")] = msg.get("data")
        elif ev == "client-message":
            args = msg.get("args") or []
            if len(args) > 1 and args[0] == "cvplay-key":
                self.keys.put(args[1])

    def _lost(self):
        self._tr = None
        if self._closed is not None:
            self._closed.set()

    def _write(self, cmd):
        if self._tr is not None and not self._tr.is_closing():
            self._tr.write((json.dumps({"command": cmd}) + "\n").encode("utf8"))

    def send(self, *cmd):
        """Tell the player something (an mpv command: "cycle", "pause"; "seek", 12.5, ...)."""
        if self.gone.is_set():
            return
        try:
            self._loop.call_soon_threadsafe(self._write, list(cmd))
        except RuntimeError:                # already closed
            pass

    def close(self, wait=2.0):
        """Close the player: asked first, then made to."""
        self.send("quit")
        if self.proc is not None:
            try:
                self.proc.wait(timeout=wait)
            except Exception:
                try:
                    self.proc.terminate()
                except OSError:
                    pass
        if not self.gone.is_set():
            try:
                self._loop.call_soon_threadsafe(self._lost)
            except RuntimeError:
                pass


if __name__ == "__main__":          # play a saved review's work folder: cvplay.py <work folder>
    st = json.loads((Path(sys.argv[1]) / "analysis.json").read_text())
    play([entry(st)], get_player(lambda f, m: print(m)))
