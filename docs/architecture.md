# Architecture and recovery

`scan` walks sorted directories, skips symlinks and backups, checks each file's
size, and records SHA-256. Individual read failures remain in `errors`; the CLI
returns exit code 1. Concurrent content changes detected while hashing are errors.

`size_report` reads metadata without hashing contents, so files above the hash
scan ceiling still appear in large-file reports. `plan_rename` changes basenames
only. `plan_sort` organizes top-level files by a normalized extension and leaves
existing subtrees alone. `validate_plan` runs both when a plan is created and
immediately before staging. `apply_plan(..., apply=False)` validates
without writing. Apply copies every source into a UUID-named backup directory,
verifies each backup hash under `files/`, saves a separate `manifest.json`,
creates any required target folders, stages names beside their originals, then
publishes target names with exclusive hard links. Only
when the entire batch is published are staging links removed.

A publish failure removes this transaction's published links and restores source
names. If another process has occupied an original path, exclusive restore fails
instead of overwriting it: backups and remaining staging files are retained.
Inspect the exception, `manifest.json`, and `.fileforge-*` files before recovery.
A power loss is outside the rollback guarantee. Recovery is currently manual:
copy a saved source only after checking that the destination is safe to replace.

Use a quiet directory owned by you. The path checks do not provide a race-proof
sandbox against another process replacing ancestor directories during a batch.
The tool follows the selected root's resolved location and refuses descendant
symlinks. Sorting creates target directories and removes empty directories it
created during a successful rollback. It does not modify ownership or file contents.
