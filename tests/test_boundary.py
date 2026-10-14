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


def test_boundary_catches_a_moved_core_checkout_reference(tmp_path: Path) -> None:
    directory = tmp_path / "tests"
    directory.mkdir()
    moved_core = "/Users/example/Desktop/cluster" + "/cloud"
    (directory / "example.py").write_text(f'CORE = "{moved_core}"\n')
    assert check(tmp_path) == ["tests/example.py references a core checkout"]


def test_boundary_scans_python_files_at_the_repository_root(tmp_path: Path) -> None:
    moved_core = "/Users/example/Desktop/cluster" + "/cloud"
    (tmp_path / "conftest.py").write_text(
        f'from services.common.sqlite import connect\nCORE = "{moved_core}"\n'
    )
    assert check(tmp_path) == [
        "conftest.py imports services.common.sqlite",
        "conftest.py references a core checkout",
    ]
