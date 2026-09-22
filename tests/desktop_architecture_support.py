"""Mechanical AST queries shared by the desktop architecture guards.

Several architecture tests grew their own copy of the same handful of
functions -- ``_tree``, ``_imports``, ``_method_source``, ``_self_attributes``,
``_page_methods`` -- because each round added a file and copied the helpers with
it.  The copies are identical in behaviour and differ only in spelling
(``_tree`` vs ``_parse``, ``_imports`` vs ``_module_imports``), which makes a
guard harder to read and a helper fix impossible to land once.

This module holds only the *mechanical* queries: parse a file, list its imports,
find a class, read one method's source.  Nothing here knows what a capability is
allowed to import or which method must not exist -- those rules stay in each
guard, where a reviewer reading a failure can see them.  There is no state, no
caching and no dependency outside the standard library.

Only the helpers this round actually needed were extracted.  The historical
guards that already have their own working copies keep them: rewriting five
files of passing tests to import a helper would be a bigger diff than the
duplication it removes, and would put this round's review surface in tests that
have nothing to do with the backtest extraction.
"""

from __future__ import annotations

import ast
import pathlib


def python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every ``.py`` file under ``root``, sorted, without ``__pycache__``."""

    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def tree(path: pathlib.Path) -> ast.Module:
    """Parse one file as UTF-8."""

    return ast.parse(path.read_text(encoding="utf-8"))


def module_imports(path: pathlib.Path) -> set[str]:
    """Every dotted module path the file imports (absolute imports only).

    Relative imports are skipped: their ``node.module`` is not a resolvable
    path, so including them would produce entries no allowlist could declare.
    """

    found: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add(node.module or "")
    return found


def top_level_imports(path: pathlib.Path) -> set[str]:
    """Only the imports at module scope, ignoring any nested in functions."""

    found: set[str] = set()
    for node in tree(path).body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add(node.module or "")
    return found


def imported_names(path: pathlib.Path) -> set[str]:
    """Every name the file binds from an import, as written.

    This is the half a module-path guard cannot see:
    ``from <allowed_module> import <forbidden_symbol>`` binds a symbol, and
    ``from us_quant.trading.runtime import workflow_state`` binds a module under
    its own name.
    """

    found: set[str] = set()
    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def class_def(path: pathlib.Path, name: str) -> ast.ClassDef:
    """The class named ``name``, searched at any depth."""

    for node in ast.walk(tree(path)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path}")


def method_names(
    path: pathlib.Path, class_name: str = "MainWindow"
) -> set[str]:
    """The methods declared directly on one class."""

    return {
        node.name
        for node in class_def(path, class_name).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def method_source(
    path: pathlib.Path,
    name: str,
    *,
    class_name: str = "MainWindow",
) -> str | None:
    """The source text of one method, or ``None`` when it is not declared.

    ``None`` rather than an exception, because "this method must be gone" is a
    guard's normal assertion and reading it through ``assert ... is None`` is
    clearer than catching an ``AssertionError`` from the helper.
    """

    for node in class_def(path, class_name).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return ast.get_source_segment(
                    path.read_text(encoding="utf-8"), node
                ) or ""
    return None


def self_attributes(
    path: pathlib.Path, class_name: str = "MainWindow"
) -> set[str]:
    """Every ``self.<attr>`` read or written anywhere in one class."""

    attributes: set[str] = set()
    for node in ast.walk(class_def(path, class_name)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            attributes.add(node.attr)
    return attributes


def assigned_self_attributes(
    path: pathlib.Path, class_name: str = "MainWindow"
) -> set[str]:
    """Only the ``self.<attr>`` names the class *assigns*.

    The distinction from :func:`self_attributes` matters for a state guard:
    ``self.foo = ...`` is the class holding state, while ``self.foo.bar()`` is
    merely a read of something else's.
    """

    names: set[str] = set()
    for node in ast.walk(class_def(path, class_name)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def public_names(path: pathlib.Path, class_name: str) -> set[str]:
    """The class-level names a caller may use: signals, properties, methods.

    Private names are excluded, so a guard can compare the result for equality
    against a declared public surface and fail on a convenience accessor.
    """

    names: set[str] = set()
    for item in class_def(path, class_name).body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(item.name)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = (
                item.targets if isinstance(item, ast.Assign) else [item.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return {name for name in names if not name.startswith("_")}


def receiver_methods(path: pathlib.Path, receiver: str) -> set[str]:
    """Every ``<receiver>.<method>`` attribute reached anywhere in the file.

    Used for "which methods of this page does the window call": the receiver is
    the attribute name on the window (``backtest_page``), so the answer does not
    depend on how the local variable holding it is spelled.
    """

    methods: set[str] = set()
    for node in ast.walk(tree(path)):
        if not isinstance(node, ast.Attribute):
            continue
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == receiver
        ):
            methods.add(node.attr)
    return methods


def calls_attribute(path: pathlib.Path, attribute: str) -> bool:
    """Does the file call ``<anything>.<attribute>(...)`` anywhere?"""

    for node in ast.walk(tree(path)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attribute:
                return True
    return False


def assigned_names(tree_or_source: ast.Module | str) -> set[str]:
    """Every bare name assigned anywhere in a tree or a source snippet.

    Accepts either form because the guards differ: some read the whole file,
    others parse one method's source to assert what that method alone assigns.
    """

    parsed = (
        tree_or_source
        if isinstance(tree_or_source, ast.Module)
        else ast.parse(tree_or_source)
    )
    names: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name
        ):
            names.add(node.target.id)
    return names


__all__ = [
    "assigned_names",
    "assigned_self_attributes",
    "calls_attribute",
    "class_def",
    "imported_names",
    "method_names",
    "method_source",
    "module_imports",
    "public_names",
    "python_files",
    "receiver_methods",
    "self_attributes",
    "top_level_imports",
    "tree",
]
