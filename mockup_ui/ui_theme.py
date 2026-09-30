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


def _navy_color(token):
    """Unify legacy UI colors without changing semantic accept/reject colors."""
    token = token.lower()
    colors = {
        "#010736": "#010736", "#0d1c42": "#0d1c42", "#22396f": "#22396f",
        "#304d85": "#304d85", "#506b9e": "#506b9e", "#32486e": "#32486e",
        "#263b63": "#263b63", "#8f9fbc": "#8f9fbc", "#a4b3cc": "#a4b3cc",
        "#172950": "#172950",
        "#303841": "#0d1c42", "#303848": "#0d1c42", "#20262d": "#0d1c42",
        "#252b38": "#0d1c42", "#263142": "#0d1c42", "#303a49": "#0d1c42",
        "#252d35": "#010736", "#20262e": "#0d1c42",
        "#3b4552": "#172950", "#424b56": "#22396f", "#525d6d": "#22396f",
        "#667385": "#50648b", "#535e6d": "#32486e", "#444e5d": "#22396f",
        "#626d7b": "#60718b", "#596476": "#60718b", "#66717d": "#60718b",
        "#65707c": "#60718b", "#68727e": "#60718b",
        "#f5f5f9": "#f4f7fc", "#f0f0f6": "#edf2fa", "#f3f4f9": "#f5f8fc",
        "#fafafe": "#fafcff", "#d9dce6": "#d7e0ed", "#e0e2eb": "#dce4ef",
        "#dedfe8": "#dce4ef", "#e3e4ed": "#e0e7f1", "#e7e8f0": "#e4ebf4",
        "#babfcd": "#b6c5da", "#9098af": "#8196b5",
        "#232c35": "#101b32", "#252e38": "#13223b", "#303b48": "#1b2d49",
        "#303247": "#1a2c50", "#536071": "#354c70",
        "#e2e7ee": "#e8eef8", "#b9c4d2": "#aebed4", "#8b8fe8": "#9db9ee",
    }
    if token in colors:
        return colors[token]
    color = QColor(token)
    hue, saturation, lightness, _ = color.getHslF()
    # Retire violet accents and tinted panels across dialogs and the workspace.
    if .60 <= hue <= .76 and saturation > .12:
        if lightness > .90:
            return "#eef3fb"
        if lightness > .78:
            return "#d6e2f4"
        if lightness > .60:
            return "#91a9d0"
        if lightness > .20:
            return "#22396f"
    return token


def _navy_stylesheet(source):
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda match: _navy_color(match.group(0)), source)


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
    source += "\n" + (Path(__file__).parent / "ios_theme.qss").read_text(encoding="utf-8")
    if current_theme() != "dark":
        return _navy_stylesheet(source)

    def declaration(match):
        name, value = match.group(1), match.group(2)
        value = re.sub(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b",
                       lambda token: _dark_color(token.group(0), name), value)
        return f"{name}: {value}"

    source = re.sub(r"\b(background(?:-color)?|alternate-background-color|selection-background-color|selection-color|color|border(?:-[a-z]+)?)\s*:\s*([^;{}]+)",
                    declaration, source)
    # Scroll-area content widgets otherwise keep the native light window fill.
    return _navy_stylesheet(source + """
QWidget#multiCameraWorkspace QScrollArea > QWidget,
QWidget#multiCameraWorkspace QScrollArea > QWidget > QWidget { background: #232c35; }
QDialog#configurationDialog QSlider::groove:horizontal,
QDialog#settingsDialog QSlider::groove:horizontal { background: #536071; }
QDialog#configurationDialog QSlider::sub-page:horizontal,
QDialog#settingsDialog QSlider::sub-page:horizontal { background: #8b8fe8; }
QDialog#configurationDialog QSlider::handle:horizontal,
QDialog#settingsDialog QSlider::handle:horizontal { background: #e2e7ee; border-color: #8b8fe8; }
""")


def apply_theme(window, theme, *, persist=True):
    theme = "dark" if theme == "dark" else "light"
    QApplication.instance().setProperty("forensikadaTheme", theme)
    palette = QPalette(QApplication.palette())
    # Set both themes explicitly so switching back cannot retain dark native controls.
    for role, color in ((QPalette.ColorRole.Window, "#f5f5f9"),
                        (QPalette.ColorRole.Base, "#ffffff"),
                        (QPalette.ColorRole.AlternateBase, "#f3f4f9"),
                        (QPalette.ColorRole.Button, "#ffffff"),
                        (QPalette.ColorRole.Text, "#20262d"),
                        (QPalette.ColorRole.WindowText, "#20262d"),
                        (QPalette.ColorRole.ButtonText, "#20262d"),
                        (QPalette.ColorRole.Mid, "#c6c9d5"),
                        (QPalette.ColorRole.Highlight, "#494cb0"),
                        (QPalette.ColorRole.HighlightedText, "#ffffff")):
        palette.setColor(role, QColor(_navy_color(color)))
    if theme == "dark":
        for role, color in ((QPalette.ColorRole.Window, "#232c35"),
                            (QPalette.ColorRole.Base, "#252e38"),
                            (QPalette.ColorRole.AlternateBase, "#303b48"),
                            (QPalette.ColorRole.Button, "#303841"),
                            (QPalette.ColorRole.Text, "#e2e7ee"),
                            (QPalette.ColorRole.WindowText, "#e2e7ee"),
                            (QPalette.ColorRole.ButtonText, "#e2e7ee"),
                            (QPalette.ColorRole.Mid, "#536071"),
                            (QPalette.ColorRole.Highlight, "#8b8fe8")):
            palette.setColor(role, QColor(_navy_color(color)))
    window.setPalette(palette)
    stylesheet = load_stylesheet()
    window.setStyleSheet(stylesheet)
    # These surfaces also work standalone and therefore own a stylesheet.
    for widget in window.findChildren(QWidget):
        if widget.objectName() in ("configurationDialog", "multiCameraWorkspace", "observationReviewDialog", "settingsDialog"):
            widget.setStyleSheet(stylesheet)
    if persist:
        QSettings("ForensiKada", "VideoDetection").setValue("appearance/theme", theme)
