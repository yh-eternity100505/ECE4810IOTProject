from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

# Reference palette: deep teal, slate #3D4D55, taupe #A79E9C,
# ivory #D3C3B9, muted bronze #B58863, and near-black.
# Edit these named colours to customise either theme.
LIGHT = {"sidebar": "#0E232B", "bg": "#D3C3B9", "accent": "#B58863",
         "secondary": "#766659", "card": "#F9F6F3", "text": "#3D4D55",
         "muted": "#655F5B", "border": "#C2B1A5", "input": "#EEE5DF",
         "accent_hover": "#C49B78", "accent_soft": "#E7D5C6",
         "nav_active": "#3D4D55", "nav_hover": "#293B43",
         "accent_text": "#141414", "sidebar_text": "#D3C3B9",
         "sidebar_muted": "#A79E9C", "heading": "#0E232B"}
DARK = {"bg": "#0E232B", "card": "#3D4D55", "text": "#F0EBE7", "muted": "#C1B6AE",
        "border": "#52616A", "input": "#293B43", "sidebar": "#141414",
        "accent": "#B58863", "secondary": "#A79E9C", "accent_hover": "#C49B78",
        "accent_soft": "#594A3E", "nav_active": "#3D4D55", "nav_hover": "#293B43",
        "accent_text": "#141414", "sidebar_text": "#D3C3B9",
        "sidebar_muted": "#A79E9C", "heading": "#D3C3B9"}

# Soft pet-themed colors with separate readable light and dark palettes.
LIGHT.update(bg="#FFF7F2", card="#FFFFFF", text="#51435D", muted="#786A85",
             border="#E9DDED", input="#FAF1FA", sidebar="#F2E7FA", accent="#BD83C4",
             accent_hover="#AA70B5", accent_soft="#F6E5F3", nav_active="#E2CDEB",
             nav_hover="#EBDAF1", accent_text="#FFFFFF", sidebar_text="#51435D",
             sidebar_muted="#786A85", heading="#624878", secondary="#AF7A94")
# Dark uses the original teal/slate palette; tiny cartoons supply the color.


def current_colors():
    app = QApplication.instance()
    return DARK if app and app.property("petcare_theme") == "Dark" else LIGHT


def apply_theme(app, theme="Dark"):
    c = DARK if theme == "Dark" else LIGHT
    app.setProperty("petcare_theme", theme)
    palette = QPalette()
    for role, key in [(QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "card"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Button, "card")]:
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 13px; }}
        QMainWindow, #shell, #page, QScrollArea, #login {{ background: {c['bg']}; }}
        QScrollArea {{ border: none; }}
        QLabel {{ background: transparent; }}
        QLabel#muted {{ color: {c['muted']}; }}
        QLabel#eyebrow {{ color: {c['muted']}; font-size: 11px; font-weight: 700; }}
        QLabel#title {{ color: {c['heading']}; font-size: 28px; font-weight: 600; }}
        QLabel#sectionTitle {{ font-size: 17px; font-weight: 600; }}
        QLabel#metric {{ color: {c['heading']}; font-size: 26px; font-weight: 700; }}
        QFrame#card, QGroupBox {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 16px; }}
        QFrame#card[petTone="sleep"] {{ border: 2px solid #B4A0DD; }}
        QFrame#card[petTone="eat"] {{ border: 2px solid #E7A0AF; }}
        QFrame#card[petTone="water"] {{ border: 2px solid #79C4D4; }}
        QFrame#card[petTone="toilet"] {{ border: 2px solid #A0C7AA; }}
        QFrame#card[petTone="gesture"] {{ border: 2px solid #EAC68E; }}
        QFrame#topStatusBar {{ background: {c['bg']}; border: none; border-bottom: 1px solid {c['border']}; }}
        QFrame#sidebar {{ background: {c['sidebar']}; border: none; }}
        #sidebar QLabel {{ color: {c['sidebar_text']}; }}
        #sidebar QLabel#muted {{ color: {c['sidebar_muted']}; }}
        QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 9px 15px; font-weight: 600; }}
        QPushButton:hover {{ border: 1px solid {c['accent']}; }}
        QPushButton:disabled {{ color: {c['muted']}; }}
        QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none; }}
        QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
        QPushButton#inspectorStage:checked {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; }}
        QPlainTextEdit {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 8px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
        QPushButton#nav {{ text-align: left; color: {c['sidebar_text']}; background: transparent; border: none; padding: 13px 16px; }}
        QPushButton#nav:checked {{ color: {c['sidebar_text']}; background: {c['nav_active']}; border-left: 3px solid {c['accent']}; }}
        QPushButton#nav:hover {{ background: {c['nav_hover']}; }}
        QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 8px; min-height: 21px; }}
        QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
        QComboBox QAbstractItemView {{ background: {c['card']}; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
        QCheckBox {{ spacing: 9px; padding: 4px 0; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; }}
        QTableWidget {{ background: {c['card']}; gridline-color: {c['border']}; border: none; border-radius: 8px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
        QHeaderView::section {{ background: {c['input']}; color: {c['muted']}; padding: 11px; border: none; border-bottom: 1px solid {c['border']}; font-weight: 600; }}
        QTableWidget::item {{ padding: 7px; }}
        QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['card']}; }}
        QTabBar::tab {{ background: {c['input']}; padding: 10px 20px; border: none; }}
        QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
        QProgressBar {{ border: none; background: {c['input']}; border-radius: 5px; height: 9px; text-align: center; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
        QFrame#card[petTone="eat"] QProgressBar::chunk {{ background: #DF8DA5; }}
        QFrame#card[petTone="water"] QProgressBar::chunk {{ background: #5EB8CB; }}
        QStatusBar {{ background: {c['card']}; color: {c['muted']}; }}
        QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """)
