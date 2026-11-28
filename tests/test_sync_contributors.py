"""Deterministic contributor wall rendering tests (no network)."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_contributors as sync


class ContributorWallTests(unittest.TestCase):
    def test_filters_bots_and_deduplicates(self):
        text = sync.render({"aaas72", "aaas72", "TianHengZhuang", "dependabot[bot]", "github-actions[bot]"})
        self.assertEqual(text.count('href="https://github.com/aaas72"'), 1)
        self.assertIn("TianHengZhuang.png?size=80", text)
        self.assertNotIn("dependabot", text)

    def test_stable_sort(self):
        self.assertEqual(sync.render({"zebra", "Alice"}), sync.render({"Alice", "zebra"}))

    def test_idempotent(self):
        base = "before\n" + sync.START + "\nold\n" + sync.END + "\nafter"
        first = sync.update_readme(base, sync.render({"aaas72"}))
        self.assertEqual(sync.update_readme(first, sync.render({"aaas72"})), first)

    def test_missing_markers_fail(self):
        with self.assertRaises(ValueError):
            sync.update_readme("no markers", sync.render({"aaas72"}))

    def test_github_noreply_attribution(self):
        match = sync.NOREPLY.fullmatch("183341550+hammadfarooq-ai@users.noreply.github.com")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "hammadfarooq-ai")
        self.assertIsNone(sync.NOREPLY.fullmatch("unlinked@example.org"))

    def test_login_validation(self):
        self.assertFalse(sync.eligible('"><script>'))
        self.assertFalse(sync.eligible("claude"))
        self.assertTrue(sync.eligible("hammadfarooq-ai"))


if __name__ == "__main__":
    unittest.main()
