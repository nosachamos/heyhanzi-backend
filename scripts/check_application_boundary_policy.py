"""Keep infrastructure ownership explicit; inspect architecture outside behavior tests."""
import ast
from pathlib import Path


def main():
    errors = []
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            if any(module.startswith(("tests", "scripts")) for module in modules):
                errors.append(f"{path.relative_to(root)}: runtime must not import tests/scripts")
            if "routers" in path.parts and any(module.startswith(("boto3", "psycopg2")) for module in modules):
                errors.append(f"{path.relative_to(root)}: router must not perform blocking provider I/O")
    for error in errors:
        print(error)
    print(f"Application boundary policy: {len(errors)} violation(s).")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
