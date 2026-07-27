"""Helpers for safely resolving user-controlled paths."""
from pathlib import Path


def confined_file(root: Path, requested_path: str) -> Path | None:
    """Return an existing file below *root*, or ``None`` if it escapes the root.

    ``Path.resolve`` also resolves symlinks, so a symlink placed below the static
    directory cannot be used to expose a file elsewhere in the container.
    """
    try:
        resolved_root = root.resolve()
        candidate = (resolved_root / requested_path).resolve()
    except OSError:
        return None
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
