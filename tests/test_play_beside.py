"""The log goes with the movie (CleanView Play 1.0.68; the owner, 2026-11-21: "want to put my
movie on a usb to watch at a friends house and they also have cleanview play. does the log go
with the movie?", then yes to saving it next to the movie). "<movie>.cleanview" sits in the
movie's folder; another PC's CleanView Play finds it and plays the movie clean, no scan."""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import support
import test_ui

import cvplay

ui = test_ui.ui


def made_up_video(folder, name="Family Movie.mp4", fill=b"a"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(fill * 300_000)
    return path


def review_of(video):
    st = support.sample_review()
    st["path"] = str(video)
    st["info"] = dict(st["info"], path=str(video))
    st["asr"] = [{"w": "word", "s": 1.0, "e": 1.2}]
    st["voices"] = {"made": "up"}
    return st


class LogBesideTheMovie(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="beside-", dir=support.SANDBOX))
        self.usb = self.root / "USB stick" / "Movies"
        self.video = made_up_video(self.usb)
        self.mine = self.root / "my PC" / "views"          # this PC's own logs
        self.friends = self.root / "friend's PC" / "views"  # another PC's, empty

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_saved_next_to_the_movie_without_this_pcs_paths(self):
        self.assertTrue(cvplay.write_beside(review_of(self.video)))
        log = self.usb / "Family Movie.cleanview"
        d = json.loads(log.read_text(encoding="utf8"))
        self.assertEqual(d["fingerprint"], cvplay.fingerprint(self.video))
        self.assertEqual(d["analysis"]["path"], "Family Movie.mp4")
        self.assertEqual(d["analysis"]["info"]["path"], "Family Movie.mp4")
        self.assertNotIn(str(self.root), log.read_text(encoding="utf8"))
        self.assertNotIn("asr", d["analysis"])
        self.assertNotIn("voices", d["analysis"])

    def test_not_written_again_when_nothing_changed(self):
        st = review_of(self.video)
        cvplay.write_beside(st)
        log = self.usb / "Family Movie.cleanview"
        old = time.time() - 1000
        os.utime(log, (old, old))
        self.assertTrue(cvplay.write_beside(st))
        self.assertEqual(log.stat().st_mtime, old)
        st["scenes"][1]["cut"] = True                       # a tick changed: written again
        cvplay.write_beside(st)
        self.assertNotEqual(log.stat().st_mtime, old)

    def test_a_friends_pc_plays_it_clean_with_no_scan(self):
        cvplay.write_beside(review_of(self.video))
        there = made_up_video(self.root / "friend's PC" / "Videos")   # copied over
        shutil.copy(self.usb / "Family Movie.cleanview", there.parent)
        st = cvplay.cleaned_before(there, self.friends)
        self.assertIsNotNone(st)
        self.assertEqual(st["path"], str(there))
        self.assertEqual(cvplay.clean_entry(st)["skips"], [[30.0, 38.0]])  # her ticks
        # kept as the friend's own: found again with the log file left behind
        (there.parent / "Family Movie.cleanview").unlink()
        self.assertIsNotNone(cvplay.cleaned_before(there, self.friends))
        listed = cvplay.cleaned_list(self.friends.parent)
        self.assertEqual([e["title"] for e in listed], ["Family Movie"])

    def test_a_renamed_movie_still_finds_its_log(self):
        cvplay.write_beside(review_of(self.video))
        renamed = self.video.with_name("Movie night.mp4")
        os.replace(self.video, renamed)
        self.assertIsNotNone(cvplay.cleaned_before(renamed, self.friends))

    def test_never_used_for_a_different_video(self):
        cvplay.write_beside(review_of(self.video))
        other = made_up_video(self.root / "elsewhere", fill=b"b")      # same name, not the same
        shutil.copy(self.usb / "Family Movie.cleanview", other.parent)
        self.assertIsNone(cvplay.cleaned_before(other, self.friends))
        self.assertFalse(self.friends.exists())

    def test_this_pcs_own_log_comes_first(self):
        st = review_of(self.video)
        view = cvplay.engine_key(str(self.video))
        (self.mine / view).mkdir(parents=True)
        mine = dict(st, scenes=[dict(sc, cut=False) for sc in st["scenes"]])  # her own ticks here
        (self.mine / view / "analysis.json").write_text(json.dumps(mine), encoding="utf8")
        cvplay.remember(mine, view, self.mine.parent)
        cvplay.write_beside(st)                                        # a different one beside
        got = cvplay.cleaned_before(self.video, self.mine)
        self.assertEqual(cvplay.clean_entry(got)["skips"], [])

    def test_a_drive_it_cant_write_to_is_fine(self):
        st = review_of(self.root / "no such folder" / "Gone.mp4")
        self.assertFalse(cvplay.write_beside(st))
        self.assertFalse(cvplay.write_beside({"path": None}))
        self.assertIsNone(cvplay.read_beside(self.root / "nothing.mp4"))

    def test_play_now_saves_it_next_to_the_movie(self):
        # _write_session -> _remember_cleaned, in the background
        job = {"work": str(self.root / "work" / "abc"), "analysis": review_of(self.video)}
        ui.App._remember_cleaned(object(), [job])
        log = self.usb / "Family Movie.cleanview"
        for _ in range(100):
            if log.exists():
                break
            time.sleep(0.05)
        self.assertTrue(log.exists(), "the log wasn't saved next to the movie")


if __name__ == "__main__":
    unittest.main()
