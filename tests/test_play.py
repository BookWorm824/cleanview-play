"""CleanView Play (the owner, 2026-11-13: "the play only clean view no saving", then "add a
play now feature for now"): "Play now" on the review page plays the original with what's
ticked muted and cut, through the mpv player and cvplay.lua. No copy is written.

The mutes, cuts and quieter music must be exactly what CleanView's render() would have put
in a clean copy, since the finding is CleanView's own."""
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unittest
from pathlib import Path
from unittest import mock

import support
import test_ui

import cleanview as engine
import cvplay

ui = test_ui.ui


def st_with(**kw):
    st = support.sample_review()
    st.update(kw)
    return st


CUES = [{"s": 10.0, "e": 12.0, "text": "Oh hell, not again."},
        {"s": 14.0, "e": 15.0, "text": "Damn it all"},
        {"s": 23.5, "e": 24.5, "text": "A muted line"},
        {"s": 31.0, "e": 33.0, "text": "In the cut"},
        {"s": 28.0, "e": 32.0, "text": "Runs into the cut"},
        {"s": 45.0, "e": 46.0, "text": "Nice day."}]


class PlayList(unittest.TestCase):
    def test_the_ticked_scene_is_skipped_the_unticked_one_not(self):
        e = cvplay.play_list(support.sample_review())
        self.assertEqual(e["skips"], [[30.0, 38.0]])
        self.assertIn("between(t,30.000,38.000)", e["vf"])         # black while it's jumped
        self.assertNotIn("60.000", e["vf"] + e["af"])

    def test_words_and_lines_are_muted_and_the_cut_silent(self):
        e = cvplay.play_list(support.sample_review())
        for s, t in (("14.200", "14.500"), ("23.000", "25.000")):   # the word, a flagged line
            self.assertIn(f"between(t,{s},{t})", e["af"])
        # the line at 38-40 touches the cut (30-38), so they're one silent stretch
        self.assertIn("between(t,30.000,40.000)", e["af"])
        self.assertTrue(e["af"].startswith("lavfi=[volume=volume=0:enable='"))

    def test_the_same_mutes_render_would_make(self):
        st = support.sample_review()
        lines = [l for l in st["lines"] if l["mute"]]
        mute_rs = engine._merge([(m["s"], m["e"]) for m in st["mutes"]] +
                                engine._merge((l["s"], l["e"]) for l in lines))
        cut_rs = [(30.0, 38.0)]
        want = engine._mute_filters(engine._merge(mute_rs + cut_rs), 0.0)
        self.assertEqual(cvplay.play_list(st)["af"], "lavfi=[" + ",".join(want) + "]")

    def test_her_ticks_win_over_the_defaults(self):
        st = support.sample_review()
        e = cvplay.play_list(st, cuts=[st["scenes"][1]], lines=[], words=[], quiet=[])
        self.assertEqual(e["skips"], [[60.0, 64.0]])
        self.assertNotIn("14.200", e["af"])
        self.assertEqual(e["mutes"], 0)

    def test_nothing_ticked_plays_it_as_it_is(self):
        e = cvplay.play_list(support.sample_review(), cuts=[], lines=[], words=[], quiet=[])
        self.assertEqual((e["skips"], e["af"], e["vf"]), ([], "", ""))

    def test_a_long_muted_stretch_is_skipped_as_render_cuts_it(self):
        st = support.sample_review()
        lines = [{"s": 50.0, "e": 52.0}, {"s": 52.3, "e": 56.5}]       # 6.5 s, nearly all muted
        e = cvplay.play_list(st, cuts=[], lines=lines, words=[], quiet=[])
        self.assertEqual(e["skips"], [[50.0, 56.5]])
        self.assertEqual(e["long"], 1)

    def test_loud_music_turned_down(self):
        st = st_with(quiet=[{"s": 70.0, "e": 80.0, "lower": True}])
        e = cvplay.play_list(st, cuts=[], lines=[], words=[])
        self.assertIn(engine._quieter([(70.0, 80.0)]), e["af"])
        self.assertIn("eval=frame", e["af"])

    def test_many_mutes_split_into_filters_of_fifty(self):
        words = [{"s": i * 2.0, "e": i * 2.0 + 0.3} for i in range(120)]
        e = cvplay.play_list(st_with(duration=300.0), cuts=[], lines=[], words=words, quiet=[])
        self.assertEqual(e["af"].count("volume=volume=0"), 3)
        self.assertEqual(e["mutes"], 120)

    def test_entry_names_the_file_and_its_sound_track(self):
        e = cvplay.entry(support.sample_review())
        self.assertEqual(e["path"], str(support.SANDBOX / "Sample Show S01E01.mp4"))
        self.assertEqual(e["title"], "Sample Show S01E01")
        self.assertEqual(e["audio_index"], 1)


