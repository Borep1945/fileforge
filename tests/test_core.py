from dataclasses import replace
from pathlib import Path

import pytest

from fileforge.cli import main
from fileforge.core import Rename, SafetyError, apply_plan, fingerprint, plan_rename, scan


def test_sha_duplicates_size_filter_and_determinism(tmp_path):
    (tmp_path / "a.txt").write_text("same")
    (tmp_path / "b.txt").write_text("same")
    (tmp_path / "empty").write_bytes(b"")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "large").write_bytes(b"12345")
    report = scan(tmp_path, max_size=4)
    assert [item.path for item in report.files] == ["a.txt", "b.txt", "empty"]
    assert len(report.duplicates()) == 1
    assert report.errors == {"nested/large": "size limit exceeded"}
    assert scan(tmp_path, recursive=False).errors == {}


def test_symlink_outside_root_is_skipped(tmp_path):
    (tmp_path / "link").symlink_to(Path(__file__))
    assert scan(tmp_path).files == []


def test_dry_run_apply_and_backups(tmp_path):
    (tmp_path / "draft.txt").write_text("original")
    plan = plan_rename(tmp_path, "draft", "final")
    assert apply_plan(tmp_path, plan) is None
    assert (tmp_path / "draft.txt").exists()
    backup = apply_plan(tmp_path, plan, apply=True)
    assert (tmp_path / "final.txt").read_text() == "original"
    assert (backup / "files" / "draft.txt").read_text() == "original"
    assert (backup / "manifest.json").exists()
    assert [item.path for item in scan(tmp_path).files] == ["final.txt"]


def test_swap_preserves_both_contents(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("A")
    b.write_text("B")
    plan = [Rename("a", "b", fingerprint(a)), Rename("b", "a", fingerprint(b))]
    apply_plan(tmp_path, plan, apply=True)
    assert a.read_text() == "B" and b.read_text() == "A"


def test_collision_and_duplicate_target_rejected(tmp_path):
    for name in ("draft.txt", "final.txt"):
        (tmp_path / name).write_text(name)
    with pytest.raises(SafetyError, match="already exists"):
        plan_rename(tmp_path, "draft", "final")
    with pytest.raises(SafetyError, match="unique"):
        plan_rename(tmp_path, ".*", "same")


@pytest.mark.parametrize("replacement", ["../bad", "", "sub/file", "bad\\name"])
def test_unsafe_names_rejected(tmp_path, replacement):
    (tmp_path / "a").write_text("content")
    with pytest.raises(SafetyError):
        plan_rename(tmp_path, "a", replacement)


def test_changed_source_rejected_before_writes(tmp_path):
    source = tmp_path / "a"
    source.write_text("before")
    plan = plan_rename(tmp_path, "a", "b")
    source.write_text("after")
    with pytest.raises(SafetyError, match="changed"):
        apply_plan(tmp_path, plan, apply=True)
    assert not (tmp_path / ".fileforge-backups").exists()


def test_plan_path_escape_rejected(tmp_path):
    source = tmp_path / "a"
    source.write_text("A")
    plan = plan_rename(tmp_path, "a", "b")
    with pytest.raises(SafetyError):
        apply_plan(tmp_path, [replace(plan[0], target="../b")], apply=True)


def test_publish_failure_rolls_back(tmp_path, monkeypatch):
    import fileforge.core as core

    source = tmp_path / "a"
    source.write_text("A")
    plan = plan_rename(tmp_path, "a", "b")
    actual_link = core.os.link

    def fail_publish(src, dst):
        if Path(dst).name == "b":
            raise OSError("injected publish failure")
        actual_link(src, dst)

    monkeypatch.setattr(core.os, "link", fail_publish)
    with pytest.raises(OSError, match="injected"):
        apply_plan(tmp_path, plan, apply=True)
    assert source.read_text() == "A"
    assert not (tmp_path / "b").exists()
    assert not [path for path in tmp_path.glob(".fileforge-*") if path.is_file()]


def test_cli_json_and_error_status(tmp_path, capsys):
    (tmp_path / "a").write_text("A")
    assert main(["scan", str(tmp_path)]) == 0
    assert '"sha256"' in capsys.readouterr().out
    config = tmp_path / "settings.toml"
    config.write_text("[scan]\nunknown = true\n")
    with pytest.raises(SystemExit) as error:
        main(["--config", str(config), "scan", str(tmp_path)])
    assert error.value.code == 2


def test_manifest_filename_is_backed_up_without_overwrite(tmp_path):
    (tmp_path / "manifest.json").write_text('{"user":"original"}')
    plan = plan_rename(tmp_path, "manifest", "renamed")
    backup = apply_plan(tmp_path, plan, apply=True)
    assert (backup / "files" / "manifest.json").read_text() == '{"user":"original"}'
    assert (backup / "manifest.json").read_text() != '{"user":"original"}'


@pytest.mark.parametrize(
    "settings", [{"max_size": True}, {"max_size": "large"}, {"recursive": "yes"}]
)
def test_scan_settings_require_correct_types(tmp_path, settings):
    with pytest.raises(ValueError):
        scan(tmp_path, **settings)


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="requires FIFO support")
def test_named_pipe_is_reported_without_blocking(tmp_path):
    import os

    os.mkfifo(tmp_path / "pipe")
    assert "Not a regular file" in scan(tmp_path).errors["pipe"]


