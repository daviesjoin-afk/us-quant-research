from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_quant.config import load_config  # noqa: E402
from us_quant.executable_research import (  # noqa: E402
    run_executable_cross_sectional_research,
    save_executable_research,
)
from us_quant.universe import load_universe_snapshot  # noqa: E402


def main() -> int:
    config = load_config(ROOT / "configs" / "paper.toml")
    universe = load_universe_snapshot(
        ROOT / "data" / "reference" / "universe.json"
    )
    result = run_executable_cross_sectional_research(
        config,
        universe,
        data_root=ROOT / "data",
    )
    target = save_executable_research(
        result,
        ROOT
        / "research"
        / "results"
        / "cross_sectional_executable_research.json",
    )
    print(
        json.dumps(
            {
                "output": str(target),
                "universe": universe.summary(),
                "scope": result.get("scope", {}),
                "out_of_sample": result.get("out_of_sample", {}),
                "promotion_gate": result.get("promotion_gate", {}),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
