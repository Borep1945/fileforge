"""A complete safe workflow in a disposable directory."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fileforge.core import apply_plan, plan_rename, plan_sort, scan, size_report

with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "draft_notes.txt").write_text("keep this content\n")
    (root / "copy.txt").write_text("keep this content\n")
    print("Duplicate groups:", len(scan(root).duplicates()))
    print("Size report:", size_report(root, min_size=1, limit=2))
    plan = plan_rename(root, "^draft_", "final_")
    print("Plan:", plan)
    print("Dry run:", apply_plan(root, plan))
    backup = apply_plan(root, plan, apply=True)
    print("Verified backup:", backup)
    print("Final contents:", (root / "final_notes.txt").read_text().strip())
    sort_plan = plan_sort(root)
    print("Extension sort plan:", sort_plan)
    apply_plan(root, sort_plan, apply=True)
    print("Sorted files:", [item.path for item in scan(root).files])
