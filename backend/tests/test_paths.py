from pathlib import Path

from utils.paths import confined_file


def test_confined_file_accepts_file_below_root(tmp_path: Path):
    root = tmp_path / "dist"
    root.mkdir()
    asset = root / "favicon.svg"
    asset.write_text("ok", encoding="utf-8")

    assert confined_file(root, "favicon.svg") == asset.resolve()


def test_confined_file_rejects_parent_traversal(tmp_path: Path):
    root = tmp_path / "dist"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    assert confined_file(root, "../secret.txt") is None
    assert confined_file(root, "../../secret.txt") is None


def test_confined_file_rejects_absolute_path(tmp_path: Path):
    root = tmp_path / "dist"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    assert confined_file(root, str(secret.resolve())) is None


def test_confined_file_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "dist"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        return  # Windows may not permit symlinks in the local test environment.

    assert confined_file(root, "link.txt") is None
