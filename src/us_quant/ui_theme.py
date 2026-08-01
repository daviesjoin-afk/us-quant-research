from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemePalette:
    name: str
    background: str
    surface: str
    surface_alt: str
    panel: str
    border: str
    grid: str
    text: str
    heading: str
    muted: str
    subtle: str
    accent: str
    accent_alt: str
    accent_surface: str
    success: str
    success_surface: str
    warning: str
    warning_surface: str
    error: str
    error_surface: str
    safety: str
    safety_surface: str
    selection_text: str


DARK_THEME = ThemePalette(
    name="dark",
    background="#0b1219",
    surface="#0e171f",
    surface_alt="#111d27",
    panel="#101b25",
    border="#20303e",
    grid="#1c2a36",
    text="#d8e2ec",
    heading="#f4f8fb",
    muted="#8ea1b4",
    subtle="#63778b",
    accent="#33d6ad",
    accent_alt="#6f97ff",
    accent_surface="#173c38",
    success="#42deb8",
    success_surface="#10332d",
    warning="#f0b35b",
    warning_surface="#3b2a15",
    error="#ff7a8a",
    error_surface="#411f29",
    safety="#c8a0f4",
    safety_surface="#2b1f37",
    selection_text="#f3faf8",
)

LIGHT_THEME = ThemePalette(
    name="light",
    background="#f4f7fa",
    surface="#ffffff",
    surface_alt="#f7fafc",
    panel="#ffffff",
    border="#d7e0e8",
    grid="#e5ebf0",
    text="#243342",
    heading="#132231",
    muted="#607487",
    subtle="#7b8c9c",
    accent="#087f6b",
    accent_alt="#345fc7",
    accent_surface="#e7f5f1",
    success="#087f6b",
    success_surface="#ddf4ed",
    warning="#9a5d00",
    warning_surface="#fff0d4",
    error="#b4233a",
    error_surface="#fde7eb",
    safety="#70419b",
    safety_surface="#f0e7f8",
    selection_text="#102c27",
)


def theme_palette(name: str) -> ThemePalette:
    return LIGHT_THEME if name == "light" else DARK_THEME


def build_stylesheet(palette: ThemePalette) -> str:
    p = palette
    return f"""
        QWidget {{
            background: {p.background};
            color: {p.text};
            font-family: "Microsoft YaHei UI";
            font-size: 13px;
        }}
        QMainWindow {{ background: {p.background}; }}
        QLabel {{ background: transparent; }}
        #appTitle {{
            font-size: 25px;
            font-weight: 700;
            color: {p.heading};
        }}
        #subtitle {{ color: {p.muted}; font-size: 12px; }}
        #fieldLabel {{
            color: {p.muted};
            font-size: 11px;
            font-weight: 600;
        }}
        #emptyState {{
            background: {p.panel};
            color: {p.muted};
            border: 1px dashed {p.border};
            border-radius: 7px;
            padding: 10px;
        }}
        #statusBadge, #safetyBadge {{
            border-radius: 12px;
            padding: 6px 10px;
            font-weight: 600;
            font-size: 11px;
        }}
        #statusBadge {{ background: {p.surface_alt}; color: {p.muted}; }}
        #statusBadge[state="ok"] {{
            background: {p.success_surface}; color: {p.success};
        }}
        #statusBadge[state="warn"] {{
            background: {p.warning_surface}; color: {p.warning};
        }}
        #statusBadge[state="error"] {{
            background: {p.error_surface}; color: {p.error};
        }}
        #safetyBadge {{
            background: {p.safety_surface}; color: {p.safety};
        }}
        QTabWidget::pane {{
            border: 1px solid {p.border};
            border-radius: 8px;
            background: {p.surface};
        }}
        QTabBar::tab {{
            background: transparent;
            color: {p.muted};
            padding: 10px 11px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            color: {p.accent};
            border-bottom: 2px solid {p.accent};
        }}
        #metricCard, #panel {{
            background: {p.panel};
            border: 1px solid {p.border};
            border-radius: 9px;
        }}
        #metricTitle {{ color: {p.muted}; font-size: 12px; }}
        #metricValue {{
            color: {p.heading};
            font-size: 26px;
            font-weight: 700;
        }}
        #metricNote {{ color: {p.subtle}; font-size: 11px; }}
        #sectionTitle {{
            font-size: 17px;
            font-weight: 650;
            color: {p.heading};
        }}
        QPushButton {{
            background: {p.accent_surface};
            color: {p.accent};
            border: 1px solid {p.accent};
            border-radius: 6px;
            padding: 7px 12px;
            min-height: 24px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {p.success_surface}; }}
        QPushButton:pressed {{ background: {p.surface_alt}; }}
        QPushButton:disabled {{
            color: {p.subtle};
            border-color: {p.border};
            background: {p.surface_alt};
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
            background: {p.surface};
            color: {p.text};
            border: 1px solid {p.border};
            border-radius: 6px;
            padding: 7px;
            min-height: 22px;
        }}
        QComboBox {{
            padding-right: 30px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 28px;
            border-left: 1px solid {p.border};
        }}
        QSpinBox, QDoubleSpinBox, QDateEdit {{
            padding-right: 24px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
        QDoubleSpinBox:focus, QDateEdit:focus {{
            border-color: {p.accent};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
        QDoubleSpinBox:disabled, QDateEdit:disabled {{
            background: {p.surface_alt};
            color: {p.subtle};
        }}
        QCheckBox {{
            spacing: 8px;
            background: transparent;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {p.border};
            border-radius: 4px;
            background: {p.surface_alt};
        }}
        QCheckBox::indicator:hover {{
            border-color: {p.accent};
        }}
        QCheckBox::indicator:checked {{
            border-color: {p.accent};
            background: {p.accent};
        }}
        QCheckBox::indicator:disabled {{
            background: {p.surface_alt};
            border-color: {p.grid};
        }}
        QTableView, QTableWidget {{
            background: {p.surface};
            alternate-background-color: {p.surface_alt};
            color: {p.text};
            border: 1px solid {p.border};
            gridline-color: {p.grid};
            selection-background-color: {p.success_surface};
            selection-color: {p.selection_text};
        }}
        QHeaderView::section {{
            background: {p.surface_alt};
            color: {p.muted};
            padding: 7px;
            border: none;
            border-right: 1px solid {p.border};
            font-weight: 600;
        }}
        QTableView::item, QTableWidget::item {{
            padding: 5px 7px;
        }}
        QTextEdit {{
            background: {p.surface};
            border: 1px solid {p.border};
            border-radius: 7px;
            padding: 8px;
            color: {p.text};
        }}
        QProgressBar {{
            border: 1px solid {p.border};
            border-radius: 5px;
            background: {p.surface};
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: {p.accent};
            border-radius: 4px;
        }}
        QScrollBar:horizontal, QScrollBar:vertical {{
            background: {p.surface_alt};
            border: none;
        }}
        QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{
            background: {p.border};
            border-radius: 4px;
            min-width: 28px;
            min-height: 28px;
        }}
        #footer {{ color: {p.muted}; padding: 2px 4px; }}
        QSplitter::handle {{
            background: {p.border};
            margin: 1px;
        }}
        QSplitter::handle:hover {{ background: {p.accent}; }}
        QToolTip {{
            background: {p.panel};
            color: {p.text};
            border: 1px solid {p.border};
            padding: 4px;
        }}
    """
