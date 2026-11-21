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
import sys
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

    def test_a_sound_only_scene_is_silenced_not_skipped(self):
        # 1.18.154: "Mute the sound only" keeps the picture; 10 s, longer than a long silence,
        # and still not skipped (render() mutes these after long_mutes)
        scene = {"s": 60.0, "e": 70.0, "cut": True, "mute_only": True, "kind": ["violence"]}
        e = cvplay.play_list(st_with(duration=300.0), cuts=[scene], lines=[], words=[], quiet=[])
        self.assertEqual(e["skips"], [])
        self.assertEqual(e["vf"], "")                               # the picture is kept
        self.assertIn("between(t,60.000,70.000)", e["af"])          # the sound isn't
        self.assertEqual(e["cut_seconds"], 0)
        both = cvplay.play_list(st_with(duration=300.0), cuts=[scene, dict(scene, s=100.0, e=105.0,
                                mute_only=False)], lines=[], words=[], quiet=[])
        self.assertEqual(both["skips"], [[100.0, 105.0]])

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

    def test_blanks_the_words_a_saved_copy_does(self):
        # 1.18.150: sexual words with "Sexual references", crude ones with "Crude humor"
        cues = [{"s": 50.0, "e": 52.0, "text": "What a slut"},
                {"s": 53.0, "e": 55.0, "text": "Nice turd"}]
        on = cvplay.entry(st_with(cues=cues, opts={"sexref": True, "crude": True}))["cues"]
        self.assertEqual([c["text"] for c in on], ["What a —", "Nice —"])
        off = cvplay.entry(st_with(cues=cues, opts={"sexref": False, "crude": False}))["cues"]
        self.assertEqual([c["text"] for c in off], ["What a slut", "Nice turd"])

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

        def play(entries, exe=None, wid=None, extra=(), start_at=None):
            played.append((entries, exe, wid))
            self.started_at = start_at
            return FakeProc()
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", play), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app._play_from_review()
            for _ in range(1000):
                self.app.update()
                w = getattr(self.app, "watching", None)
                if played and w is not None and w.remote is not None:
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

    def test_its_own_window_that_closes_on_its_own(self):
        # 1.0.13: "when i play a video but want to go back to [the program] i cant ... can we
        # turn the player into an extension of the program so it cann be closed separately"
        entries, exe, wid = self.press_play()[0]
        w = self.app.watching
        self.assertEqual(wid, w.video.winfo_id(), "the player draws in the picture area")
        self.assertIs(w.winfo_toplevel(), w.win, "a window of its own")
        self.assertIs(w.win.master, self.app, "CleanView Play's own")
        self.assertIn("Sample Show S01E01 - CleanView", w.win.title())
        w.close_window()                            # its X
        self.app.update()
        self.assertFalse(w.win.winfo_exists())
        self.assertTrue(self.app.winfo_exists(), "the program stays open")
        self.assertEqual(self.app.page_name, "review")
        self.assertIsNone(self.app.watching)

    def test_the_window_comes_up_at_once_saying_so(self):
        # 1.0.13: "when i make changes ... and then hit play ... it doesn't say anything. so it
        # appears the player froze"
        gate = threading.Event()
        real = cvplay.entry

        def slow(*a, **k):
            gate.wait(5)                    # working out the new ticks
            return real(*a, **k)
        with mock.patch.object(ui.cvplay, "entry", slow), \
                mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", lambda *a, **k: FakeProc()), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app._play_from_review()
            self.app.update()
            w = self.app.watching
            self.assertIsNotNone(w)
            self.assertEqual(w.time_lbl.cget("text"), "Getting it ready to play\u2026")
            gate.set()
            for _ in range(1000):
                self.app.update()
                if w.remote is not None:
                    break
                self.app.after(10)
            self.assertEqual(w.time_lbl.cget("text"), "Opening\u2026")

    def test_play_now_again_carries_on_from_the_same_spot(self):
        self.press_play()
        w = self.app.watching
        w.win.geometry("700x500+40+50")
        self.app.update()
        w.remote.state.update({"time-pos": 42.5, "playlist-pos": 0, "duration": 95.0})
        self.run_ticks(1)
        for v, j, sc in self.app.scene_vars:     # a tick changed while it plays
            v.set(sc["s"] == 60.0)
        played = self.press_play()
        self.assertTrue(w.remote.closed, "the old player closed")
        self.assertEqual(self.started_at, (0, 42.5))
        self.assertEqual(played[0][0][0]["skips"], [[60.0, 64.0]])
        self.assertTrue(self.app.watching.win.geometry().startswith("700x500"))

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
        with mock.patch.object(w.win, "attributes", lambda *a: asked.append(a)):
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

    def test_the_files_are_checked_and_the_player_started_in_the_background(self):
        # 1.0.6: still "not responding" for several seconds; a sleeping drive can take that
        # long just to say a file is there
        where = []

        def play(entries, exe=None, wid=None, extra=(), start_at=None):
            where.append(threading.current_thread() is threading.main_thread())
            return FakeProc()
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", play), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app._play_from_review()
            for _ in range(1000):
                self.app.update()
                w = getattr(self.app, "watching", None)
                if w is not None and w.remote is not None:
                    break
                self.app.after(10)
        self.assertEqual(where, [False])

    def test_stop_before_the_player_is_there(self):
        # Stop pressed while it's still starting: the player that turns up is closed
        gate, made = threading.Event(), []

        def play(entries, exe=None, wid=None, extra=(), start_at=None):
            gate.wait(5)
            made.append(FakeProc())
            return made[-1]
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", play), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app._play_from_review()
            for _ in range(1000):           # until the player is being started
                self.app.update()
                w = getattr(self.app, "watching", None)
                if w is not None and getattr(w, "started", None):
                    break
                self.app.after(10)
            self.app.watching._do("stop")
            self.assertIsNone(self.app.watching)
            gate.set()
            for _ in range(1000):           # up to 10 s on a busy PC
                self.app.update()
                if made:
                    break
                self.app.after(10)
            for _ in range(20):
                self.app.update()
                self.app.after(10)
        self.assertEqual(len(made), 1)
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)

    def test_a_short_freeze_after_play_now_is_written_down(self):
        # the window held up for 4 seconds just after Play now: where it was stuck is kept for
        # the problem note offered next time
        support.APP_DATA.mkdir(parents=True, exist_ok=True)
        note = ui.PROBLEM_NOTE
        note.unlink(missing_ok=True)
        self.addCleanup(note.unlink, True)
        with mock.patch.object(ui.App, "PLAY_FREEZE", 0.5):
            self.press_play()
            time.sleep(1.2)                 # the window doesn't answer for a while
            for _ in range(100):
                self.app.update()
                if note.exists():
                    break
                time.sleep(0.02)
        text = note.read_text(encoding="utf8")
        self.assertIn("after Play now (1 video)", text)
        self.assertIn("stopped answering for about", text)
        self.assertIn("--- MainThread ---", text)
        self.assertIn("test_a_short_freeze_after_play_now_is_written_down", text)  # where

    def test_subtitles_button(self):
        self.press_play()
        w = self.app.watching
        self.run_ticks(1)
        self.assertFalse(w.subs_btn.winfo_manager(), "no subtitles in this one: no button")
        w.remote.state["user-data/cvplay/subs"] = True          # cvplay.lua: it has some
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
            for _ in range(1000):
                self.app.update()
                if said:
                    break
                self.app.after(10)
        self.assertIn("Can't find Sample.mp4", said[0])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)
        self.assertIsNone(getattr(self.app, "watching", None))


