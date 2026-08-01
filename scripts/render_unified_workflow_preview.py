from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("US_QUANT_LEGACY_UI", None)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from us_quant.desktop import MainWindow, configure_chinese_font  # noqa: E402


def main() -> int:
    output_dir = ROOT / "research" / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="usquant-unified-preview-") as state_root:
        os.environ["US_QUANT_STATE_ROOT"] = state_root
        application = QApplication.instance() or QApplication([])
        configure_chinese_font(application)
        window = MainWindow()
        window.resize(1440, 900)
        window.show()
        application.processEvents()

        workflow = window.unified_workflow_page
        if workflow is None:
            raise RuntimeError("default desktop did not build the unified workflow")

        for theme in ("dark", "light"):
            theme_index = window.settings_theme_combo.findData(theme)
            window.settings_theme_combo.setCurrentIndex(theme_index)
            application.processEvents()
            for key in workflow._sections:
                workflow.navigate_to(key)
                application.processEvents()
                output = output_dir / f"desktop_unified_{key}_{theme}.png"
                if not window.grab().save(str(output)):
                    raise RuntimeError(f"could not save {output.name}")

        window.close()
        window.deleteLater()
        application.processEvents()
    print("Rendered unified workflow previews for dark and light themes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
