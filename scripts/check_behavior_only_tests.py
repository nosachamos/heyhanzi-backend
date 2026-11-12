#!/usr/bin/env python3
"""Reject implementation-coupled tests and support modules.

This is a static policy check, not a semantic proof of public-boundary coverage.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh"}
PARSER_MODULES = {"ast", "libcst", "astroid"}
INTROSPECTION = {"getsource", "getsourcelines", "getsourcefile", "getfile", "getmembers"}
RECONSTRUCTION = {"eval", "exec", "compile"}


def module_path(root: Path, name: str) -> Path | None:
    path = root.joinpath(*name.split("."))
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def scan_file(root: Path, path: Path) -> tuple[list[dict], list[Path]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: dict[str, str] = {}
    helpers: list[Path] = []
    operations: list[dict] = []
    scope = "<module>"
    values: dict[str, str] = {}
    literal_sequences: dict[str, tuple[str, ...]] = {}
    loop_strings: dict[str, tuple[str, ...]] = {}

    def expression(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return values.get(node.id, node.id)
        if isinstance(node, ast.Constant):
            return str(node.value)
        return " ".join(expression(child) for child in ast.iter_child_nodes(node))

    def qualified(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return imported.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + "." + node.attr
        if isinstance(node, ast.Call):
            return qualified(node.func)
        return ""

    def record(rule: str, node: ast.AST) -> None:
        rendered = ast.unparse(node)
        operations.append({"file": path.relative_to(root).as_posix(), "case": scope,
                           "rule": rule, "operation": rendered,
                           "line": node.lineno})

    # Bind imports before visiting uses, including imports inside scenarios.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
                helper = module_path(root, alias.name)
                if helper and not alias.name.startswith(("app.", "scripts.")):
                    helpers.append(helper)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported[alias.asname or alias.name] = module + "." + alias.name
            helper = module_path(root, module)
            if helper and not module.startswith(("app.", "scripts.")):
                helpers.append(helper)

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            nonlocal scope
            previous = scope
            scope = node.name if previous == "<module>" else previous + "." + node.name
            self.generic_visit(node)
            scope = previous

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            nonlocal scope
            previous = scope
            scope = node.name if previous == "<module>" else previous + "." + node.name
            self.generic_visit(node)
            scope = previous

        def visit_Assign(self, node):
            rendered = expression(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = rendered
                    if isinstance(node.value, (ast.Tuple, ast.List)) and all(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        for item in node.value.elts
                    ):
                        literal_sequences[target.id] = tuple(item.value for item in node.value.elts)
                    else:
                        literal_sequences.pop(target.id, None)
                    owner = qualified(node.value)
                    if owner.startswith(("app.", "scripts.")):
                        imported[target.id] = owner
                    elif (
                        isinstance(node.value, ast.Call)
                        and qualified(node.value.func) == "importlib.import_module"
                        and node.value.args
                        and isinstance(node.value.args[0], ast.Constant)
                        and isinstance(node.value.args[0].value, str)
                        and node.value.args[0].value.startswith(("app.", "scripts."))
                    ):
                        imported[target.id] = node.value.args[0].value
            self.generic_visit(node)

        def visit_For(self, node):
            if isinstance(node.target, ast.Name):
                if isinstance(node.iter, (ast.Tuple, ast.List)) and all(
                    isinstance(item, ast.Constant) and isinstance(item.value, str)
                    for item in node.iter.elts
                ):
                    names = tuple(item.value for item in node.iter.elts)
                elif isinstance(node.iter, ast.Name):
                    names = literal_sequences.get(node.iter.id, ())
                else:
                    names = ()
                if names:
                    previous = loop_strings.get(node.target.id)
                    loop_strings[node.target.id] = names
                    self.generic_visit(node)
                    if previous is None:
                        loop_strings.pop(node.target.id, None)
                    else:
                        loop_strings[node.target.id] = previous
                    return
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name.split(".")[0] in PARSER_MODULES:
                    record("implementation-parser", node)

        def visit_ImportFrom(self, node):
            if (node.module or "").split(".")[0] in PARSER_MODULES:
                record("implementation-parser", node)
            if (node.module or "").startswith(("app.", "scripts.")):
                for alias in node.names:
                    if is_private(alias.name):
                        record("private-import", node)
                        break

        def visit_Attribute(self, node):
            owner = qualified(node.value)
            if is_private(node.attr) and owner.startswith(("app.", "scripts.")):
                record("private-member", node)
            self.generic_visit(node)

        def visit_Call(self, node):
            owner = qualified(node.func)
            last = owner.rsplit(".", 1)[-1]
            if owner.startswith("inspect.") and last in INTROSPECTION:
                record("source-introspection", node)
            if owner in RECONSTRUCTION or owner.startswith("builtins.") and last in RECONSTRUCTION:
                record("code-reconstruction", node)
            if last in {"read_text", "read_bytes", "open"}:
                target = node.func.value if isinstance(node.func, ast.Attribute) and last != "open" else (node.args[0] if node.args else node.func)
                tokens = expression(target).replace("'", " ").replace('"', " ").split()
                if any(Path(token).suffix in SOURCE_SUFFIXES for token in tokens):
                    record("implementation-source-read", node)
            if last in {"setattr", "delattr", "getattr", "patch"}:
                strings = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        strings.extend(loop_strings.get(arg.id, ()))
                if (any(value.startswith(("app.", "scripts.")) and any(is_private(part) for part in value.split(".")) for value in strings)
                        or len(node.args) >= 2 and qualified(node.args[0]).startswith(("app.", "scripts."))
                        and any(is_private(value) for value in strings)):
                    record("private-reflection", node)
            if owner in {"importlib.import_module", "__import__"} and node.args:
                if isinstance(node.args[0], ast.Constant) and str(node.args[0].value).split(".")[0] in PARSER_MODULES:
                    record("implementation-parser", node)
            self.generic_visit(node)

    Visitor().visit(tree)
    return operations, helpers


def discover(root: Path) -> list[dict]:
    queued = list((root / "tests").rglob("*.py"))
    visited: set[Path] = set()
    operations = []
    while queued:
        path = queued.pop()
        if path in visited:
            continue
        visited.add(path)
        found, helpers = scan_file(root, path)
        operations.extend(found)
        queued.extend(helpers)
    return sorted(operations, key=lambda item: (item["file"], item["line"], item["rule"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "tests").is_dir():
        parser.error("root must contain a tests directory")
    inventory_path = root / "docs/testing/behavior-only-pending.json"
    if inventory_path.exists():
        parser.error("pending inventory is no longer supported")
    operations = discover(root)
    if operations:
        for item in operations:
            print(f'{item["file"]}:{item["line"]} {item["case"]}: {item["rule"]}: {item["operation"]}')
        print(f"Behavior-only policy FAILED: {len(operations)} implementation-coupled operations.")
        return 1
    print("Behavior-only policy passed: 0 implementation-coupled operations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
