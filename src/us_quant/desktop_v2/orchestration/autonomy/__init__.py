"""Desktop adapters for the Paper autonomy supervisor capability."""

from us_quant.desktop_v2.orchestration.autonomy.completion import (
    PaperAutonomyCompletionObserver,
)
from us_quant.desktop_v2.orchestration.autonomy.executor import (
    PaperAutonomyDesktopExecutor,
)
from us_quant.desktop_v2.orchestration.autonomy.facts import (
    PaperAutonomyRuntimeFactsAdapter,
    PaperAutonomyStartupFactsAdapter,
)

__all__ = [
    "PaperAutonomyDesktopExecutor",
    "PaperAutonomyCompletionObserver",
    "PaperAutonomyRuntimeFactsAdapter",
    "PaperAutonomyStartupFactsAdapter",
]