class Subtitles(unittest.TestCase):
    # the owner, 2026-11-14: "people can still access subtitles in some way right? i just
    # didn't like the bubble thing". Clean ones, off unless turned on.
    def test_clean_like_a_saved_copy_but_on_the_files_own_clock(self):
        cues = cvplay.entry(st_with(cues=CUES))["cues"]
        self.assertEqual(cues, [
            {"s": 10.0, "e": 12.0, "text": "Oh \u2014, not again."},     # language blanked
            {"s": 14.0, "e": 15.0, "text": "\u2014 it all"},
            {"s": 28.0, "e": 30.0, "text": "Runs into the cut"},          # the part before it
            {"s": 45.0, "e": 46.0, "text": "Nice day."}])                 # not moved up
        # the muted line (23-25) and the one inside the cut (30-38) are left out

    def test_no_subtitles_none_offered(self):
        self.assertEqual(cvplay.entry(st_with(cues=[]))["cues"], [])

    def test_each_video_gets_its_own_file(self):
        a, b = cvplay.entry(st_with(cues=CUES)), cvplay.entry(st_with(cues=[]))
        path = cvplay.write_list([a, b])
        videos = json.loads(path.read_text(encoding="utf8"))["videos"]
        srt = Path(videos[0]["srt"])
        self.assertEqual(srt, support.APP_DATA / "play" / "subtitles 1.srt")
        text = srt.read_text(encoding="utf8")
        self.assertIn("00:00:10,000 --> 00:00:12,000\nOh \u2014, not again.", text)
        self.assertNotIn("hell", text)
        self.assertNotIn("srt", videos[1])
        self.assertNotIn("cues", videos[0], "the list itself stays small")
        self.assertIn("cues", a, "what it was given is left as it was")


