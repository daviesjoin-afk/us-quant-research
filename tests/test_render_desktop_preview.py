"""Tooling regression for the desktop backtest preview path."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PREVIEW_PATH = _REPO_ROOT / "scripts" / "render_desktop_preview.py"


def _preview_module():
    spec = importlib.util.spec_from_file_location(
        "render_desktop_preview_under_test",
        _PREVIEW_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeBacktestPage:
    def __init__(self, draft: object) -> None:
        self._draft = draft

    def current_draft(self) -> object:
        return self._draft


class _FakeWindow:
    def __init__(self, draft: object) -> None:
        self.backtest_page = _FakeBacktestPage(draft)
        self.seen: list[tuple[bool, object]] = []

    def _run_backtest_workspace(self, compare_all: bool, draft: object) -> None:
        self.seen.append((compare_all, draft))


def test_preview_uses_current_draft_for_backtest_orchestration() -> None:
    draft = object()
    window = _FakeWindow(draft)
    _preview_module()._start_backtest_preview(window)
    assert window.seen == [(False, draft)]


def test_preview_does_not_call_the_retired_one_argument_signature() -> None:
    source = _PREVIEW_PATH.read_text(encoding="utf-8")
    assert "_run_backtest_workspace(False)" not in source
    assert "backtest_page.current_draft()" in source


class _FakeShell:
    def __init__(self) -> None:
        self.routes: list[str] = []

    def navigate_to(self, route: str) -> None:
        self.routes.append(route)


class _FakeResearchPage:
    def __init__(self) -> None:
        self.workspaces: list[object] = []

    def set_active_workspace(self, workspace: object) -> None:
        self.workspaces.append(workspace)


class _FakeSelectWindow:
    def __init__(self) -> None:
        self.shell = _FakeShell()
        self.research_page = _FakeResearchPage()


def test_preview_select_research_uses_the_semantic_workspace() -> None:
    module = _preview_module()
    window = _FakeSelectWindow()

    module.select_research(
        window,
        module.ResearchWorkspace.BACKTEST,
    )

    assert window.shell.routes == ["research"]
    assert window.research_page.workspaces == [
        module.ResearchWorkspace.BACKTEST
    ]