class JustWatching(unittest.TestCase):
    """1.0.9 (the owner, 2026-11-14: "what if we also made it an allpurpose player with
    cleaning capablities. so someone could just open a movie and watch without cleaning",
    "could it also be like vlc where it plays many types of files?")."""
    @classmethod
    def setUpClass(cls):
        OnTheReviewPage.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        OnTheReviewPage.tearDownClass.__func__(cls)

    def setUp(self):
        self.app.show_setup()
        self.app.update()
        self.video = support.SANDBOX / "Some Movie (1999).mkv"
        self.video.write_bytes(b"x" * 10)
        self.views = support.APP_DATA / "views"

    def tearDown(self):
        w = getattr(self.app, "watching", None)
        if w is not None and w.winfo_exists():
            w.finish()
        self.app.update()
        shutil.rmtree(self.views, ignore_errors=True)

    def cleaned(self, path):
        st = st_with(path=str(path), duration=100.0)
        st["mutes"] = [{"s": 14.2, "e": 14.5, "word": "d**n"},
                       {"s": 50.0, "e": 50.3, "word": "h**l", "keep": True}]   # unticked
        folder = self.views / cvplay.engine_key(str(path))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "analysis.json").write_text(json.dumps(st), encoding="utf8")
        return st

    def watch(self, paths, answer=None):
        played, asked = [], []

        def play(entries, exe=None, wid=None, extra=(), start_at=None):
            played.append(entries)
            return FakeProc()

        def ask(title, msg, **k):
            asked.append(msg)
            return answer
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", play), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote), \
                mock.patch.object(ui.messagebox, "askyesnocancel", ask):
            self.app.watch_files(paths)
            for _ in range(1000):
                self.app.update()
                w = getattr(self.app, "watching", None)
                if played and w is not None and w.remote is not None:
                    break
                if asked and answer is None:
                    break
                self.app.after(10)
        return played, asked

    def test_the_button_is_on_the_start_page(self):
        self.assertEqual(self.app.watch_btn.cget("text"), "\u25b6  Watch a video\u2026")

    def test_plays_as_it_is(self):
        played, asked = self.watch([str(self.video)])
        self.assertEqual(asked, [], "never cleaned here: nothing to ask")
        e, = played[0]
        self.assertEqual((e["path"], e["skips"], e["af"], e["vf"]), (str(self.video), [], "", ""))
        self.assertTrue(e["own_subs"], "its own subtitles there to turn on")
        self.assertEqual(self.app.page_name, "setup")
        self.assertIsNotNone(self.app.watching)

    def test_the_player_screen_comes_up_at_once(self):
        # 1.0.10: "when i chose a file it also was very slow opening it seemed before playing".
        # Looking for a past cleaning (it reads the drive) is done in the background, with the
        # player's screen up and saying "Opening…" meanwhile.
        gate, where = threading.Event(), []

        def slow(path, views=None):
            where.append(threading.current_thread() is threading.main_thread())
            gate.wait(5)                    # a drive waking up
            return None
        with mock.patch.object(ui.cvplay, "cleaned_before", slow),                 mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")),                 mock.patch.object(ui.cvplay, "play", lambda *a, **k: FakeProc()),                 mock.patch.object(ui.cvplay, "Remote", FakeRemote):
            self.app.watch_files([str(self.video)])
            self.app.update()
            w = self.app.watching
            self.assertIsNotNone(w, "the screen is up before the check is done")
            self.assertEqual(w.time_lbl.cget("text"), "Opening…")
            gate.set()
            for _ in range(1000):
                self.app.update()
                if w.remote is not None:
                    break
                self.app.after(10)
            w._tick()
            self.assertEqual(w.time_lbl.cget("text"), "Opening…", "until the picture")
            w.remote.state["time-pos"] = 1.0
            w.remote.state["duration"] = 60.0
            w._tick()
            self.assertEqual(w.time_lbl.cget("text"), "0:01 / 1:00")
        self.assertEqual(where, [False])

    def test_cleaned_before_can_play_clean(self):
        self.cleaned(self.video)
        played, asked = self.watch([str(self.video)], answer=True)
        self.assertIn("You cleaned \u201cSome Movie (1999)\u201d before", asked[0])
        e, = played[0]
        self.assertEqual(e["skips"], [[30.0, 38.0]])                 # the ticked scene only
        self.assertIn("between(t,14.200,14.500)", e["af"])          # the ticked word
        self.assertNotIn("50.000", e["af"], "a word unticked last time isn't muted")
        self.assertFalse(e.get("own_subs"), "never the video's own subtitles when cleaned")

    def test_or_as_it_is(self):
        self.cleaned(self.video)
        played, asked = self.watch([str(self.video)], answer=False)
        self.assertEqual(played[0][0]["skips"], [])
        self.assertEqual(played[0][0]["af"], "")

    def test_or_not_at_all(self):
        self.cleaned(self.video)
        played, asked = self.watch([str(self.video)], answer=None)
        self.assertEqual(len(asked), 1)
        self.assertEqual(played, [])
        self.assertIsNone(getattr(self.app, "watching", None))

    def test_found_whichever_way_the_path_is_written(self):
        self.cleaned(str(self.video).replace("\\", "/"))       # as the file picker writes it
        self.assertIsNotNone(cvplay.cleaned_before(str(self.video), self.views))   # Explorer's
        self.assertIsNone(cvplay.cleaned_before(str(support.SANDBOX / "Other.mkv"), self.views)
                          if (support.SANDBOX / "Other.mkv").exists() else None)

    def test_many_types_offered(self):
        for kind in (".mkv", ".mp4", ".avi", ".wmv", ".m2ts", ".vob", ".webm", ".mp3", ".flac"):
            self.assertIn(kind, cvplay.VIDEO_TYPES + cvplay.AUDIO_TYPES)
        got = {}
        with mock.patch.object(ui.filedialog, "askopenfilenames",
                               lambda **k: got.update(k) or ()):
            self.app.watch_files()
        names = [n for n, _ in got["filetypes"]]
        self.assertEqual(names, ["Videos and music", "Videos", "Music", "All files"])
        self.assertIn("*.mkv", got["filetypes"][0][1])

    def test_a_video_sent_from_file_explorer_plays(self):
        sent = []
        with mock.patch.object(self.app, "watch_files", lambda paths=None: sent.append(paths)):
            ui.OPEN_THESE.write_text(json.dumps({"at": time.time(), "paths": [str(self.video)]}))
            self.app._check_open_these()
            ui.OPEN_THESE.write_text(json.dumps({"at": time.time() - 600, "paths": ["old"]}))
            self.app._check_open_these()                # left from long ago: not played now
        self.assertEqual(sent, [[str(self.video)]])
        self.assertFalse(ui.OPEN_THESE.exists())


