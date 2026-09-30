"""Geometry and state-preservation checks for the shared page shell."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from mockup_ui.app import MainWindow, DetectionConfigDialog
from mockup_ui.test_multi_camera import make_video


class AppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / "temp")
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.settings = QSettings(str(folder / "preferences.ini"), QSettings.Format.IniFormat)
        patcher = patch("mockup_ui.preferences.settings_store", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.videos = [make_video(folder / f"CAM{i}.mp4", f"CAMERA {i}") for i in (1, 2)]
        self.window = MainWindow()
        self.window.set_theme("light", persist=False)
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: self.window.set_theme("light", persist=False))
        self.window.open_multi_camera([str(video.path) for video in self.videos])
        self.window.show()

    def bounds(self, widget):
        return widget.rect().translated(widget.mapTo(self.window, QPoint()))

    def test_tab_switch_keeps_columns_headings_and_metrics_stationary(self):
        for width, height in ((1450, 880), (1060, 720)):
            self.window.resize(width, height)
            for theme in ("light", "dark"):
                self.window.set_theme(theme, persist=False)
                snapshots = []
                for mode in (0, 1, 0, 1):
                    self.window.mode_tabs.setCurrentIndex(mode)
                    QTest.qWait(50)
                    owner = self.window if mode == 0 else self.window._multi_camera_window
                    shell = self.window.pages.widget(0) if mode == 0 else owner.page_shell
                    heading = shell.widget(1).findChild(QLabel, "workspaceTitle")
                    snapshots.append([
                        *(self.bounds(shell.widget(i)) for i in range(3)),
                        self.bounds(shell.widget(0).findChild(QLabel, "sectionTitle")),
                        (self.bounds(heading).topLeft(), heading.height()),
                        *(self.bounds(value.parentWidget()) for value in
                          (owner.total_metric, owner.handgun_metric, owner.knife_metric)),
                    ])
                for snapshot in snapshots[1:]:
                    self.assertEqual(snapshot, snapshots[0])

    def test_settings_preserve_camera_state_and_save_defaults(self):
        multi = self.window._multi_camera_window
        multi.scene.setText("Incident 004")
        multi.seek.setValue(200)
        paths = [card[0].path for card in multi.cards]
        frames = [card[0].frame_number for card in multi.cards]
        self.window.show_settings()
        settings = self.window._settings_dialog
        settings.theme_combo.setCurrentIndex(settings.theme_combo.findData("dark"))
        settings.confidence_spin.setValue(75)
        settings.accept()
        self.assertEqual(self.settings.value("appearance/theme"), "dark")
        self.assertEqual(self.settings.value("analysis/default_confidence_percent"), 75)
        self.assertEqual(multi.scene.text(), "Incident 004")
        self.assertEqual(paths, [card[0].path for card in multi.cards])
        self.assertEqual(frames, [card[0].frame_number for card in multi.cards])
        self.assertEqual(self.app.property("forensikadaTheme"), "dark")
        self.assertEqual(multi.threshold, 50)
        dialog = DetectionConfigDialog(self.videos[0], 74, self.window)
        self.addCleanup(dialog.close)
        self.assertIn("#101b32", dialog.styleSheet())
        self.window.set_theme("light", persist=False)
        self.assertEqual(dialog.slider.value(), 74)
        self.assertNotIn("#101b32", dialog.styleSheet())
        sidebar = self.window.pages.widget(0).widget(0)
        self.assertTrue(sidebar.isAncestorOf(self.window.enhancement_button))