class Player(unittest.TestCase):
    def test_the_list_is_kept_in_the_apps_own_folder(self):
        path = cvplay.write_list([cvplay.entry(support.sample_review())])
        self.assertEqual(path, support.APP_DATA / "play" / "now.json")
        data = json.loads(path.read_text(encoding="utf8"))
        self.assertEqual(data["videos"][0]["skips"], [[30.0, 38.0]])

    def test_the_player_is_started_on_its_own_with_the_script(self):
        cmd = cvplay.command(Path("mpv.exe"), Path("now.json"), ["a.mkv", "b.mkv"])
        self.assertIn("--no-config", cmd)                    # her own mpv settings can't interfere
        self.assertIn(f"--script={cvplay.SCRIPT}", cmd)
        self.assertIn("--sid=no", cmd)                       # no captions
        self.assertEqual(cmd[-3:], ["--", "a.mkv", "b.mkv"])

    def test_none_of_the_players_own_controls_or_info(self):
        # the owner, 2026-11-14: "less ability to get info on the screen", the subtitle button
        # "should be gone", "Simpler, bigger buttons", "Play inside CleanView Play"
        cmd = cvplay.command(Path("mpv.exe"), Path("now.json"), ["a.mkv"], wid=1234,
                             pipe=r"\\.\pipe\cvplay-test")
        for opt in ("--osc=no", "--load-stats-overlay=no", "--load-console=no",
                    "--load-select=no", "--input-default-bindings=no",
                    "--input-builtin-bindings=no", "--osd-level=0", "--wid=1234",
                    r"--input-ipc-server=\\.\pipe\cvplay-test"):
            self.assertIn(opt, cmd)
        self.assertNotIn("--autofit=85%", cmd)               # it fills the window's picture area

    def test_watched_time_leaves_the_cuts_out(self):
        skips = [[30.0, 38.0], [60.0, 64.0]]
        self.assertEqual(cvplay.watched(skips, 20.0), 20.0)
        self.assertEqual(cvplay.watched(skips, 34.0), 30.0)  # inside a cut: where it starts
        self.assertEqual(cvplay.watched(skips, 70.0), 58.0)
        self.assertEqual(cvplay.original(skips, 30.0), 38.0)  # never inside a cut
        self.assertEqual(cvplay.original(skips, 58.0), 70.0)
        for w in (0.0, 12.5, 29.9, 31.0, 55.0, 80.0):
            self.assertAlmostEqual(cvplay.watched(skips, cvplay.original(skips, w)), w)
        self.assertEqual(cvplay.watch_length({"skips": skips, "duration": 100.0}), 88.0)
        self.assertEqual(cvplay.watch_length({"skips": [[90.0, 120.0]]}, 100.0), 90.0)

    def test_play_tells_the_script_where_the_list_is(self):
        video = support.SANDBOX / "Sample Show S01E01.mp4"
        video.write_bytes(b"")
        started = []

        class Proc:
            def __init__(self, cmd, env=None, **kw):
                started.append((cmd, env))
                self.stopped = False

            def poll(self):
                return None

            def terminate(self):
                self.stopped = True

        with mock.patch.object(cvplay.subprocess, "Popen", Proc):
            first = cvplay.play([cvplay.entry(support.sample_review())], exe=Path("mpv.exe"))
            second = cvplay.play([cvplay.entry(support.sample_review())], exe=Path("mpv.exe"))
        cmd, env = started[0]
        self.assertEqual(env[cvplay.LIST_ENV], str(support.APP_DATA / "play" / "now.json"))
        self.assertEqual(env[cvplay.REMOTE_ENV], "1")        # keys and clicks to the window
        self.assertEqual(cmd[-1], str(video))
        self.assertIn(f"--input-ipc-server={first.pipe}", cmd)
        self.assertNotEqual(first.pipe, second.pipe)
        self.assertTrue(first.stopped, "playing again closes the player that was open")
        self.assertFalse(second.stopped)
        cvplay._playing.clear()

    def test_a_moved_video_is_said_plainly(self):
        e = cvplay.entry(st_with(path=str(support.SANDBOX / "Gone away.mp4")))
        with self.assertRaises(FileNotFoundError) as cm:
            cvplay.play([e], exe=Path("mpv.exe"))
        self.assertIn("Gone away.mp4", str(cm.exception))

    @unittest.skipUnless(os.name == "nt", "Windows' own tar unpacks the player")
    def test_the_player_is_downloaded_once_and_unpacked(self):
        tmp = Path(tempfile.mkdtemp(prefix="cv-mpv-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        src = tmp / "src" / "mpv-x86_64-test"
        src.mkdir(parents=True)
        (src / "mpv.exe").write_bytes(b"not really mpv")
        (src / "d3dcompiler_43.dll").write_bytes(b"x")
        arc = tmp / "mpv.7z"
        tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        made = subprocess.run([str(tar), "--format", "7zip", "-cf", str(arc), "-C",
                               str(tmp / "src"), "mpv-x86_64-test"], capture_output=True)
        if made.returncode or not arc.exists():
            self.skipTest("this tar can't make a .7z to test with")

        class Resp:
            def __init__(self, data):
                self.data, self.headers = data, {"Content-Length": str(len(data))}

            def read(self, n=-1):
                out, self.data = self.data[:n], self.data[n:]
                return out

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        said = []
        with mock.patch.object(cvplay, "PLAYER_DIR", tmp / "app" / "bin" / "mpv"), \
                mock.patch.object(cvplay, "_player_url", lambda: "https://example.invalid/m.7z"), \
                mock.patch.object(cvplay.urllib.request, "urlopen",
                                  lambda req, timeout=0: Resp(arc.read_bytes())):
            exe = cvplay.get_player(lambda f, m: said.append(m))
            self.assertEqual(exe, tmp / "app" / "bin" / "mpv" / "mpv.exe")
            self.assertEqual(exe.read_bytes(), b"not really mpv")
            self.assertTrue((exe.parent / "d3dcompiler_43.dll").exists(), "the whole folder")
            self.assertEqual(cvplay.get_player(), exe)                 # not again
        self.assertIn("only this once", said[0])
        self.assertFalse((support.APP_DATA / "downloads" / "mpv.7z").exists(), "tidied up")

    def test_the_new_files_go_out_with_updates(self):
        v = json.loads((support.ROOT / "version.json").read_text())
        for name in ("cvplay.py", "cvplay.lua"):
            self.assertIn(name, v["files"])
        for name in v["files"]:
            self.assertTrue((support.ROOT / name).exists(), name)

    def test_reports_say_cleanview_play(self):
        self.assertTrue(engine.app_version().startswith("Play "), engine.app_version())


class OnTheReviewPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = (ui.App._check_update, ui.App._check_every_ai)
        ui.App._check_update = lambda self, manual=False: None
        ui.App._check_every_ai = lambda self: None
        support.write_review()
        try:
            cls.app = ui.App()
        except tk.TclError as ex:
            ui.App._check_update, ui.App._check_every_ai = cls.saved
            raise unittest.SkipTest(f"no screen to draw on ({ex})")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except tk.TclError:
            pass
        ui.App._check_update, ui.App._check_every_ai = cls.saved

    def setUp(self):
        support.write_review()
        self.assertTrue(self.app._resume())
        self.app.update()

    def buttons(self):
        out = []

        def walk(w):
            if isinstance(w, ttk_button):
                out.append(str(w.cget("text")))
            for c in w.winfo_children():
                walk(c)
        walk(self.app.footer)
        return out

    def press_play(self):
        """Play now with a stand-in player: returns what it was asked to play."""
        played = []

        def play(entries, exe=None, wid=None, extra=()):
            played.append((entries, exe, wid))
            return FakeProc()
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", play), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app._play_from_review()
            for _ in range(200):
                self.app.update()
                if played and getattr(self.app, "watching", None) is not None:
                    break
                self.app.after(10)
        return played

    def tearDown(self):
        w = getattr(self.app, "watching", None)
        if w is not None and w.winfo_exists():
            w.finish()
        self.app.update()

    def run_ticks(self, n=3):
        for _ in range(n):
            w = self.app.watching
            if w is None:
                return
            w._tick()
            self.app.update()

    def test_play_now_instead_of_save(self):
        names = self.buttons()
        self.assertIn("▶  Play now", names)
        self.assertFalse([n for n in names if "Save clean" in n])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)

    def test_play_now_plays_what_is_ticked(self):
        played = self.press_play()
        self.assertEqual(len(played), 1)
        entries, exe, wid = played[0]
        self.assertEqual(exe, Path("mpv.exe"))
        self.assertEqual(entries[0]["skips"], [[30.0, 38.0]])
        self.assertIn("between(t,14.200,14.500)", entries[0]["af"])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAYING_SAID)
        self.assertEqual(self.app.page_name, "review", "the review stays open")

    def test_it_plays_inside_the_window(self):
        # the owner, 2026-11-14: "Play inside CleanView Play"
        entries, exe, wid = self.press_play()[0]
        w = self.app.watching
        self.assertEqual(wid, w.video.winfo_id(), "the player draws in the picture area")
        self.assertEqual(w.winfo_toplevel(), self.app)
        self.assertEqual(str(w.place_info().get("relwidth")), "1")   # covers the review

    def test_big_simple_buttons(self):
        # the owner, 2026-11-14: "Simpler, bigger buttons"
        self.press_play()
        w = self.app.watching
        found = {}

        def walk(x):
            if isinstance(x, tk.Button):
                found[x.what] = x
            for c in x.winfo_children():
                walk(c)
        walk(w.bar)
        for what in ("stop", "back", "pause", "forward", "quieter", "louder", "fullscreen",
                     "next"):
            self.assertIn(what, found)
        for b in found.values():
            self.assertGreaterEqual(abs(tkfont.Font(font=b.cget("font")).actual("size")), 13)
        self.assertFalse(found["next"].winfo_manager(), "Next only with more than one video")

    def test_the_buttons_drive_the_player(self):
        self.press_play()
        w = self.app.watching
        r = w.remote
        r.state.update({"time-pos": 25.0, "duration": 100.0, "playlist-pos": 0, "pause": False})
        self.run_ticks()
        self.assertEqual(w.pause_btn.cget("text"), "❚❚  Pause")
        w._do("pause")
        w._do("forward")                    # 25 + 10 of what's seen, past the 30-38 cut: 43
        w._do("louder")
        r.state["time-pos"] = 40.0
        w._do("back")                       # 40 is 32 seen; 10 back is 22
        self.assertEqual(r.sent, [("cycle", "pause"), ("seek", 43.0, "absolute+exact"),
                                  ("add", "volume", 10), ("seek", 22.0, "absolute+exact")])
        r.state.update({"pause": True, "volume": 90.0})
        self.run_ticks()
        self.assertEqual(w.pause_btn.cget("text"), "▶  Play")
        self.assertIn("Volume 90%", w.time_lbl.cget("text"))
        self.assertIn("0:32 / 1:32", w.time_lbl.cget("text"))     # cut left out of both

    def test_keys_and_clicks_on_the_picture(self):
        self.press_play()
        w = self.app.watching
        w.remote.keys.put("pause")
        w.remote.keys.put("quieter")
        self.run_ticks(1)
        self.assertEqual(w.remote.sent, [("cycle", "pause"), ("add", "volume", -10)])

    def test_full_screen_and_back(self):
        self.press_play()
        w = self.app.watching
        asked = []
        with mock.patch.object(self.app, "attributes", lambda *a: asked.append(a)):
            w._do("fullscreen")
            self.assertTrue(w.full)
            self.assertIn("Leave", w.full_btn.cget("text"))
            w._do("esc")
        self.assertEqual(asked, [("-fullscreen", True), ("-fullscreen", False)])
        self.assertFalse(w.full)

    def test_stop_uncovers_the_review_as_it_was(self):
        ticks = [v.get() for v, j, sc in self.app.scene_vars]
        self.press_play()
        w = self.app.watching
        r = w.remote
        r.state["time-pos"] = 5.0
        self.run_ticks(1)
        w._do("stop")
        for _ in range(50):
            self.app.update()
            if r.closed:
                break
            self.app.after(10)
        self.assertTrue(r.closed, "the player is closed")
        self.assertFalse(w.winfo_exists())
        self.assertIsNone(self.app.watching)
        self.assertEqual(self.app.page_name, "review")
        self.assertEqual([v.get() for v, j, sc in self.app.scene_vars], ticks)
        self.assertEqual(self.app.play_lbl.cget("text"), ui.WATCHED_SAID)

    def test_the_end_of_the_video_uncovers_the_review(self):
        self.press_play()
        w = self.app.watching
        w.remote.state["time-pos"] = 99.0
        self.run_ticks(1)
        w.remote.gone.set()                 # the last video ended and the player closed
        self.run_ticks(1)
        self.assertIsNone(self.app.watching)
        self.assertEqual(self.app.play_lbl.cget("text"), ui.WATCHED_SAID)

    def test_a_player_that_never_started_is_said_plainly(self):
        said = []
        self.press_play()
        w = self.app.watching
        with mock.patch.object(ui.messagebox, "showerror", lambda t, m, **k: said.append(m)):
            w.remote.gone.set()
            self.run_ticks(1)
        self.assertIn("closed before the video started", said[0])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)

    def test_closing_the_window_closes_the_player(self):
        self.press_play()
        w = self.app.watching
        proc = w.proc
        w.destroy()                         # as when the whole window closes
        self.assertTrue(proc.stopped)
        self.app.watching = None

    def test_the_window_keeps_answering_while_it_gets_ready(self):
        # 2026-11-14: "when it first opened it said not responding for a few seconds". The
        # mutes and subtitles are worked out, and the review saved, in the background.
        where, real_entry, real_write = [], cvplay.entry, self.app._write_session

        def entry(*a, **k):
            where.append(("entry", threading.current_thread() is threading.main_thread()))
            return real_entry(*a, **k)

        def write(*a, **k):
            where.append(("save", threading.current_thread() is threading.main_thread()))
            return real_write(*a, **k)
        with mock.patch.object(ui.cvplay, "entry", entry),                 mock.patch.object(self.app, "_write_session", write):
            self.press_play()
        self.assertEqual(sorted(set(where)), [("entry", False), ("save", False)])

    def test_subtitles_button(self):
        self.press_play()
        w = self.app.watching
        self.run_ticks(1)
        self.assertFalse(w.subs_btn.winfo_manager(), "no subtitles in this one: no button")
        w.entries[0]["cues"] = [{"s": 1.0, "e": 2.0, "text": "Hello"}]
        self.run_ticks(1)
        self.assertTrue(w.subs_btn.winfo_manager())
        self.assertEqual(w.subs_btn.cget("text"), "Subtitles: Off")
        w._do("subs")
        self.assertEqual(w.remote.sent, [("script-message", "cvplay-subs")])
        w.remote.state["sid"] = 3
        self.run_ticks(1)
        self.assertEqual(w.subs_btn.cget("text"), "Subtitles: On")
        w.remote.state["sid"] = False
        self.run_ticks(1)
        self.assertEqual(w.subs_btn.cget("text"), "Subtitles: Off")

    def test_a_changed_tick_plays_with_the_change(self):
        for v, j, sc in self.app.scene_vars:
            v.set(sc["s"] == 60.0)              # the kiss ticked, the fight unticked
        for v, j, m in self.app.word_vars:
            v.set(False)
        entries, _, _ = self.press_play()[0]
        self.assertEqual(entries[0]["skips"], [[60.0, 64.0]])
        self.assertNotIn("14.200", entries[0]["af"])

    def test_nothing_is_saved_but_the_ticks(self):
        before = set(support.SANDBOX.rglob("*.mp4"))
        self.press_play()
        self.assertEqual(set(support.SANDBOX.rglob("*.mp4")), before)
        self.assertFalse((support.SANDBOX / "Clean").exists())
        self.assertTrue((support.APP_DATA / "last_review.json").exists(), "picked up later")

    def test_a_failure_is_said_plainly(self):
        said = []
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", mock.Mock(
                    side_effect=cvplay.Moved("Can't find Sample.mp4. Was it moved?"))), \
                mock.patch.object(ui.messagebox, "showerror",
                                  lambda t, m, **k: said.append(m)):
            self.app._play_from_review()
            for _ in range(200):
                self.app.update()
                if said:
                    break
                self.app.after(10)
        self.assertIn("Can't find Sample.mp4", said[0])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)
        self.assertIsNone(getattr(self.app, "watching", None))