class CleanedBefore(unittest.TestCase):
    """1.0.11 (the owner, 2026-11-14: "can we scan a video, save the log of where it should be
    cut ... come back to it later and it still play clean with out saving the source?", then
    yes to knowing a video by the file itself, and a Cleaned before list)."""
    @classmethod
    def setUpClass(cls):
        OnTheReviewPage.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        OnTheReviewPage.tearDownClass.__func__(cls)

    def setUp(self):
        self.app.show_setup()
        self.app.update()
        self.dir = Path(tempfile.mkdtemp(prefix="cv-cleaned-", dir=support.SANDBOX))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.views = support.APP_DATA / "views"
        self.addCleanup(shutil.rmtree, self.views, True)
        self.addCleanup(lambda: (support.APP_DATA / cvplay.INDEX_NAME).unlink(missing_ok=True))
        self.video = self.dir / "Castle S02E06.mkv"
        self.video.write_bytes(os.urandom(300_000))

    def tearDown(self):
        for name in ("watching", "cleaned_win"):
            w = getattr(self.app, name, None)
            if w is not None and w.winfo_exists():
                (w.finish if name == "watching" else w.destroy)()
        self.app.update()
        for t in threading.enumerate():     # a review saved puts its video on the list a
            if t.name == "remember cleaned":    # moment later: done before the list is cleared
                t.join(5)

    def scanned(self, path):
        """As a scan and review leave it: the log in views/<key>, on the list."""
        st = st_with(path=str(path), duration=100.0)
        view = cvplay.engine_key(str(path))
        (self.views / view).mkdir(parents=True, exist_ok=True)
        (self.views / view / "analysis.json").write_text(json.dumps(st), encoding="utf8")
        return st, view

    def test_the_file_itself_is_known(self):
        fp = cvplay.fingerprint(self.video)
        moved = self.dir / "elsewhere" / "renamed.mkv"
        moved.parent.mkdir()
        shutil.copy(self.video, moved)
        self.assertEqual(cvplay.fingerprint(moved), fp, "any folder, any name")
        other = self.dir / "other.mkv"
        other.write_bytes(os.urandom(300_000))
        self.assertNotEqual(cvplay.fingerprint(other), fp)
        tiny = self.dir / "tiny.mp3"
        tiny.write_bytes(b"abc")
        self.assertTrue(cvplay.fingerprint(tiny).startswith("3-"))

    def test_moved_and_renamed_still_plays_clean(self):
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        moved = self.dir / "Another drive" / "castle 2x06.mkv"
        moved.parent.mkdir()
        shutil.move(str(self.video), moved)
        found = cvplay.cleaned_before(str(moved), self.views)
        self.assertIsNotNone(found)
        self.assertEqual(found["path"], str(moved))
        self.assertEqual(cvplay.load_index(support.APP_DATA)[cvplay.fingerprint(moved)]["path"],
                         str(moved), "where it is now is remembered")

    def test_cleaned_before_this_update_are_listed_too(self):
        st, view = self.scanned(self.video)          # a log from before 1.0.11, not listed
        self.assertEqual(cvplay.cleaned_list(support.APP_DATA), [])
        self.assertEqual(cvplay.index_views(self.views), 1)
        (item,) = cvplay.cleaned_list(support.APP_DATA)
        self.assertEqual((item["title"], item["view"]), ("Castle S02E06", view))
        self.assertEqual(cvplay.index_views(self.views), 0, "once")

    def test_an_old_log_found_by_its_path_goes_on_the_list(self):
        self.scanned(self.video)
        self.assertIsNotNone(cvplay.cleaned_before(str(self.video), self.views))
        self.assertIn(cvplay.fingerprint(self.video), cvplay.load_index(support.APP_DATA))

    def test_found_on_another_drive_letter(self):
        st, view = self.scanned(self.video)
        fp = cvplay.remember(st, view, support.APP_DATA)
        item = dict(cvplay.cleaned_list(support.APP_DATA)[0])
        here = str(self.video)
        other = "Q" if here[0].upper() != "Q" else "R"
        item["path"] = other + here[1:]              # the drive had another letter then
        self.assertEqual(cvplay.find_again(item), here)
        item["fp"] = "0-not-it"
        self.assertIsNone(cvplay.find_again(item), "same name and size isn't enough")
        self.assertTrue(fp)

    def test_a_reviewed_video_goes_on_the_list(self):
        (support.SANDBOX / "Sample Show S01E01.mp4").write_bytes(b"sample video")
        support.write_review()
        self.assertTrue(self.app._resume())
        self.app.update()
        self.app._save_session()
        for _ in range(100):
            if cvplay.cleaned_list(support.APP_DATA):
                break
            time.sleep(0.02)
        titles = [e["title"] for e in cvplay.cleaned_list(support.APP_DATA)]
        self.assertIn("Sample Show S01E01", titles)

    def tree_of(self, win):
        return next(c for c in win.winfo_children()[2].winfo_children()
                    if isinstance(c, ui.ttk.Treeview))

    def choose(self, title="Castle S02E06"):
        """Open Cleaned before, choose this one and press Play clean (Enter)."""
        self.app.show_cleaned()
        win = self.app.cleaned_win
        self.app.update()
        tree = self.tree_of(win)
        row = next(i for i in tree.get_children() if tree.item(i)["values"][0] == title)
        tree.selection_set(row)
        win.play()                          # Play clean (a made-up key can go astray)
        return win

    def test_the_list_plays_one_clean(self):
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        self.assertEqual(self.app.cleaned_btn.cget("text"), "Cleaned before\u2026")
        asked = []
        with mock.patch.object(self.app, "watch_files",
                               lambda paths=None, clean=None, find=None, expect=None:
                               asked.append((clean, find["title"]))):
            win = self.choose()
            self.app.update()
        self.assertEqual(asked, [(True, "Castle S02E06")])
        self.assertFalse(win.winfo_exists())

    def play_from_list(self, find_again=None, said=None, answer=False):
        played = []
        patches = [mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")),
                   mock.patch.object(ui.cvplay, "play",
                                     lambda e, *a, **k: played.append(e) or FakeProc()),
                   mock.patch.object(ui.cvplay, "Remote", FakeRemote),
                   mock.patch.object(ui.messagebox, "askyesno",
                                     lambda t, m, **k: (said.append(m) if said is not None
                                                        else None) or answer)]
        if find_again:
            patches.append(mock.patch.object(ui.cvplay, "find_again", find_again))
        for x in patches:
            x.start()
            self.addCleanup(x.stop)
        self.choose()
        return played

    def pump_until(self, test, secs=10):
        stop = time.monotonic() + secs
        while time.monotonic() < stop:
            self.app.update()
            if test():
                return True
            self.app.after(10)
        return False

    def test_the_player_says_its_looking_then_plays_it_clean(self):
        # 1.0.14: "when i clicked on play one i cleaned before it took a while to load.
        # understandaable. but it didn't gove any indication of what was happening"
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        gate, real = threading.Event(), cvplay.find_again

        def slow(item):
            gate.wait(5)                    # a sleeping drive waking up
            return real(item)
        played = self.play_from_list(find_again=slow)
        self.app.update()
        w = self.app.watching
        self.assertIsNotNone(w, "the player's window is up at once")
        self.assertEqual(w.msg.cget("text"), "Looking for \u201cCastle S02E06\u201d\u2026")
        self.assertTrue(w.msg.place_info(), "big, in the middle")
        gate.set()
        self.assertTrue(self.pump_until(lambda: played and w.remote is not None))
        self.assertEqual(w.msg.cget("text"), "Opening\u2026")
        self.assertEqual(played[0][0]["path"], str(self.video))
        self.assertEqual(played[0][0]["skips"], [[30.0, 38.0]])         # clean, not asked
        w.remote.state.update({"time-pos": 1.0, "duration": 100.0})
        w._tick()
        self.assertFalse(w.msg.place_info(), "gone once the picture is up")

    def test_one_that_cant_be_found_is_said(self):
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        self.video.unlink()                         # the drive isn't plugged in
        said = []
        played = self.play_from_list(said=said, answer=False)
        self.assertTrue(self.pump_until(lambda: said))
        self.assertIn("Can't find \u201cCastle S02E06\u201d where it was", said[0])
        self.assertEqual(played, [])
        self.assertIsNone(self.app.watching, "its window closed")

    def test_a_different_file_chosen_is_said(self):
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        other = self.dir / "something else.mkv"
        other.write_bytes(os.urandom(300_000))
        told = []
        with mock.patch.object(ui.messagebox, "showinfo", lambda t, m, **k: told.append(m)), \
                mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")):
            self.app.watch_files([str(other)], clean=True,
                                 expect=cvplay.fingerprint(self.video))
            self.assertTrue(self.pump_until(lambda: told))
        self.assertIn("That's a different video", told[0])

    def test_nothing_cleaned_yet(self):
        said = []
        with mock.patch.object(ui.messagebox, "showinfo", lambda t, m, **k: said.append(m)):
            self.app.show_cleaned()
        self.assertIn("Nothing cleaned yet", said[0])

    def test_from_the_list_it_doesnt_ask_again(self):
        st, view = self.scanned(self.video)
        cvplay.remember(st, view, support.APP_DATA)
        played, asked = [], []
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play",
                                  lambda e, *a, **k: played.append(e) or FakeProc()), \
                mock.patch.object(ui.cvplay, "Remote", FakeRemote), \
                mock.patch.object(ui.messagebox, "askyesnocancel",
                                  lambda *a, **k: asked.append(a)):
            self.app.watch_files([str(self.video)], clean=True)
            for _ in range(1000):
                self.app.update()
                w = self.app.watching
                if played and w is not None and w.remote is not None:
                    break
                self.app.after(10)
        self.assertEqual(asked, [])
        self.assertEqual(played[0][0]["skips"], [[30.0, 38.0]])


