import ast
from pathlib import Path

FORBIDDEN_IMPORTS = {"app", "worker", "contracts", "cli", "ops", "services.common"}
FORBIDDEN_PATHS = (
    "../cloud",
    "../computecloud",
    "/Desktop/cloud",
    "host.docker.internal",
)


def check(root: Path) -> list[str]:
    failures = []
    for directory in ("services", "tools", "tests"):
        for path in sorted((root / directory).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source, filename=str(path))):
                modules = (
                    [entry.name for entry in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                for module in modules:
                    if any(
                        module == item or module.startswith(item + ".")
                        for item in FORBIDDEN_IMPORTS
                    ):
                        failures.append(f"{path.relative_to(root)} imports {module}")
            if path.name != "check_boundary.py" and any(
                value in source for value in FORBIDDEN_PATHS
            ):
                failures.append(f"{path.relative_to(root)} references a core checkout")
    return failures


def main() -> None:
    failures = check(Path(__file__).resolve().parents[1])
    if failures:
        raise SystemExit("\n".join(failures))
    print("Independent application boundary verified")


if __name__ == "__main__":
    main()
