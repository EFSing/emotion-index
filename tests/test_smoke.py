import tempfile
import unittest
from pathlib import Path

from emotion_index.app.cli import run_smoke
from emotion_index.domain.models import EmotionRegime
from emotion_index.integrations.storage import atomic_publish_html, load_snapshot_fixture
from emotion_index.sentiment.scorer import compute_emotion_index
from emotion_index.sources.filter import filter_point_in_time_posts

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "configs" / "smoke_demo.json"


class SmokePipelineTests(unittest.TestCase):
    def test_point_in_time_filtering_excludes_late_available_posts(self):
        window, posts = load_snapshot_fixture(FIXTURE)
        audit = filter_point_in_time_posts(window, posts)
        self.assertEqual(audit.total_input_posts, 4)
        self.assertEqual(len(audit.eligible_posts), 3)
        self.assertEqual(audit.late_unavailable_count, 1)
        self.assertEqual(audit.out_of_window_count, 0)

        snapshot = compute_emotion_index(window, audit)
        self.assertEqual(snapshot.eligible_post_count, 3)
        self.assertGreater(snapshot.emotion_index, 50.0)
        self.assertIn(snapshot.regime, {EmotionRegime.GREED, EmotionRegime.EXTREME_GREED})

    def test_smoke_cli_is_idempotent_and_rejects_conflicting_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            first = run_smoke(FIXTURE, out_dir)
            self.assertTrue(first.exists())
            second = run_smoke(FIXTURE, out_dir)
            self.assertEqual(first, second)

            with self.assertRaises(FileExistsError):
                atomic_publish_html(first, "<html>conflicting content</html>")


if __name__ == "__main__":
    unittest.main()
