"""Motion lifecycle and persisted accessibility preference regression tests."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from mockup_ui.motion import SpringButton, SpringSwitch, SpringTabs, SpringDialog, finish_motion
from mockup_ui.preferences import Preferences, load_preferences, save_preferences
from mockup_ui.settings_dialog import SettingsDialog


class MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = QSettings(folder.name + '/prefs.ini', QSettings.Format.IniFormat)
        patcher = patch('mockup_ui.preferences.settings_store', return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_switch_reverses_and_keyboard_activation_works(self):
        switch = SpringSwitch('Playback')
        self.addCleanup(switch.close)
        switch.show()
        switch.setChecked(True)
        QTest.qWait(80)
        switch.setChecked(False)
        QTest.qWait(460)
        self.assertAlmostEqual(switch._position, 0)
        QTest.keyClick(switch, Qt.Key.Key_Space)
        self.assertTrue(switch.isChecked())

    def test_button_hit_target_and_tabs_stay_stable(self):
        button = SpringButton('Accept')
        self.addCleanup(button.close)
        button.show()
        geometry = button.geometry()
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        QTest.qWait(120)
        self.assertEqual(button.geometry(), geometry)
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
        QTest.qWait(460)
        self.assertAlmostEqual(button._inset, 0)
        tabs = SpringTabs()
        self.addCleanup(tabs.close)
        tabs.addTab('Single video')
        tabs.addTab('Multi-camera')
        tabs.show()
        tabs.setCurrentIndex(1)
        QTest.qWait(580)
        self.assertEqual(tabs._indicator.toRect(), tabs.tabRect(1))

    def test_settings_save_cancel_and_reduce_motion(self):
        dialog = SettingsDialog()
        self.addCleanup(dialog.close)
        dialog.reduce_motion_checkbox.setChecked(True)
        dialog.reject()
        self.assertFalse(load_preferences().reduce_motion)
        dialog.accept()
        self.assertTrue(load_preferences().reduce_motion)
        switch = SpringSwitch('Playback')
        self.addCleanup(switch.close)
        switch.show()
        switch.setChecked(True)
        self.assertEqual(switch._position, 1)
        self.assertEqual(switch._motion.duration(), 0)

    def test_dialog_close_during_entrance_restores_position(self):
        dialog = SpringDialog()
        dialog.resize(250, 180)
        dialog.move(100, 100)
        self.addCleanup(dialog.close)
        dialog.show()
        QTest.qWait(80)
        target = dialog._motion_target
        dialog.close()
        self.assertEqual(dialog.pos(), target)
        dialog.show()
        QTest.qWait(640)
        self.assertEqual(dialog.pos(), target)

    def test_reduced_motion_settles_active_switch_and_tabs(self):
        switch = SpringSwitch('Playback')
        tabs = SpringTabs()
        for widget in (switch, tabs):
            self.addCleanup(widget.close)
        tabs.addTab('Single')
        tabs.addTab('Multi')
        switch.show()
        tabs.show()
        switch.setChecked(True)
        tabs.setCurrentIndex(1)
        QTest.qWait(50)
        save_preferences(Preferences(reduce_motion=True))
        finish_motion()
        self.assertEqual(switch._position, 1)
        self.assertEqual(tabs._indicator.toRect(), tabs.tabRect(1))


if __name__ == '__main__':
    unittest.main()
