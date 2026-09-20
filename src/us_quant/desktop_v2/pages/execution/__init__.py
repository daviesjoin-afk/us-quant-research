"""Desktop UI v2 execution page package.

The execution route is the fourth chain to become a native v2 page, after
account, strategy and risk.  It is a package rather than a module because the
route has four distinct jobs -- immutable view models, a Qt-free projection, the
detail tables, and the page that composes them -- and one module holding all four
would be the 400-line page this migration exists to avoid.

Nothing in this package may import an application service, a workflow
controller, a runtime, a repository, a broker adapter, Qt's network stack or the
legacy window; the guards in ``tests/test_trading_architecture.py`` scan the
whole package for exactly those.
"""

from __future__ import annotations

from us_quant.desktop_v2.pages.execution.page import ExecutionPage

__all__ = ["ExecutionPage"]