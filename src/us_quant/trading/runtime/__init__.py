"""Trading runtime (skeleton).

Deliberately empty in this change.  The runtime is the single place where the
five pipelines -- market data, account, strategy, risk and execution --
converge, and it is what turns them into the ``TradingSnapshot`` the UI
renders.

It cannot be written before its first pipeline exists.  The real Paper
workflow (``paper_workflow.py``) eventually lands here, but it stays where it
is until the market data and execution migrations give it something to
orchestrate.
"""

from __future__ import annotations
