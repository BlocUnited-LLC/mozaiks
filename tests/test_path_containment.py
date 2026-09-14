"""Containment policy tests. One owner, so one suite.

Previously this policy was implemented five times and tested three times; the
copies had already drifted to three different exception types.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.utils.path_containment import (
    contains_symlink_component,
    is_relative_to,
    resolve_inside,
)


class TestIsRelativeTo:
    def test_child_under_parent(self):
        assert is_relative_to(Path("/a/b/c"), Path("/a/b")) is True

    def test_identical_paths(self):
        assert is_relative_to(Path("/a/b"), Path("/a/b")) is True

    def test_sibling_is_outside(self):
        assert is_relative_to(Path("/a/sib"), Path("/a/b")) is False

    def test_parent_is_not_inside_child(self):
        assert is_relative_to(Path("/a"), Path("/a/b")) is False

    def test_prefix_collision_is_not_containment(self):
        # /a/bc must not count as inside /a/b on a string prefix match.
        assert is_relative_to(Path("/a/bc"), Path("/a/b")) is False


class TestResolveInside:
    def test_returns_resolved_child_when_contained(self, tmp_path):
        child = tmp_path / "inner" / "file.txt"
        child.parent.mkdir(parents=True)
        assert resolve_inside(tmp_path, child) == child.resolve()

    def test_rejects_escape(self, tmp_path):
        with pytest.raises(ValueError):
            resolve_inside(tmp_path, tmp_path.parent / "escape")

    def test_rejects_dotdot_traversal(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ValueError):
            resolve_inside(root, root / ".." / "escape")

    def test_caller_supplied_error_type_is_raised(self, tmp_path):
        class PromotionError(Exception):
            pass

        with pytest.raises(PromotionError, match="custom message"):
            resolve_inside(
                tmp_path,
                tmp_path.parent / "escape",
                error=PromotionError,
                message="custom message",
            )


class TestContainsSymlinkComponent:
    def test_plain_path_has_no_symlink(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")
        assert contains_symlink_component(tmp_path, "a/f.txt") is False

    def test_symlinked_root_is_detected(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this host")
        assert contains_symlink_component(link, "anything") is True

    def test_symlink_partway_down_is_detected(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        try:
            (root / "hop").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this host")
        # The escape is mid-path: a resolve-then-compare check would miss it.
        assert contains_symlink_component(root, "hop/file.txt") is True

    def test_missing_components_are_not_symlinks(self, tmp_path):
        assert contains_symlink_component(tmp_path, "does/not/exist") is False
