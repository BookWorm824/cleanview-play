"""CleanView Play seals its updates with its OWN key (2026-11-19), not CleanView's: it is its
own app updating from its own repo, so a CleanView release can never be taken as a Play one."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLEANVIEW_KEY = bytes.fromhex("8cf7d7b15dc080a415e1d6d799ea5e5e7f5201d273ec6b4fca6f9504ed878cb5")
PLAY_KEY = bytes.fromhex("5c11b8528b5c579a84b64e9bb1d5d3590c2116623147d5477fb4deb1c3818d0e")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class PlaysOwnSeal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = load("play_launcher_seal", REPO / "launcher.pyw")
        cls.sr = load("play_sign_release", REPO / "tools" / "sign_release.py")

    def test_plays_own_key_not_cleanviews(self):
        self.assertEqual(self.lm.PUBLIC_KEY, PLAY_KEY)
        self.assertNotEqual(self.lm.PUBLIC_KEY, CLEANVIEW_KEY)

    def test_plays_own_seal_name(self):
        self.assertEqual(self.lm.APP_NAME, "CleanView Play")
        self.assertEqual(self.lm.SEAL_FOR, "CleanView Play update\n")

    def test_signing_tool_uses_plays_key_file(self):
        self.assertEqual(self.sr.APP, "CleanView Play")
        self.assertEqual(self.sr.KEY_FILE.name, "CleanView Play update key - SECRET.txt")


if __name__ == "__main__":
    unittest.main()
