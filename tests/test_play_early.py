"""CleanView Play 1.0.44: the videos done can be reviewed while the rest are checked
(CleanView 1.18.132). Play now plays the done ones; a video that finished meanwhile is put
on the page first, so it never plays with nothing ticked."""
import unittest

from test_early_review import ReviewWhileChecking, review
from test_ui import ui


class PlayWhileChecking(ReviewWhileChecking):
    def setUp(self):
        super().setUp()
        self.started = []
        self.real_watching = ui.Watching
        test = self

        class FakeWatching:
            def __init__(self, app, entries, done, **k):
                test.started.append([e["path"] for e in entries])
                self.closing = False

            def winfo_exists(self):
                return True

            def finish(self):
                self.closing = True
        ui.Watching = FakeWatching
        self.real_thread = ui.App._watch_play_freeze
        ui.App._watch_play_freeze = lambda self, videos, secs=90: None

    def tearDown(self):
        ui.Watching = self.real_watching
        ui.App._watch_play_freeze = self.real_thread
        super().tearDown()

    def test_says_play_not_save(self):
        self.app.early_btn.invoke()
        self.settle()
        said = self.app._early_text.cget("text")
        self.assertIn("review and play these while the rest are checked", said)
        self.assertNotIn("saved", said)

    def test_plays_the_done_ones_while_the_rest_are_checked(self):
        app = self.app
        app.early_btn.invoke()
        self.settle()
        good = app._gather_choices(write=False)
        self.assertEqual(len(good), 2)
        app._play_from_review()
        self.settle()
        self.assertEqual(len(self.started), 1)
        self.assertEqual(len(self.started[0]), 2)
        app.watching = None

    def test_one_finished_meanwhile_goes_on_the_page_first(self):
        app = self.app
        app.early_btn.invoke()
        self.settle()
        app.jobs[2]["analysis"] = review("Show_S01E11.mp4")
        app._handle(("for_run", app.run, ("job", 2, "done")))
        self.settle()
        app._play_from_review()
        self.settle()
        self.assertEqual(self.started, [])                  # not played yet
        self.assertEqual(self.told[-1], ui.PLAY_ADDED_FIRST)
        self.assertEqual(len(app._on_page), 3)


del ReviewWhileChecking         # its own tests run from test_early_review.py

if __name__ == "__main__":
    unittest.main()
