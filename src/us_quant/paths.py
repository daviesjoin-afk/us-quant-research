from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys


APP_DIRECTORY_NAME = "USQuantResearch"
STATE_ROOT_ENV = "US_QUANT_STATE_ROOT"


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Separate immutable packaged resources from mutable user state."""

    resource_root: Path
    state_root: Path

    @classmethod
    def discover(
        cls,
        *,
        resource_root: Path | None = None,
        state_root: Path | None = None,
    ) -> ApplicationPaths:
        resources = resource_root or _default_resource_root()
        state = state_root or _default_state_root(resources)
        return cls(
            resource_root=resources.resolve(),
            state_root=state.resolve(),
        )

    @property
    def runtime_root(self) -> Path:
        return self.state_root / "runtime"

    @property
    def logs_root(self) -> Path:
        return self.state_root / "logs"

    @property
    def exports_root(self) -> Path:
        return self.state_root / "exports"

    @property
    def user_data_root(self) -> Path:
        return self.state_root / "data"

    @property
    def bundled_data_root(self) -> Path:
        return self.resource_root / "data"

    @property
    def research_results_root(self) -> Path:
        return self.state_root / "research" / "results"

    @property
    def bundled_research_results_root(self) -> Path:
        return self.resource_root / "research" / "results"

    @property
    def config_path(self) -> Path:
        return self.resource_root / "configs" / "paper.toml"

    def ensure_state_directories(self) -> None:
        for path in (
            self.state_root,
            self.runtime_root,
            self.logs_root,
            self.exports_root,
            self.user_data_root,
            self.research_results_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def seed_research_results(self) -> None:
        """Copy small bundled baseline artifacts into the writable catalog."""
        source = self.bundled_research_results_root
        if not source.exists():
            return
        self.research_results_root.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            target = self.research_results_root / item.name
            if target.exists() or not item.is_file():
                continue
            shutil.copy2(item, target)

    def ensure_user_reference_catalog(self) -> Path:
        """Materialize reference inputs only when a refresh needs to write."""
        target = self.user_data_root / "reference"
        if not target.exists() and self.bundled_data_root.joinpath(
            "reference"
        ).exists():
            shutil.copytree(
                self.bundled_data_root / "reference",
                target,
            )
        target.mkdir(parents=True, exist_ok=True)
        return target


def _default_resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _default_state_root(resource_root: Path) -> Path:
    override = os.environ.get(STATE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    if not getattr(sys, "frozen", False):
        return resource_root / "runtime" / "appdata"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / APP_DIRECTORY_NAME
