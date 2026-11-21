"""CleanView Play is shown as "CleanView" wherever people read it (1.0.71; the owner, 2026-11-21:
"wekk the shortcut and anything people can read should change to cleanview also",
"functtionally file wise it can be clean view play", then "Everywhere readable"); CleanView
itself shows "CleanView Save" (1.18.157). Folders, files, the data sent in and the update seal
stay "CleanView Play"."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (must come first)

import cleanview as engine
import cleanview_learn as learn
import cvopenwith
import cvplay
import launcher as lm


class Names(unittest.TestCase):
    def test_shown_and_kept(self):
        self.assertEqual(lm.SHOWN, "CleanView")
        self.assertEqual(lm.APP_NAME, "CleanView Play")        # the seal and the folder
        self.assertEqual(lm.SEAL_FOR, "CleanView Play update\n")
        self.assertEqual(lm.SHORTCUT, "CleanView.lnk")
        self.assertEqual(lm.OLD_SHORTCUT, "CleanView Play.lnk")
        self.assertEqual(lm.OWN_EXE, "CleanView Play.exe")
        self.assertEqual(cvopenwith.SHOWN, "CleanView")
        self.assertEqual(cvopenwith.APP_NAME, "CleanView Play")

    def test_what_people_read(self):
        self.assertEqual(engine.TECHNICAL_HEAD.split("'")[0], "For CleanView")
        self.assertEqual(engine.LINE_LABEL["shared"], "CleanView learned")
        self.assertIn("CleanView has learned", engine.LESSONS_ADDED)

    def test_working_names_kept(self):
        self.assertTrue(str(cvplay.APP_DATA).endswith("CleanView Play"))
        self.assertTrue(str(learn.APP_DATA).endswith("CleanView Play"))
        self.assertEqual(cvplay.BESIDE_WHAT, "CleanView Play log")   # a tag other PCs read

    def test_a_lesson_by_either_name(self):
        # what Play wrote before (it never released "CleanView Play learned this scene")
        self.assertTrue(learn.same_lesson("CleanView learned this scene", learn.TAUGHT_SCENE))
        self.assertFalse(learn.same_lesson("you kept this scene before",
                                           "CleanView learned this scene"))

    def test_task_manager_name(self):
        with tempfile.TemporaryDirectory() as d:
            ico = Path(d) / "x.ico"
            ico.write_bytes(b"icon")
            self.assertTrue(lm._icon_mark(ico).endswith(" CleanView"))


@unittest.skipUnless(os.name == "nt", "Windows windows")
class FindsItsOwnWindow(unittest.TestCase):
    """The launcher finds the open window by title: "CleanView 1.0.71", never CleanView's
    own "CleanView Save 1.18.157"."""
    def test_name_and_version_only(self):
        import ctypes
        import tkinter as tk
        r = tk.Tk()
        try:
            r.withdraw()
            for title, found in (("CleanView Save 1.18.157", False), ("CleanView 1.0.71", True),
                                 ("CleanView", True), ("CleanViewer", False)):
                r.title(title)
                r.deiconify()
                r.attributes("-alpha", 0.0)
                r.update()
                hwnd = ctypes.windll.user32.GetParent(r.winfo_id()) or r.winfo_id()
                # windows_of skips this process's own windows, so ask it as another would
                real = os.getpid
                try:
                    os.getpid = lambda: -1
                    got = hwnd in lm.windows_of(title="CleanView")
                finally:
                    os.getpid = real
                self.assertEqual(got, found, title)
                r.withdraw()
        finally:
            r.destroy()


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

    def names(self):
        return sorted(p.name for p in (self.dir / "desk").iterdir())

    def test_ours_becomes_cleanview(self):
        self.make("CleanView Play.lnk", self.here / "launcher.pyw")
        self.assertTrue(lm.name_shortcuts(self.here))
        self.assertEqual(self.names(), ["CleanView.lnk"])
        self.assertFalse(lm.name_shortcuts(self.here))           # once

    def test_waits_while_cleanviews_own_is_there(self):
        # CleanView (Save) not yet updated: its "CleanView.lnk" is never touched
        save = self.make("CleanView.lnk", self.dir / "save" / "launcher.pyw")
        self.make("CleanView Play.lnk", self.here / "launcher.pyw")
        self.assertFalse(lm.name_shortcuts(self.here))
        self.assertEqual(self.names(), ["CleanView Play.lnk", "CleanView.lnk"])
        self.assertFalse(lm.SHORTCUT_NAMED.exists())             # tried again next time
        # CleanView renames its own to "CleanView Save.lnk"; Play's turn comes
        os.replace(save, save.with_name("CleanView Save.lnk"))
        self.assertTrue(lm.name_shortcuts(self.here))
        self.assertEqual(self.names(), ["CleanView Save.lnk", "CleanView.lnk"])

    def test_an_old_one_goes_when_ours_is_there(self):
        self.make("CleanView Play.lnk", self.here / "launcher.pyw")
        self.make("CleanView.lnk", self.here / "launcher.pyw")
        self.assertTrue(lm.name_shortcuts(self.here))
        self.assertEqual(self.names(), ["CleanView.lnk"])

    def test_another_installs_shortcut_left(self):
        self.make("CleanView Play.lnk", self.dir / "other" / "launcher.pyw")
        lm.name_shortcuts(self.here)
        self.assertEqual(self.names(), ["CleanView Play.lnk"])


if __name__ == "__main__":
    unittest.main()
