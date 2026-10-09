"""Read-only scanning and a preflighted, backed-up rename transaction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

LOGGER = logging.getLogger(__name__)


def _event(event: str, **metadata: object) -> None:
    LOGGER.info(json.dumps({"event": event, **metadata}))


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str


class SizeRecord(TypedDict):
    path: str
    size: int


@dataclass(frozen=True)
class ScanResult:
    files: list[FileRecord]
    errors: dict[str, str]

    def duplicates(self) -> list[list[FileRecord]]:
        groups: dict[tuple[int, str], list[FileRecord]] = {}
        for item in self.files:
            groups.setdefault((item.size, item.sha256), []).append(item)
        return [items for items in groups.values() if len(items) > 1]


@dataclass(frozen=True)
class Rename:
    source: str
    target: str
    sha256: str


class SafetyError(ValueError):
    """The requested operation cannot be applied without risking existing files."""


def fingerprint(path: Path) -> str:
    """Hash a regular file without following a terminal symlink; detect concurrent edits."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SafetyError(f"Not a regular file: {path}")
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise SafetyError(f"File changed while hashing: {path}")
    return digest


def scan(root: Path, *, recursive: bool = True, max_size: int = 1_000_000_000) -> ScanResult:
    """Return deterministic SHA-256 records, reporting unreadable files individually."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("root must be a directory")
    if type(max_size) is not int or max_size < 0 or type(recursive) is not bool:
        raise ValueError("max_size must be a nonnegative integer and recursive must be boolean")
    files: list[FileRecord] = []
    errors: dict[str, str] = {}

    def walk_error(exc: OSError) -> None:
        path = Path(exc.filename or root)
        errors[str(path.relative_to(root))] = str(exc)

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = (
            sorted(
                d
                for d in dirs
                if d != ".fileforge-backups" and not (Path(directory) / d).is_symlink()
            )
            if recursive
            else []
        )
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
                if size > max_size:
                    errors[relative] = "size limit exceeded"
                    continue
                files.append(FileRecord(relative, size, fingerprint(path)))
            except (OSError, SafetyError) as exc:
                errors[relative] = str(exc)
    _event(
        "scan_completed",
        files=len(files),
        bytes=sum(item.size for item in files),
        errors=len(errors),
    )
    return ScanResult(files, errors)


def size_report(root: Path, *, min_size: int = 0, limit: int = 20) -> dict[str, object]:
    """Report aggregate size plus the largest files above a caller-selected threshold."""
    if type(min_size) is not int or min_size < 0 or type(limit) is not int or limit < 1:
        raise ValueError("min_size must be nonnegative and limit must be a positive integer")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("root must be a directory")
    files: list[SizeRecord] = []
    errors: dict[str, str] = {}

    def walk_error(exc: OSError) -> None:
        path = Path(exc.filename or root)
        errors[str(path.relative_to(root))] = str(exc)

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(
            d for d in dirs if d != ".fileforge-backups" and not (Path(directory) / d).is_symlink()
        )
        for name in names:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                info = path.stat()
                if stat.S_ISREG(info.st_mode):
                    files.append({"path": relative, "size": info.st_size})
            except OSError as exc:
                errors[relative] = str(exc)
    largest = sorted(
        (item for item in files if item["size"] >= min_size),
        key=lambda item: (-item["size"], item["path"]),
    )[:limit]
    total = sum(item["size"] for item in files)
    _event("size_report_completed", files=len(files), bytes=total, errors=len(errors))
    return {"total_files": len(files), "total_bytes": total, "largest": largest, "errors": errors}


def _safe_path(root: Path, relative: str) -> Path:
    component = Path(relative)
    if component.is_absolute() or ".." in component.parts or not component.parts:
        raise SafetyError("Paths must stay inside the selected root")
    current = root
    for part in component.parts:
        current = current / part
        if current.is_symlink():
            raise SafetyError(f"Symlink paths are not supported: {relative}")
    if not current.resolve().is_relative_to(root):
        raise SafetyError(f"Path escapes root: {relative}")
    return current


def validate_plan(root: Path, plan: list[Rename]) -> None:
    sources = {item.source for item in plan}
    targets = {item.target for item in plan}
    if len(sources) != len(plan) or len(targets) != len(plan):
        raise SafetyError("Rename sources and targets must be unique")
    for item in plan:
        source = _safe_path(root, item.source)
        target = _safe_path(root, item.target)
        if not source.is_file() or source == target:
            raise SafetyError(f"Invalid source: {item.source}")
        for ancestor in target.parents:
            if ancestor == root:
                break
            if ancestor.exists() and not ancestor.is_dir():
                raise SafetyError(f"Target parent is not a directory: {item.target}")
        if target.exists() and item.target not in sources:
            raise SafetyError(f"Target already exists: {item.target}")
        if fingerprint(source) != item.sha256:
            raise SafetyError(f"Source changed since planning: {item.source}")


def plan_rename(root: Path, pattern: str, replacement: str) -> list[Rename]:
    """Apply a regular expression to basenames and preflight the entire batch."""
    root = root.resolve(strict=True)
    regex = re.compile(pattern)
    result = scan(root)
    if result.errors:
        raise SafetyError(f"Scan has errors: {result.errors}")
    plan: list[Rename] = []
    for record in result.files:
        source = Path(record.path)
        name = regex.sub(replacement, source.name)
        if (
            name in {"", ".", ".."}
            or "/" in name
            or "\\" in name
            or any(ord(character) < 32 for character in name)
        ):
            raise SafetyError("Replacement must produce a plain filename")
        if source.name != name:
            plan.append(Rename(record.path, source.with_name(name).as_posix(), record.sha256))
    validate_plan(root, plan)
    return plan


def plan_sort(root: Path) -> list[Rename]:
    """Plan top-level files into extension directories; leave existing subtrees alone."""
    root = root.resolve(strict=True)
    report = scan(root, recursive=False)
    if report.errors:
        raise SafetyError(f"Scan has errors: {report.errors}")
    plan = []
    for item in report.files:
        source = Path(item.path)
        extension = source.suffix.lstrip(".").lower()
        category = extension if extension and re.fullmatch(r"[a-z0-9_-]+", extension) else "other"
        plan.append(Rename(item.path, f"{category}/{source.name}", item.sha256))
    validate_plan(root, plan)
    return plan


def apply_plan(root: Path, plan: list[Rename], *, apply: bool = False) -> Path | None:
    """Dry-run by default. On apply, create backups and stage all source names first."""
    root = root.resolve(strict=True)
    validate_plan(root, plan)
    if not apply or not plan:
        _event("dry_run_completed", operations=len(plan))
        return None
    backup_root = root / ".fileforge-backups"
    if backup_root.is_symlink():
        raise SafetyError("Backup directory must not be a symlink")
    transaction = uuid.uuid4().hex
    backup = backup_root / transaction
    backup.mkdir(parents=True, exist_ok=False)
    for item in plan:
        saved = backup / "files" / item.source
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_safe_path(root, item.source), saved)
        if fingerprint(saved) != item.sha256:
            raise SafetyError(f"Backup verification failed: {item.source}")
    manifest = {"root": str(root), "renames": [asdict(item) for item in plan]}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    validate_plan(root, plan)
    staged: dict[str, Path] = {}
    published: list[Path] = []
    created_directories: list[Path] = []
    try:
        for item in plan:
            parent = (root / item.target).parent
            missing = []
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_directories.append(directory)
        validate_plan(root, plan)
        _event("backup_verified", transaction=transaction, operations=len(plan))
        for index, item in enumerate(plan):
            source = root / item.source
            temporary = source.with_name(f".fileforge-{transaction}-{index}")
            if temporary.exists() or temporary.is_symlink():
                raise SafetyError("Staging path unexpectedly exists")
            source.rename(temporary)
            staged[item.source] = temporary
        for item in plan:
            # A hard-link publish fails atomically if a target appeared after preflight.
            target = root / item.target
            os.link(staged[item.source], target)
            published.append(target)
    except BaseException:
        for target in published:
            target.unlink()
        for source, temporary in staged.items():
            # Never overwrite a file introduced by another process during rollback.
            os.link(temporary, root / source)
            temporary.unlink()
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass  # A directory occupied by another process must be preserved.
        raise
    for temporary in staged.values():
        temporary.unlink()
    _event("transaction_completed", transaction=transaction, operations=len(plan))
    return backup
