"""CleanView Play (the owner, 2026-11-13: "the play only clean view no saving", then "add a
play now feature for now"): "Play now" on the review page plays the original with what's
ticked muted and cut, through the mpv player and cvplay.lua. No copy is written.

The mutes, cuts and quieter music must be exactly what CleanView's render() would have put
in a clean copy, since the finding is CleanView's own."""
import json
import os
import shutil
import subprocess
import tempfile
import tkinter as tk
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
        self.assertEqual(cmd[-1], str(video))
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
        played = []
        with mock.patch.object(ui.cvplay, "player", lambda: Path("mpv.exe")), \
                mock.patch.object(ui.cvplay, "play", lambda entries, exe=None: played.append(
                    (entries, exe))):
            self.app._play_from_review()
            for _ in range(200):
                self.app.update()
                if played and self.app.play_lbl.cget("text") == ui.PLAYING_SAID:
                    break
                self.app.after(10)
        return played

    def test_play_now_instead_of_save(self):
        names = self.buttons()
        self.assertIn("\u25b6  Play now", names)
        self.assertFalse([n for n in names if "Save clean" in n])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAY_SAID)

    def test_play_now_plays_what_is_ticked(self):
        played = self.press_play()
        self.assertEqual(len(played), 1)
        entries, exe = played[0]
        self.assertEqual(exe, Path("mpv.exe"))
        self.assertEqual(entries[0]["skips"], [[30.0, 38.0]])
        self.assertIn("between(t,14.200,14.500)", entries[0]["af"])
        self.assertEqual(self.app.play_lbl.cget("text"), ui.PLAYING_SAID)
        self.assertEqual(self.app.page_name, "review", "the review stays open")

    def test_a_changed_tick_plays_with_the_change(self):
        for v, j, sc in self.app.scene_vars:
            v.set(sc["s"] == 60.0)              # the kiss ticked, the fight unticked
        for v, j, m in self.app.word_vars:
            v.set(False)
        entries, _ = self.press_play()[0]
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


ttk_button = ui.ttk.Button


class Script(unittest.TestCase):
    def test_the_player_script_reads_the_list_it_is_given(self):
        lua = cvplay.SCRIPT.read_text(encoding="utf8")
        self.assertIn('os.getenv("CVPLAY_LIST")', lua)
        self.assertEqual(cvplay.LIST_ENV, "CVPLAY_LIST")
        for prop in ('"af"', '"vf"', '"aid"', '"time-pos"', '"absolute+exact"'):
            self.assertIn(prop, lua)


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
        # at the jump a frame or two of black may show (the cut's cover), never its picture
        self.assertLessEqual(len(sizes) - len(picture), 3)


if __name__ == "__main__":
    unittest.main()