class FakeProc:
    pipe = "cvplay-test"

    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True


class FakeRemote:
    """Stands in for cvplay.Remote: what the window sent, and what the player says."""
    def __init__(self, pipe, proc=None, wait=20.0):
        self.pipe, self.proc = pipe, proc
        self.state, self.keys, self.sent = {}, queue.Queue(), []
        self.ready, self.gone = threading.Event(), threading.Event()
        self.closed = False

    def send(self, *cmd):
        self.sent.append(cmd)

    def close(self, wait=2.0):
        self.closed = True


ttk_button = ui.ttk.Button


class Script(unittest.TestCase):
    def test_the_player_script_reads_the_list_it_is_given(self):
        lua = cvplay.SCRIPT.read_text(encoding="utf8")
        self.assertIn('os.getenv("CVPLAY_LIST")', lua)
        self.assertEqual(cvplay.LIST_ENV, "CVPLAY_LIST")
        for prop in ('"af"', '"vf"', '"aid"', '"time-pos"', '"absolute+exact"'):
            self.assertIn(prop, lua)

    def test_keys_and_clicks_go_to_the_window(self):
        lua = cvplay.SCRIPT.read_text(encoding="utf8")
        self.assertIn('os.getenv("CVPLAY_REMOTE")', lua)
        self.assertEqual(cvplay.REMOTE_ENV, "CVPLAY_REMOTE")
        self.assertIn('"cvplay-key"', lua)
        self.assertIn("add_forced_key_binding", lua)     # mpv's own keys are all off
        for key in ("SPACE", "MBTN_LEFT", "MBTN_LEFT_DBL", "ESC", "LEFT", "RIGHT"):
            self.assertIn(key, lua)


