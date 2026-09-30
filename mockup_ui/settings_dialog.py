"""Small settings popup with explicit Save/Cancel and persisted defaults."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from mockup_ui.preferences import Preferences, load_preferences, save_preferences
from mockup_ui.ui_theme import current_theme, load_stylesheet
from mockup_ui.motion import (SpringButton as QPushButton, SpringSwitch,
    SpringDialog as QDialog, finish_motion)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("System settings")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(load_stylesheet())
        self.setMinimumSize(560, 480)
        screen = self.screen().availableGeometry()
        self.resize(min(670, screen.width() - 40), min(780, screen.height() - 40))
        self.saved_preferences = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        def text(value, name):
            label = QLabel(value)
            label.setObjectName(name)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            return label

        hero = QFrame()
        hero.setObjectName("configHero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 20, 28, 20)
        hero_layout.setSpacing(7)
        hero_layout.addWidget(text("WORKSPACE PREFERENCES", "configEyebrow"))
        hero_layout.addWidget(text("System settings", "configHeading"))
        hero_layout.addWidget(text("Appearance, analysis defaults and saved reports.", "configSourceDetail"))
        root.addWidget(hero)

        content_frame = QFrame()
        content_frame.setObjectName("configBody")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setSpacing(13)
        scroll = QScrollArea()
        scroll.setObjectName("configScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("configScrollBody")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(12)
        scroll.setWidget(content)
        content_layout.addWidget(scroll, 1)

        def section(title):
            card = QFrame()
            card.setObjectName("configCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(6)
            layout.addWidget(text(title, "configCardTitle"))
            body.addWidget(card)
            return layout

        appearance = section("Appearance & playback")
        row = QHBoxLayout()
        theme_label = QLabel("Color theme")
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Color theme")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setMinimumWidth(140)
        theme_label.setBuddy(self.theme_combo)
        row.addWidget(theme_label, 1)
        row.addWidget(self.theme_combo)
        appearance.addLayout(row)
        self.autoplay_checkbox = SpringSwitch("Play annotated videos after analysis")
        appearance.addWidget(self.autoplay_checkbox)
        appearance.addWidget(text("Start playback when single-video or multi-camera analysis finishes.", "configCardDescription"))
        self.reduce_motion_checkbox = SpringSwitch("Reduce motion")
        appearance.addWidget(self.reduce_motion_checkbox)

        detection = section("Detection defaults")
        detection.addWidget(text("Defaults for new imports and analysis setups. Adjustable before each run.", "configCardDescription"))
        row = QHBoxLayout()
        confidence_label = QLabel("Confidence threshold")
        self.confidence_spin = QSpinBox()
        self.confidence_spin.setRange(10, 95)
        self.confidence_spin.setSuffix("%")
        self.confidence_spin.setSingleStep(5)
        self.confidence_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.confidence_spin.setMinimumWidth(94)
        self.confidence_spin.setAccessibleName("Default confidence threshold")
        self.confidence_slider = QSlider(Qt.Orientation.Horizontal)
        self.confidence_slider.setRange(10, 95)
        self.confidence_slider.setMinimumWidth(120)
        self.confidence_slider.setMinimumHeight(28)
        self.confidence_slider.setAccessibleName("Default confidence threshold slider")
        self.confidence_slider.valueChanged.connect(self.confidence_spin.setValue)
        self.confidence_spin.valueChanged.connect(self.confidence_slider.setValue)
        confidence_label.setBuddy(self.confidence_spin)
        row.addWidget(confidence_label)
        row.addSpacing(14)
        row.addWidget(self.confidence_slider, 1)
        row.addWidget(self.confidence_spin)
        detection.addLayout(row)
        detection.addWidget(text("Lower values show more candidates; higher values require greater confidence.", "configCardDescription"))
        self.intelligence_checkbox = SpringSwitch("CCTV intelligence")
        detection.addWidget(self.intelligence_checkbox)
        detection.addWidget(text("Check person proximity, motion and object scale.", "configCardDescription"))
        self.temporal_checkbox = SpringSwitch("Temporal consistency")
        detection.addWidget(self.temporal_checkbox)
        detection.addWidget(text("Check nearby frames for persistence. Requires CCTV intelligence.", "configCardDescription"))
        self.intelligence_checkbox.toggled.connect(self._toggle_intelligence)

        exports = section("Export location")
        exports.addWidget(text("Start the Save video + report dialog in this folder.", "configCardDescription"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setAccessibleName("Default export folder")
        self.folder_edit.setPlaceholderText(str(Path.home()))
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setAutoDefault(False)
        self.browse_button.clicked.connect(self.choose_folder)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.browse_button)
        exports.addLayout(row)
        body.addStretch()

        self.error_label = text("", "settingsError")
        self.error_label.hide()
        content_layout.addWidget(self.error_label)
        actions = QFrame()
        actions.setObjectName("configActions")
        buttons = QHBoxLayout(actions)
        buttons.setContentsMargins(14, 11, 12, 11)
        buttons.setSpacing(12)
        self.reset_button = QPushButton("Restore defaults")
        self.reset_button.setAutoDefault(False)
        self.reset_button.setToolTip("Reset this form. Click Save settings to apply.")
        self.reset_button.clicked.connect(lambda: self.set_values(Preferences()))
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Save settings")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setDefault(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        buttons.addWidget(self.button_box)
        content_layout.addWidget(actions)
        root.addWidget(content_frame, 1)
        self.set_values(load_preferences())
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(current_theme()))

    def _toggle_intelligence(self, enabled):
        self.temporal_checkbox.setEnabled(enabled)
        if not enabled:
            self.temporal_checkbox.setChecked(False)

    def set_values(self, values):
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(values.theme))
        self.confidence_spin.setValue(values.confidence_percent)
        self.intelligence_checkbox.setChecked(values.cctv_intelligence)
        self._toggle_intelligence(values.cctv_intelligence)
        self.temporal_checkbox.setChecked(values.cctv_intelligence and values.temporal_consistency)
        self.autoplay_checkbox.setChecked(values.autoplay_results)
        self.reduce_motion_checkbox.setChecked(values.reduce_motion)
        self.folder_edit.setText(values.export_folder)
        self.folder_edit.setToolTip(values.export_folder or str(Path.home()))
        self.error_label.hide()

    def choose_folder(self):
        start = self.folder_edit.text() or str(Path.home())
        if not Path(start).is_dir():
            start = str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Choose default export folder", start)
        if folder:
            self.folder_edit.setText(folder)
            self.folder_edit.setToolTip(folder)
            self.error_label.hide()

    def accept(self):
        values = Preferences(
            theme=self.theme_combo.currentData(), confidence_percent=self.confidence_spin.value(),
            cctv_intelligence=self.intelligence_checkbox.isChecked(),
            temporal_consistency=self.intelligence_checkbox.isChecked() and self.temporal_checkbox.isChecked(),
            autoplay_results=self.autoplay_checkbox.isChecked(), export_folder=self.folder_edit.text(),
            reduce_motion=self.reduce_motion_checkbox.isChecked(),
        )
        if values.export_folder and not Path(values.export_folder).is_dir():
            self.error_label.setText("That export folder is unavailable. Choose an existing folder or restore defaults.")
            self.error_label.show()
            return
        try:
            save_preferences(values)
        except OSError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.saved_preferences = values
        finish_motion()
        super().accept()
