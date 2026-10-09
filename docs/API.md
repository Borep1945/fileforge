# Python API reference

All imports below are from `fileforge.core`. Functions accept `pathlib.Path` roots
and operate on regular files. The selected root resolves to an existing directory;
descendant symlinks are rejected for changes and skipped during scans.

## Records and scans

`FileRecord(path: str, size: int, sha256: str)` is an immutable content record.
Paths are relative to the root. `ScanResult(files: list[FileRecord],
errors: dict[str, str])` contains deterministic scan records and per-path errors.
`report.duplicates() -> list[list[FileRecord]]` groups equal size/SHA-256 values
and returns only groups containing more than one file. It never deletes files.

`scan(root: Path, *, recursive=True, max_size=1_000_000_000) -> ScanResult` hashes
contents using SHA-256, skips symlinks and `.fileforge-backups`, and records errors
for unreadable or over-limit files. `recursive` must be bool; `max_size` must be a
nonnegative integer. Missing roots and invalid configuration raise exceptions.
The scan reports concurrent changes detected while hashing. Metadata for the
entire inventory remains in memory.

`fingerprint(path: Path) -> str` hashes a regular file, rejects terminal symlinks
where the OS supports O_NOFOLLOW, and checks its size/timestamps before and after
reading. A special file is rejected without waiting for a FIFO writer on systems
supporting O_NONBLOCK. This is not a lock against concurrent modification.

`size_report(root: Path, *, min_size=0, limit=20) -> dict[str, object]` reads
metadata only. It returns `total_files`, `total_bytes`, `largest` and `errors`.
Largest entries contain `path` and `size`, ordered by descending size then path.
The threshold is in bytes. It includes files larger than the hash scan ceiling,
without reading their contents. Metadata can change during traversal.

## Planning

`Rename(source: str, target: str, sha256: str)` is an immutable planned move whose
paths are relative to the root. Every source/target is unique; target collisions
and changed source contents are rejected.

`plan_rename(root: Path, pattern: str, replacement: str) -> list[Rename]` applies
a regular expression to every scanned basename. Names stay in their original
directory. Replacements use Python regex semantics; generated names cannot be
empty, traversal components, paths, or contain control characters.

`plan_sort(root: Path) -> list[Rename]` scans top-level files, then plans each into
a folder named after its lowercase extension. Unknown/unsafe extensions and
extensionless files go to `other`. Existing subtrees remain untouched. Existing
destination files or a non-directory destination parent reject the entire plan.
Like rename planning, sort planning uses the default hash scan size ceiling.

`validate_plan(root: Path, plan: list[Rename]) -> None` verifies relative paths,
symlinks, source hashes, uniqueness, existing target names and target parents.
Pass a resolved existing root when using this lower-level function directly.
`SafetyError` is a `ValueError` subclass for unsafe operations.

## Applying a plan

`apply_plan(root: Path, plan: list[Rename], *, apply=False) -> Path | None` validates
in dry-run mode and returns `None` without changing the filesystem. With explicit
`apply=True`, it copies and verifies backups under
`.fileforge-backups/<transaction>/files/`, writes a separate JSON manifest,
creates required target directories, stages source names beside their originals,
and publishes target names using exclusive hard links. It returns the backup
transaction directory. An empty applied plan also returns `None`.

A successful publish is followed by staging cleanup. A failed publish removes
published links and restores staged names; newly created empty directories are
removed. Hard-link support is required. Backups remain available on failure.
If a concurrent process occupies an original name during rollback, restore fails
instead of overwriting it; manual recovery from retained backups/staging may be
necessary. There is no power-loss recovery, distributed lock or automatic restore.

```python
from pathlib import Path
from fileforge.core import apply_plan, plan_rename, plan_sort, scan, size_report

root = Path("/YOUR_DISPOSABLE_FOLDER")
print(scan(root).duplicates())
print(size_report(root, min_size=10_000_000, limit=10))
rename_plan = plan_rename(root, "^draft_", "final_")
apply_plan(root, rename_plan)  # validates only
# Explicit apply after reviewing the plan:
backup = apply_plan(root, rename_plan, apply=True)
print(backup)
sort_plan = plan_sort(root)
apply_plan(root, sort_plan)  # preview; no directory creation
```

`fileforge.cli.main(argv: list[str] | None = None) -> int` implements the CLI.
Subcommands are `scan`, `duplicates`, `sizes`, `rename` and `sort`. Scan and duplicate
commands accept settings from a `[scan]` TOML section. `--verbose` prints structured
JSON metadata on stderr; results remain on stdout. Exit codes are 0 success,
1 scan/report errors, 2 invalid input or unsafe operation. Parse errors raise
`SystemExit` as with argparse.

See [architecture and recovery](architecture.md) and
[local verification](VERIFICATION.md) for operational boundaries.