class YearOldLeftovers(unittest.TestCase):
    """1.0.12 (the owner, 2026-11-14: "do you think we should add an expiry on the log so it
    doesn't fill up? like a year", then yes to keeping the logs for good and clearing only the
    scan leftovers of videos not played or opened in a year)."""
    DAY = 86400

    def setUp(self):
        self.views = support.APP_DATA / "views"
        shutil.rmtree(self.views, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.views, True)
        self.addCleanup(lambda: (support.APP_DATA / cvplay.INDEX_NAME).unlink(missing_ok=True))
        self.dir = Path(tempfile.mkdtemp(prefix="cv-tidy-", dir=support.SANDBOX))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def scanned(self, name, used_days_ago=None):
        """A scanned video's folder, as a scan leaves it."""
        video = self.dir / f"{name}.mkv"
        video.write_bytes(os.urandom(50_000))
        st = st_with(path=str(video), duration=100.0)
        view = self.views / name
        (view / "thumbs").mkdir(parents=True)
        (view / "sheets").mkdir()
        (view / "analysis.json").write_text(json.dumps(st), encoding="utf8")
        (view / "thumbs" / "s1.png").write_bytes(b"p" * 100)
        (view / "sheets" / "sheet_1.jpg").write_bytes(b"j" * 5000)
        (view / "transcript.json").write_text("{}" + " " * 3000)
        (view / "preview_30_38.mkv").write_bytes(b"v" * 4000)
        if used_days_ago is not None:
            cvplay.remember(st, name, support.APP_DATA)
            idx = cvplay.load_index(support.APP_DATA)
            for e in idx.values():
                if e["view"] == name:
                    e["used"] = time.time() - used_days_ago * self.DAY
            cvplay._save_index(idx, support.APP_DATA)
        return video, view

    def left(self, view):
        return sorted(p.name for p in view.iterdir())

    def test_a_year_unused_keeps_only_the_log(self):
        video, view = self.scanned("old", used_days_ago=400)
        freed = cvplay.tidy_leftovers(self.views)
        self.assertGreaterEqual(freed, 12000)
        self.assertEqual(self.left(view), ["analysis.json", "thumbs", "tidied.txt"])
        self.assertTrue((view / "thumbs" / "s1.png").exists())
        st = cvplay.cleaned_before(str(video), self.views)          # still plays clean
        self.assertEqual(cvplay.clean_entry(st)["skips"], [[30.0, 38.0]])
        self.assertEqual(cvplay.tidy_leftovers(self.views), 0, "nothing more to clear")

    def test_played_or_opened_within_the_year_is_left_alone(self):
        _, view = self.scanned("recent", used_days_ago=300)
        self.assertEqual(cvplay.tidy_leftovers(self.views), 0)
        self.assertIn("sheets", self.left(view))

    def test_the_open_review_and_one_being_checked_are_left_alone(self):
        _, kept = self.scanned("open review", used_days_ago=400)
        _, busy = self.scanned("being checked", used_days_ago=400)
        (busy / "queue.lock").write_text("")
        self.assertEqual(cvplay.tidy_leftovers(self.views, keep_views={"open review"}), 0)
        self.assertIn("sheets", self.left(kept))
        self.assertIn("sheets", self.left(busy))

    def test_not_on_the_list_goes_by_when_its_log_was_written(self):
        _, view = self.scanned("unlisted")
        old = time.time() - 400 * self.DAY
        os.utime(view / "analysis.json", (old, old))
        self.assertGreater(cvplay.tidy_leftovers(self.views), 0)
        self.assertEqual(self.left(view), ["analysis.json", "thumbs", "tidied.txt"])

    def test_scanned_again_later_can_be_cleared_again(self):
        _, view = self.scanned("again", used_days_ago=400)
        cvplay.tidy_leftovers(self.views)
        (view / "transcript.json").write_text("{}")             # a second scan, long ago
        self.assertGreater(cvplay.tidy_leftovers(self.views), 0)

    def test_playing_counts_as_used(self):
        video, view = self.scanned("watched", used_days_ago=400)
        cvplay.mark_used(["watched"], support.APP_DATA)
        self.assertEqual(cvplay.tidy_leftovers(self.views), 0)

    def test_found_through_the_list_says_which_log(self):
        video, view = self.scanned("which", used_days_ago=1)
        self.assertEqual(cvplay.cleaned_before(str(video), self.views)["_view"], "which")


