"""Preferences and popup integration checks using isolated INI settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from mockup_ui.app import DetectionConfigDialog, MainWindow
from mockup_ui.multi_camera_panel import MultiCameraDialog
from mockup_ui.preferences import Preferences, export_start_directory, load_preferences, save_preferences
from mockup_ui.test_multi_camera import make_video, fake_result


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / "temp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = QSettings(str(self.folder / "preferences.ini"), QSettings.Format.IniFormat)
        patcher = patch("mockup_ui.preferences.settings_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app.setProperty("forensikadaTheme", "light")
        self.window = MainWindow()
        self.window.show()
        self.addCleanup(self.window.close)
        self.addCleanup(lambda: self.window.set_theme("light", persist=False))

    def popup(self):
        QTest.mouseClick(self.window.settings_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        return self.window._settings_dialog

    def videos(self):
        return [make_video(self.folder / f"SYNTHETIC_CAM_{i}.mp4", f"TEST {i}") for i in (1, 2)]

    def test_button_opens_one_popup_and_cancel_discards_changes(self):
        dialog = self.popup()
        self.assertTrue(dialog.isVisible())
        self.window.show_settings()
        self.assertIs(self.window._settings_dialog, dialog)
        dialog.confidence_spin.setValue(80)
        dialog.theme_combo.setCurrentIndex(1)
        dialog.intelligence_checkbox.setChecked(False)
        self.assertFalse(dialog.temporal_checkbox.isEnabled())
        self.assertFalse(dialog.temporal_checkbox.isChecked())
        dialog.reject()
        self.assertEqual(load_preferences(), Preferences())
        self.assertEqual(self.app.property("forensikadaTheme"), "light")
        self.assertIsNone(self.window._settings_dialog)

    def test_save_persists_for_new_windows_and_analysis_setup(self):
        dialog = self.popup()
        dialog.theme_combo.setCurrentIndex(1)
        dialog.confidence_spin.setValue(75)
        dialog.intelligence_checkbox.setChecked(False)
        dialog.autoplay_checkbox.setChecked(False)
        with patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.folder)):
            dialog.choose_folder()
        dialog.accept()
        expected = Preferences("dark", 75, False, False, False, str(self.folder))
        reopened = QSettings(self.store.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(load_preferences(reopened), expected)
        self.assertEqual(self.window.threshold_slider.value(), 75)
        self.assertEqual(self.app.property("forensikadaTheme"), "dark")
        self.app.setProperty("forensikadaTheme", None)
        with patch("mockup_ui.ui_theme.QSettings", return_value=reopened):
            new_window = MainWindow()
        self.addCleanup(new_window.close)
        self.assertEqual(new_window.threshold_slider.value(), 75)
        self.assertEqual(self.app.property("forensikadaTheme"), "dark")
        videos = self.videos()
        new_window.load_video(str(videos[0].path))
        self.assertEqual(new_window.threshold_slider.value(), 75)
        multi = MultiCameraDialog(videos, self.window)
        self.addCleanup(multi.close)
        self.assertEqual(multi.threshold, 75)
        for parent in (new_window, multi):
            config = DetectionConfigDialog(videos[0], 75, parent)
            self.addCleanup(config.close)
            self.assertFalse(config.cctv_intel_checkbox.isChecked())
            self.assertFalse(config.temporal_checkbox.isChecked())
            self.assertFalse(config.temporal_checkbox.isEnabled())

    def test_save_preserves_loaded_video_and_current_run_threshold(self):
        video = self.videos()[0]
        self.window.load_video(str(video.path))
        self.window.threshold_slider.setValue(65)
        self.window.player.set_locked(False)
        self.window.player.seek_frame(4)
        dialog = self.popup()
        dialog.confidence_spin.setValue(90)
        dialog.accept()
        self.assertEqual(self.window.video_info.path, video.path)
        self.assertEqual(self.window.threshold_slider.value(), 65)
        self.assertEqual(self.window.player.frame_number, 4)
        self.window.load_video(str(video.path))
        self.assertEqual(self.window.threshold_slider.value(), 90)

    def test_restore_is_a_draft_until_saved_and_missing_folder_is_reported(self):
        saved = replace(Preferences(), confidence_percent=85, autoplay_results=False)
        save_preferences(saved)
        dialog = self.popup()
        dialog.reset_button.click()
        self.assertEqual(dialog.confidence_spin.value(), 50)
        dialog.reject()
        self.assertEqual(load_preferences(), saved)
        dialog = self.popup()
        dialog.folder_edit.setText(str(self.folder / "missing"))
        dialog.accept()
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.error_label.isHidden())
        self.assertEqual(load_preferences(), saved)
        dialog.reset_button.click()
        dialog.accept()
        self.assertEqual(load_preferences(), Preferences())

    def test_export_folder_is_used_by_both_save_dialogs(self):
        save_preferences(replace(Preferences(), export_folder=str(self.folder)))
        self.assertEqual(export_start_directory(), str(self.folder))
        videos = self.videos()
        self.window.result = fake_result(videos[0], "CAM-01")
        with patch.object(QFileDialog, "getExistingDirectory", return_value="") as choose:
            self.window.save_current_result()
        self.assertEqual(choose.call_args.args[2], str(self.folder))
        multi = MultiCameraDialog(videos, self.window)
        self.addCleanup(multi.close)
        multi.results = [self.window.result]
        with patch.object(QFileDialog, "getExistingDirectory", return_value="") as choose:
            multi.export()
        self.assertEqual(choose.call_args.args[2], str(self.folder))

    def test_autoplay_preference_controls_both_result_players(self):
        videos = self.videos()
        result = fake_result(videos[0], "CAM-01")
        self.window.load_video(str(videos[0].path))
        multi = MultiCameraDialog(videos, self.window)
        self.addCleanup(multi.close)
        for enabled in (False, True):
            save_preferences(replace(Preferences(), autoplay_results=enabled))
            with patch.object(self.window.player, "play") as play:
                self.window._analysis_succeeded(result)
            self.assertEqual(play.call_count, int(enabled))
            multi.results = [result]
            multi.worker = Mock()
            with patch.object(multi, "toggle_playback") as play_all:
                multi.worker_finished()
            self.assertEqual(play_all.call_count, int(enabled))

    def test_invalid_persisted_values_fall_back_safely(self):
        self.store.setValue("analysis/default_confidence_percent", "invalid")
        self.store.setValue("analysis/default_cctv_intelligence", "false")
        self.store.setValue("analysis/default_temporal_consistency", "true")
        self.store.setValue("playback/autoplay_results", "0")
        self.store.setValue("export/default_folder", str(self.folder / "missing"))
        values = load_preferences()
        self.assertEqual(values.confidence_percent, 50)
        self.assertFalse(values.temporal_consistency)
        self.assertFalse(values.autoplay_results)
        self.assertEqual(export_start_directory(), str(Path.home()))


if __name__ == "__main__":
    unittest.main()
