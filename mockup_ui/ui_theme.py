"""Application appearance; stylesheet geometry stays identical in both themes."""
from pathlib import Path
import re

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


def current_theme():
    app = QApplication.instance()
    return (app.property("forensikadaTheme") or "light") if app else "light"


def restore_theme():
    app = QApplication.instance()
    if app.property("forensikadaTheme") is None:
        saved = QSettings("ForensiKada", "VideoDetection").value("appearance/theme", "light")
        app.setProperty("forensikadaTheme", "dark" if saved == "dark" else "light")


def _dark_color(token, property_name):
    color = QColor(token)
    red, green, blue, _ = color.getRgb()
    if "background" in property_name:
        if min(red, green, blue) > 150:
            if green > red + 5 and green > blue:
                return "#253b32"
            if blue > red + 6:
                return "#303247"
            return "#252e38" if min(red, green, blue) > 235 else "#303b48"
    elif property_name.startswith("border"):
        if max(red, green, blue) > 145:
            return "#536071"
    elif property_name in ("color", "selection-color"):
        if max(red, green, blue) < 200:
            if max(red, green, blue) - min(red, green, blue) < 65:
                return "#e2e7ee" if max(red, green, blue) < 100 else "#b9c4d2"
            return color.lighter(220).name()
    return token


def load_stylesheet():
    source = (Path(__file__).parent / "styles.qss").read_text(encoding="utf-8")
    if current_theme() != "dark":
        return source

    def declaration(match):
        name, value = match.group(1), match.group(2)
        value = re.sub(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b",
                       lambda token: _dark_color(token.group(0), name), value)
        return f"{name}: {value}"

    source = re.sub(r"\b(background(?:-color)?|alternate-background-color|selection-background-color|selection-color|color|border(?:-[a-z]+)?)\s*:\s*([^;{}]+)",
                    declaration, source)
    # Scroll-area content widgets otherwise keep the native light window fill.
    return source + """
QWidget#multiCameraWorkspace QScrollArea > QWidget,
QWidget#multiCameraWorkspace QScrollArea > QWidget > QWidget { background: #232c35; }
QDialog#configurationDialog QSlider::groove:horizontal,
QDialog#settingsDialog QSlider::groove:horizontal { background: #536071; }
QDialog#configurationDialog QSlider::sub-page:horizontal,
QDialog#settingsDialog QSlider::sub-page:horizontal { background: #8b8fe8; }
QDialog#configurationDialog QSlider::handle:horizontal,
QDialog#settingsDialog QSlider::handle:horizontal { background: #e2e7ee; border-color: #8b8fe8; }
"""


def apply_theme(window, theme, *, persist=True):
    theme = "dark" if theme == "dark" else "light"
    QApplication.instance().setProperty("forensikadaTheme", theme)
    palette = QPalette(QApplication.palette())
    if theme == "dark":
        for role, color in ((QPalette.ColorRole.Window, "#232c35"),
                            (QPalette.ColorRole.Base, "#252e38"),
                            (QPalette.ColorRole.AlternateBase, "#303b48"),
                            (QPalette.ColorRole.Button, "#303841"),
                            (QPalette.ColorRole.Text, "#e2e7ee"),
                            (QPalette.ColorRole.WindowText, "#e2e7ee"),
                            (QPalette.ColorRole.ButtonText, "#e2e7ee")):
            palette.setColor(role, QColor(color))
    window.setPalette(palette)
    stylesheet = load_stylesheet()
    window.setStyleSheet(stylesheet)
    # These surfaces also work standalone and therefore own a stylesheet.
    for widget in window.findChildren(QWidget):
        if widget.objectName() in ("configurationDialog", "multiCameraWorkspace", "observationReviewDialog", "settingsDialog"):
            widget.setStyleSheet(stylesheet)
    if persist:
        QSettings("ForensiKada", "VideoDetection").setValue("appearance/theme", theme)