@unittest.skipUnless(support.FFMPEG and cvplay.player(), "needs ffmpeg and the player (bin\\mpv)")
class RealPlayer(unittest.TestCase):
    """The real mpv with cvplay.lua, hidden (no window, sound written to a file): a tone whose
    pitch says which second is playing, so what's heard says where each mute and skip fell.
    Checked 2026-11-14 with mpv 0.42: to within a twentieth of a second, a .ts whose clock
    starts at 11 s too, the second sound track, and no frame of a cut shown."""
    RATE = 48000

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cv-real-play-"))
        cls.video = cls.tmp / "tone.mp4"
        subprocess.run([support.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=320x180:r=24:d=10", "-f", "lavfi", "-i",
                        "aevalsrc='0.5*sin(2*PI*(200+50*floor(t))*t)':s=48000:d=10",
                        "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*3000*t)':s=48000:d=10",
                        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264",
                        "-preset", "veryfast", "-c:a", "aac", str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def heard(self, st, extra=()):
        import numpy as np
        import wave
        out = self.tmp / "heard.wav"
        out.unlink(missing_ok=True)
        e = cvplay.entry(st)
        lst = cvplay.write_list([e], self.tmp)
        cmd = cvplay.command(cvplay.player(), lst, [e["path"]])
        at = cmd.index("--")
        cmd[at:at] = ["--vo=null", "--ao=pcm", f"--ao-pcm-file={out}", "--force-window=no",
                      "--audio-format=s16", "--msg-level=all=error", *extra]
        subprocess.run(cmd, env=dict(os.environ, **{cvplay.LIST_ENV: str(lst)}),
                       capture_output=True, timeout=120, check=True)
        with wave.open(str(out)) as w:
            ch = w.getnchannels()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return a.reshape(-1, ch)[:, 0].astype(float) / 32768

    def what(self, a, t):
        """At t seconds of what was heard: None for silence, else the tone's pitch in Hz."""
        import numpy as np
        x = a[int(t * self.RATE):int((t + 0.1) * self.RATE)]
        if np.sqrt(np.mean(x ** 2)) < 0.01:
            return None
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), 16384))
        return round(np.argmax(spec) * self.RATE / 16384)

    def second(self, a, t):
        f = self.what(a, t)
        return None if f is None else round((f - 200) / 50)

    def sample(self, **kw):
        st = {"path": str(self.video), "duration": 10.0, "info": {"audio": {"index": 1}},
              "scenes": [], "lines": [], "mutes": [], "quiet": []}
        st.update(kw)
        return st

    def test_mutes_and_a_skip_land_where_they_should(self):
        a = self.heard(self.sample(mutes=[{"s": 2.0, "e": 3.0}],
                                   lines=[{"s": 4.5, "e": 5.0, "mute": True}],
                                   scenes=[{"s": 6.0, "e": 8.0, "cut": True}]))
        self.assertAlmostEqual(len(a) / self.RATE, 8.0, delta=0.15)        # 2 s skipped
        self.assertEqual(self.second(a, 1.85), 1)
        self.assertIsNone(self.second(a, 2.05))                             # the word
        self.assertIsNone(self.second(a, 2.85))
        self.assertEqual(self.second(a, 3.1), 3)
        self.assertIsNone(self.second(a, 4.65))                             # the line
        self.assertEqual(self.second(a, 5.1), 5)
        self.assertEqual(self.second(a, 5.85), 5)
        self.assertEqual(self.second(a, 6.1), 8)                            # 6-8 skipped

    def test_the_sound_track_cleanview_listened_to(self):
        a = self.heard(self.sample(info={"audio": {"index": 2}}))
        self.assertAlmostEqual(self.what(a, 4.0), 3000, delta=10)

    def test_no_frame_of_a_cut_is_shown(self):
        frames = self.tmp / "frames"
        shutil.rmtree(frames, ignore_errors=True)
        frames.mkdir()
        self.heard(self.sample(scenes=[{"s": 3.0, "e": 5.0, "cut": True}]),
                   extra=["--vo=image", "--vo-image-format=jpg", f"--vo-image-outdir={frames}"])
        sizes = [f.stat().st_size for f in sorted(frames.glob("*.jpg"))]
        picture = [s for s in sizes if s > sorted(sizes)[len(sizes) // 2] / 2]
        # 10 s less the 2 cut, at 24 a second; the frame at exactly 5.000 is the cut's own (its
        # cover is black there, as render() drops it), so 191
        self.assertIn(len(picture), (8 * 24 - 1, 8 * 24))
        # at the jump a frame or two of black may show (the cut's cover), never its picture.
        # Played hidden, it runs as fast as it can, so on a busy PC (a full test run) the jump
        # can come a few frames later: 4 once (2026-11-14). Still black, never the picture.
        self.assertLessEqual(len(sizes) - len(picture), 8)

    def test_the_window_drives_the_player(self):
        # the window's buttons and keys reach the real player through Remote (no picture or
        # sound here): pause, a jump into a cut lands past it, a key on the picture comes back
        e = cvplay.entry(self.sample(scenes=[{"s": 6.0, "e": 8.0, "cut": True}]))
        proc = cvplay.play([e], cvplay.player(),
                           extra=["--vo=null", "--ao=null", "--force-window=no"])
        r = cvplay.Remote(proc.pipe, proc)
        self.addCleanup(r.close)
        self.assertTrue(r.ready.wait(15), "reached the player")

        def until(test, secs=8.0):
            stop = time.monotonic() + secs
            while time.monotonic() < stop:
                if test():
                    return True
                time.sleep(0.05)
            return False
        self.assertTrue(until(lambda: (r.state.get("time-pos") or 0) > 0.3))
        self.assertEqual(r.state.get("duration"), 10.0)
        self.assertEqual(r.state.get("playlist-count"), 1)
        r.send("cycle", "pause")
        self.assertTrue(until(lambda: r.state.get("pause") is True))
        r.send("cycle", "pause")
        r.send("seek", 6.5, "absolute+exact")
        self.assertTrue(until(lambda: (r.state.get("time-pos") or 0) >= 8.0),
                        r.state.get("time-pos"))
        r.send("script-message", "cvplay-key", "fullscreen")   # as the script does for a key
        self.assertTrue(until(lambda: not r.keys.empty()))
        self.assertEqual(r.keys.get(), "fullscreen")
        r.close()
        self.assertTrue(r.gone.wait(5))
        self.assertIsNotNone(proc.poll(), "the player closed")

    def test_clean_subtitles_turn_on_and_off(self):
        # off at first, the clean ones when turned on (never the video's own), off again
        st = self.sample(cues=[{"s": 0.0, "e": 9.5, "text": "Oh hell, it's a tone."}],
                         groups=["mild"])
        e = cvplay.entry(st)
        proc = cvplay.play([e], cvplay.player(),
                           extra=["--vo=null", "--ao=null", "--force-window=no"])
        r = cvplay.Remote(proc.pipe, proc)
        self.addCleanup(r.close)
        self.assertTrue(r.ready.wait(15), "reached the player")
        r.send("observe_property", 99, "sub-text")

        def until(test, secs=8.0):
            stop = time.monotonic() + secs
            while time.monotonic() < stop:
                if test():
                    return True
                time.sleep(0.05)
            return False
        self.assertTrue(until(lambda: (r.state.get("time-pos") or 0) > 0.5))
        self.assertIn(r.state.get("sid"), (False, "no"))
        self.assertFalse(r.state.get("sub-text"))
        r.send("script-message", "cvplay-subs")
        self.assertTrue(until(lambda: r.state.get("sid") not in (None, False, "no")))
        self.assertTrue(until(lambda: r.state.get("sub-text")), r.state)
        self.assertEqual(r.state["sub-text"], "Oh \u2014, it's a tone.")
        r.send("script-message", "cvplay-subs")
        self.assertTrue(until(lambda: r.state.get("sid") in (False, "no")))

    @unittest.skipUnless(os.name == "nt", "the picture inside the window is for Windows")
    def test_it_plays_inside_the_window(self):
        # the owner, 2026-11-14: "Play inside CleanView Play". The real player in a window
        # (see-through, as every test window; no sound): its picture sits in the window's
        # picture area and follows it when the window changes size; Stop closes it.
        import ctypes
        from ctypes import wintypes

        class Win(tk.Tk):
            def _px(self, n):
                return n
        try:
            root = Win()
        except tk.TclError as ex:
            self.skipTest(f"no screen to draw on ({ex})")
        self.addCleanup(root.destroy)
        root.geometry("800x600+40+40")
        ended = []
        w = ui.Watching(root, [cvplay.entry(self.sample())], lambda p, e: ended.append((p, e)))
        w.extra = ["--ao=null"]
        w.start(cvplay.player())
        user32 = ctypes.windll.user32
        user32.GetWindow.restype = wintypes.HWND

        def size(hwnd):
            rc = wintypes.RECT()
            user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rc))
            return rc.right - rc.left, rc.bottom - rc.top

        def pump(test, secs=15.0):
            stop = time.monotonic() + secs
            while time.monotonic() < stop:
                root.update()
                if test():
                    return True
                time.sleep(0.02)
            return False
        area = w.video.winfo_id()
        self.assertTrue(pump(lambda: w.played), "the video started")
        self.assertTrue(pump(lambda: user32.GetWindow(wintypes.HWND(area), 5)), "the picture")
        child = user32.GetWindow(wintypes.HWND(area), 5)          # GW_CHILD
        self.assertTrue(pump(lambda: size(child) == size(area)), (size(child), size(area)))
        root.geometry("640x480")
        self.assertTrue(pump(lambda: size(area)[0] < 700 and size(child) == size(area)),
                        (size(child), size(area)))
        w._do("pause")
        self.assertTrue(pump(lambda: w.remote.state.get("pause") is True))
        proc = w.proc
        w._do("stop")
        self.assertTrue(pump(lambda: proc.poll() is not None, 5), "the player closed")
        self.assertEqual(ended, [(True, False)])


if __name__ == "__main__":
    unittest.main()
