"""Refuse library reads while an exclusive reorganization is active."""
from pathlib import Path


class LibraryMaintenanceError(ValueError):
    """The library is temporarily unavailable for readers."""


def require_library_available(root: Path, error_type=LibraryMaintenanceError) -> None:
    lock = Path(root) / ".library-reorganization.lock"
    if lock.exists() or lock.is_symlink():
        raise error_type("模板库正在整理维护，请在迁移完成后重试 (library maintenance in progress)")
