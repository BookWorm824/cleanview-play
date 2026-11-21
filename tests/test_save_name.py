"""CleanView (1.18.157) shows "CleanView Save" wherever people read its name. In CleanView Play
the same code shows Play's own name: launcher.SHOWN is APP_NAME, "CleanView Play", for now (the
owner hasn't chosen yet whether Play is shown as "CleanView"). Folders, files and the update
seal stay "CleanView Play" either way."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (must come first)

import cleanview as engine
import cleanview_learn as learn
import launcher as lm


class Names(unittest.TestCase):
    def test_shown_and_kept(self):
        self.assertEqual(lm.SHOWN, "CleanView Play")
        self.assertEqual(lm.APP_NAME, "CleanView Play")        # the seal and the folder
        self.assertEqual(lm.SEAL_FOR, "CleanView Play update\n")
        self.assertEqual(lm.SHORTCUT, "CleanView Play.lnk")

    def test_the_report_and_notes(self):
        self.assertIn("CleanView Play", engine.TECHNICAL_HEAD)
        self.assertEqual(engine.LINE_LABEL["shared"], "CleanView Play learned")
        st = {"notes": ["The AI helper used x for the picture (CleanView 1.18.150)."],
              "scan": {"app_version": "1.18.150"}}
        # a review made before the rename isn't given its version twice
        self.assertEqual(sum("1.18.150" in n for n in engine.technical_notes(st)), 1)

    def test_a_lesson_by_either_name(self):
        self.assertTrue(learn.same_lesson("CleanView learned this scene",
                                          "CleanView Play learned this scene"))
        self.assertFalse(learn.same_lesson("you kept this scene before",
                                           "CleanView Play learned this scene"))

    def test_task_manager_name(self):
        with tempfile.TemporaryDirectory() as d:
            ico = Path(d) / "x.ico"
            ico.write_bytes(b"icon")
            self.assertTrue(lm._icon_mark(ico).endswith(" CleanView Play"))


@unittest.skipUnless(os.name == "nt", "Windows shortcuts")
class Shortcut(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=support.SANDBOX))
        self.here = self.dir / "app"
        self.here.mkdir()
        self.places, self.named = lm.NAME_PLACES, lm.SHORTCUT_NAMED
        lm.NAME_PLACES = "@('" + str(self.dir / "desk").replace("'", "''") + "')"
        lm.SHORTCUT_NAMED = self.dir / "shortcut_named.txt"
        (self.dir / "desk").mkdir()

    def tearDown(self):
        lm.NAME_PLACES, lm.SHORTCUT_NAMED = self.places, self.named

    def make(self, name, launcher):
        p = self.dir / "desk" / name
        script = ("$s = New-Object -ComObject WScript.Shell; "
                  f"$l = $s.CreateShortcut('{p}'); $l.TargetPath = 'C:\\Windows\\notepad.exe'; "
                  f"$l.Arguments = '\"{launcher}\"'; $l.Save()")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, creationflags=0x08000000)
        return p

    def test_plays_own_shortcut_is_left_alone(self):
        # the same old and new name: CleanView's renamer would have deleted it
        self.make("CleanView Play.lnk", self.here / "launcher.pyw")
        self.make("CleanView.lnk", self.dir / "save" / "launcher.pyw")   # CleanView's own
        self.assertFalse(lm.name_shortcuts(self.here))
        self.assertEqual(sorted(p.name for p in (self.dir / "desk").iterdir()),
                         ["CleanView Play.lnk", "CleanView.lnk"])


if __name__ == "__main__":
    unittest.main()
