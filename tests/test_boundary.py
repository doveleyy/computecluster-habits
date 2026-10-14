from pathlib import Path

from tools.check_boundary import check


def test_boundary_catches_an_actual_core_import(tmp_path: Path) -> None:
    directory = tmp_path / "services"
    directory.mkdir()
    source = directory / "example.py"
    source.write_text("from services.common.sqlite import connect\n")
    assert check(tmp_path) == ["services/example.py imports services.common.sqlite"]
    source.write_text("from services.habit_tracker.sqlite import connect\n")
    assert check(tmp_path) == []
