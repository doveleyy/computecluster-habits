import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for command in (
        [sys.executable, "-m", "tools.check_boundary"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "mypy", "--python-executable", sys.executable],
        [sys.executable, "-m", "pytest", "-q"],
        ["git", "diff", "--check"],
    ):
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