class OpenWith(unittest.TestCase):
    """File Explorer's "Open with CleanView Play" (cvopenwith), with a stand-in registry:
    the tests never touch the real one."""
    def setUp(self):
        import cvopenwith
        self.ow = cvopenwith
        self.root = Path(tempfile.mkdtemp(prefix="cv-ow-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "app").mkdir()
        (self.root / "venv" / "Scripts").mkdir(parents=True)
        (self.root / "venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
        self.reg = FakeReg()

    def test_added_for_every_kind_never_the_default(self):
        self.assertTrue(self.ow.register(self.root / "app", self.reg))
        keys = self.reg.keys
        cmd = keys[r"Software\Classes\CleanViewPlay.Media\shell\open\command"][""]
        self.assertEqual(cmd, f'"{self.root / "venv" / "Scripts" / "pythonw.exe"}" '
                              f'"{self.root / "app" / "launcher.pyw"}" "%1"')
        for kind in (".mkv", ".mp4", ".mp3"):
            self.assertEqual(keys[rf"Software\Classes\{kind}\OpenWithProgids"],
                             {"CleanViewPlay.Media": ""})
            self.assertNotIn(rf"Software\Classes\{kind}", keys, "the default is left alone")
        self.assertFalse(self.ow.register(self.root / "app", self.reg), "nothing changed")

    def test_a_new_icon_shown_by_a_new_name(self):
        # CleanView 1.18.107's slight 3D look: Windows keeps an icon's picture by its file name
        app = self.root / "app"
        (app / "cleanview.ico").write_bytes(b"old icon")
        self.ow.register(app, self.reg)
        key = r"Software\Classes\CleanViewPlay.Media\DefaultIcon"
        first = Path(self.reg.keys[key][""])
        self.assertTrue(first.name.startswith("openwith-"))
        self.assertEqual(first.read_bytes(), b"old icon")
        (app / "cleanview.ico").write_bytes(b"new icon")
        self.assertTrue(self.ow.register(app, self.reg))
        now = Path(self.reg.keys[key][""])
        self.assertNotEqual(now, first)
        self.assertEqual(now.read_bytes(), b"new icon")
        self.assertEqual(list(app.glob("openwith-*.ico")), [now], "the old copy removed")

    def test_its_own_name_when_it_has_its_own_exe(self):
        own = self.root / "venv" / "CleanView Play" / "CleanView Play.exe"
        own.parent.mkdir(parents=True)
        own.write_bytes(b"")
        self.assertTrue(self.ow.command(self.root).startswith(f'"{own}"'))

    def test_only_from_an_installed_copy(self):
        self.assertFalse(self.ow.register(support.ROOT, self.reg))       # the source folder
        self.assertEqual(self.reg.keys, {})
        self.assertIn("cvopenwith.py", json.loads((support.ROOT / "version.json").read_text())
                      ["files"])

    def test_the_launcher_hands_the_file_over(self):
        import importlib.machinery
        import importlib.util
        path = str(support.ROOT / "launcher.pyw")
        spec = importlib.util.spec_from_file_location(
            "cv_launcher", path, loader=importlib.machinery.SourceFileLoader("cv_launcher", path))
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        video = support.SANDBOX / "Sent.mkv"
        video.write_bytes(b"x")
        out = support.SANDBOX / "open_these.json"
        with mock.patch.object(launcher, "OPEN_THESE", out):
            launcher.hand_over([str(video), str(support.SANDBOX / "not there.mkv"),
                                launcher.APP_PART])
        self.assertEqual(json.loads(out.read_text())["paths"], [str(video)])


class OpeningBox(unittest.TestCase):
    """1.0.9 (the owner, after an update: "it didn't say anything to let me know what it was
    doing", "could it have the little opening box that says the program like the others but
    also its updating")."""
    def load(self):
        import importlib.machinery
        import importlib.util
        path = str(support.ROOT / "launcher.pyw")
        spec = importlib.util.spec_from_file_location(
            "cv_launcher2", path, loader=importlib.machinery.SourceFileLoader("cv_launcher2", path))
        lm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lm)
        return lm

    def boxed(self, env):
        lm = self.load()
        seen = {"said": [], "title": [], "top": []}

        class Box(tk.Tk):
            def title(self, *a):
                if a:
                    seen["title"].append(a[0])
                return super().title(*a)

            def attributes(self, *a):
                if a[:1] == ("-topmost",) and len(a) > 1:
                    seen["top"].append(a[1])
                return super().attributes(*a)

        real_var = tk.StringVar

        def var(*a, **k):
            seen["said"].append(k.get("value"))
            return real_var(*a, **k)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
        with mock.patch.dict(os.environ, env), mock.patch.object(tk, "Tk", Box),                 mock.patch.object(tk, "StringVar", var):
            lm.splash(child)
        child.wait()
        return seen

    def test_on_top_and_named_as_opening(self):
        seen = self.boxed({"CLEANVIEW_UPDATED": ""})
        self.assertEqual(seen["title"], ["Opening CleanView"])
        self.assertEqual(seen["top"], [True])
        # since 1.18.128 the box's title and its "Please wait" line are text too
        self.assertIn("This can take a few seconds.", seen["said"])
        self.assertIn("Opening CleanView…", seen["said"])

    def test_says_it_was_just_updated(self):
        seen = self.boxed({"CLEANVIEW_UPDATED": "CleanView was updated to version 9.9.9."})
        self.assertIn("Updated to version 9.9.9.", seen["said"])
        self.assertNotIn("This can take a few seconds.", seen["said"])
        # an older copy's "Update now" still says "CleanView Play was updated" (1.0.71)
        seen = self.boxed({"CLEANVIEW_UPDATED": "CleanView Play was updated to version 9.9.9."})
        self.assertIn("Updated to version 9.9.9.", seen["said"])

    def test_says_its_updating_while_it_downloads(self):
        # 1.18.128: "Updating CleanView Play to 1.0.40... (5 of 23)"
        lm = self.load()
        self.assertEqual(lm.APP_NAME, "CleanView Play")
        src = (support.ROOT / "launcher.pyw").read_text(encoding="utf8")
        self.assertIn('opening(f"{UPDATING} {SHOWN} to {ver}\\u2026 (0 of {len(names)})")', src)


class FakeReg:
    HKEY_CURRENT_USER, REG_SZ, __name__ = "HKCU", 1, "fake"

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, reg, key):
            self.reg, self.key = reg, key

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, key):
        if key not in self.keys:
            raise OSError(key)
        return self._Key(self, key)

    def CreateKey(self, root, key):
        self.keys.setdefault(key, {})
        return self._Key(self, key)

    def QueryValueEx(self, k, name):
        if name not in self.keys[k.key]:
            raise OSError(name)
        return self.keys[k.key][name], self.REG_SZ

    def SetValueEx(self, k, name, _, kind, value):
        self.keys[k.key][name] = value


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

    def test_carries_on_from_a_spot(self):
        lua = cvplay.SCRIPT.read_text(encoding="utf8")
        self.assertIn("current.start", lua)

    def test_its_own_subtitles_when_just_watching(self):
        lua = cvplay.SCRIPT.read_text(encoding="utf8")
        self.assertIn("current.own_subs", lua)
        self.assertIn('"user-data/cvplay/subs"', lua)
        self.assertIn("user-data/cvplay/subs", cvplay.Remote.WATCH)

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

    def test_carrying_on_from_the_same_spot(self):
        e = cvplay.entry(self.sample(scenes=[{"s": 6.0, "e": 8.0, "cut": True}]))
        proc = cvplay.play([e], cvplay.player(), start_at=(0, 4.0),
                           extra=["--vo=null", "--ao=null", "--force-window=no"])
        r = cvplay.Remote(proc.pipe, proc)
        self.addCleanup(r.close)
        self.assertTrue(r.ready.wait(15))
        stop = time.monotonic() + 8
        while time.monotonic() < stop and not (r.state.get("time-pos") or 0) >= 4.0:
            time.sleep(0.05)
        t = r.state.get("time-pos") or 0
        self.assertGreaterEqual(t, 4.0)
        self.assertLess(t, 5.5, "from there, not from the start")

    def test_just_watching_has_its_own_subtitles(self):
        # a video played as it is (nothing muted): its own subtitle track there to turn on
        video = self.tmp / "with subs.mkv"
        srt = self.tmp / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:09,000\nHello there.\n", encoding="utf8")
        subprocess.run([support.FFMPEG, "-v", "error", "-y", "-i", str(self.video), "-i", str(srt),
                        "-map", "0:v", "-map", "0:a:0", "-map", "1", "-c", "copy",
                        "-c:s", "srt", str(video)], check=True)
        proc = cvplay.play([cvplay.plain_entry(video)], cvplay.player(),
                           extra=["--vo=null", "--ao=null", "--force-window=no"])
        r = cvplay.Remote(proc.pipe, proc)
        self.addCleanup(r.close)
        self.assertTrue(r.ready.wait(15))
        r.send("observe_property", 99, "sub-text")

        def until(test, secs=8.0):
            stop = time.monotonic() + secs
            while time.monotonic() < stop:
                if test():
                    return True
                time.sleep(0.05)
            return False
        self.assertTrue(until(lambda: r.state.get("user-data/cvplay/subs") is True), r.state)
        self.assertIn(r.state.get("sid"), (False, "no"), "off at first")
        r.send("script-message", "cvplay-subs")
        self.assertTrue(until(lambda: r.state.get("sub-text") == "Hello there."), r.state)

    @unittest.skipUnless(os.name == "nt", "the picture inside the window is for Windows")
    def test_it_plays_inside_the_window(self):
        # the owner, 2026-11-14: "Play inside CleanView Play". The real player in a window
        # (see-through, as every test window; no sound): its picture sits in the window's
        # picture area and follows it when the window changes size; Stop closes it.
        import ctypes
        from ctypes import wintypes

        class Win(tk.Tk):
            q = queue.Queue()               # what the player start sends back (App._poll)

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
        user32.FindWindowExW.restype = wintypes.HWND
        user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                                         wintypes.LPCWSTR)

        def size(hwnd):
            rc = wintypes.RECT()
            user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rc))
            return rc.right - rc.left, rc.bottom - rc.top

        def pump(test, secs=15.0):
            stop = time.monotonic() + secs
            while time.monotonic() < stop:
                while not root.q.empty():
                    root.q.get()[1]()
                root.update()
                if test():
                    return True
                time.sleep(0.02)
            return False
        area = w.video.winfo_id()
        self.assertTrue(pump(lambda: w.played), "the video started")
        # the player's own window inside the picture area (class "mpv"; the area also holds
        # the "Opening…" message, 1.0.14)
        def mpv_window():
            return user32.FindWindowExW(wintypes.HWND(area), None, "mpv", None)
        self.assertTrue(pump(mpv_window), "the picture")
        child = mpv_window()
        self.assertTrue(pump(lambda: size(child) == size(area)), (size(child), size(area)))
        w.win.geometry("640x480")
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