def test_extension_sort_dry_run_apply_and_preserve_existing_subtree(tmp_path):
    from fileforge.core import plan_sort

    (tmp_path / "notes.TXT").write_text("notes")
    (tmp_path / "no_extension").write_text("other")
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing" / "keep.txt").write_text("keep")
    plan = plan_sort(tmp_path)
    assert {(item.source, item.target) for item in plan} == {
        ("notes.TXT", "txt/notes.TXT"),
        ("no_extension", "other/no_extension"),
    }
    apply_plan(tmp_path, plan)
    assert not (tmp_path / "txt").exists()
    backup = apply_plan(tmp_path, plan, apply=True)
    assert (tmp_path / "txt" / "notes.TXT").read_text() == "notes"
    assert (tmp_path / "other" / "no_extension").read_text() == "other"
    assert (tmp_path / "existing" / "keep.txt").read_text() == "keep"
    assert (backup / "files" / "notes.TXT").read_text() == "notes"


def test_sort_collision_preserves_all_original_files(tmp_path):
    from fileforge.core import plan_sort

    (tmp_path / "note.txt").write_text("new")
    (tmp_path / "txt").mkdir()
    (tmp_path / "txt" / "note.txt").write_text("existing")
    with pytest.raises(SafetyError, match="already exists"):
        plan_sort(tmp_path)
    assert (tmp_path / "note.txt").read_text() == "new"
    assert (tmp_path / "txt" / "note.txt").read_text() == "existing"


def test_size_report_totals_threshold_and_order(tmp_path):
    from fileforge.core import size_report

    (tmp_path / "small").write_bytes(b"a")
    (tmp_path / "large").write_bytes(b"12345")
    (tmp_path / "medium").write_bytes(b"123")
    report = size_report(tmp_path, min_size=2, limit=1)
    assert report["total_files"] == 3 and report["total_bytes"] == 9
    assert [item["path"] for item in report["largest"]] == ["large"]


def test_sort_failure_rolls_back_and_cleans_new_directories(tmp_path, monkeypatch):
    import fileforge.core as core

    (tmp_path / "note.txt").write_text("keep")
    plan = core.plan_sort(tmp_path)
    actual_link = core.os.link

    def fail_publish(source, target):
        if Path(target).parent.name == "txt":
            raise OSError("injected failure")
        actual_link(source, target)

    monkeypatch.setattr(core.os, "link", fail_publish)
    with pytest.raises(OSError, match="injected"):
        core.apply_plan(tmp_path, plan, apply=True)
    assert (tmp_path / "note.txt").read_text() == "keep"
    assert not (tmp_path / "txt").exists()


def test_structured_transaction_logs(tmp_path, caplog):
    import json

    (tmp_path / "a").write_text("content")
    with caplog.at_level("INFO"):
        plan = plan_rename(tmp_path, "a", "b")
        apply_plan(tmp_path, plan, apply=True)
    assert "transaction_completed" in [
        json.loads(record.message)["event"] for record in caplog.records
    ]


def test_large_file_search_uses_metadata_without_hashing(tmp_path, monkeypatch):
    import fileforge.core as core

    with (tmp_path / "large.bin").open("wb") as handle:
        handle.truncate(2_000_000_000)

    def unexpected_hash(path):
        pytest.fail("size_report must not read file contents")

    monkeypatch.setattr(core, "fingerprint", unexpected_hash)
    report = core.size_report(tmp_path, min_size=1_000_000_000)
    assert report["largest"] == [{"path": "large.bin", "size": 2_000_000_000}]
    assert report["total_bytes"] == 2_000_000_000 and report["errors"] == {}


def test_cli_sort_apply_and_size_json(tmp_path, capsys):
    import json

    (tmp_path / "note.txt").write_text("data")
    assert main(["sizes", str(tmp_path), "--min-size", "2", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["total_bytes"] == 4
    assert main(["sort", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert not (tmp_path / "txt").exists()
    assert main(["sort", str(tmp_path), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert (tmp_path / "txt" / "note.txt").read_text() == "data"
