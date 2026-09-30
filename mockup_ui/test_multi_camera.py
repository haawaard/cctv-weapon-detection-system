"""Synthetic multi-camera integration checks; no test predictions enter production."""
import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton, QTableWidget

from mockup_ui.app import DetectionConfigDialog, MainWindow
from mockup_ui.model_bridge import VideoAnalysisResult, VideoInfo, read_video
from mockup_ui.multi_camera import Alignment, CameraSource, build_timeline, export_session, validate_session
from mockup_ui.multi_camera_panel import CameraPreviewDialog, CameraSetupDialog, MultiCameraDialog, MultiCameraReportDialog
from mockup_ui.observation_review import ReviewStore


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.sources = [CameraSource(VideoInfo(Path(f"TEST_CAM_{i}.mp4"), 320, 180, 10, 100, 10, None), f"CAM-{i}") for i in (1, 2)]
        self.alignment = Alignment("TEST-SCENE", True, "Synthetic shared clock", .25)

    def result(self, time, label="knife"):
        return SimpleNamespace(run_id=f"TEST-{time}-{label}", detections=[{
            "frame_number": round(time * 10), "timestamp_seconds": time, "class_name": label,
            "confidence": .8, "box": [1, 2, 3, 4], "automated_validation_status": "VALIDATED_TEMPORAL"}])

    def test_requires_correspondence_and_distinct_cameras(self):
        rows, metrics = build_timeline(self.sources, [self.result(1), self.result(1.1)], Alignment())
        self.assertTrue(all(row["corroboration_status"] == "Not Applicable" for row in rows))
        self.assertIsNone(metrics["mccr_percent"])
        self.sources[1].camera_id = self.sources[0].camera_id
        with self.assertRaisesRegex(ValueError, "different"):
            validate_session(self.sources, self.alignment)

    def test_offsets_enable_same_class_support_without_cross_view_iou(self):
        self.sources[1].offset_seconds = 2
        results = [self.result(3), self.result(1.1)]
        results[1].detections[0]["box"] = [200, 50, 300, 170]
        rows, metrics = build_timeline(self.sources, results, self.alignment)
        self.assertEqual(metrics["mccr_percent"], 100)
        self.assertEqual([row["session_seconds"] for row in rows], [3, 3.1])
        self.assertTrue(all(row["supporting_observations"] for row in rows))

    def test_conflicts_and_non_overlapping_footage_have_separate_outcomes(self):
        rows, metrics = build_timeline(self.sources, [self.result(1), self.result(1.1, "handgun")], self.alignment)
        self.assertEqual(metrics["counts"], {"Uncertain": 2})
        self.assertIsNone(metrics["mccr_percent"])
        self.sources[1].offset_seconds = 20
        rows, metrics = build_timeline(self.sources, [self.result(1), self.result(1.1)], self.alignment)
        self.assertEqual(metrics["counts"], {"Not Applicable": 2})

    def test_missing_support_is_zero_and_empty_detections_are_na(self):
        rows, metrics = build_timeline(self.sources, [self.result(1), self.result(8)], self.alignment)
        self.assertEqual(metrics["mccr_percent"], 0)
        rows, metrics = build_timeline(self.sources, [SimpleNamespace(run_id="A", detections=[]), SimpleNamespace(run_id="B", detections=[])], self.alignment)
        self.assertIsNone(metrics["mccr_percent"])


def make_video(path, caption):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (480, 270))
    assert writer.isOpened()
    for frame in range(12):
        image = np.full((270, 480, 3), (40, 45, 58), dtype=np.uint8)
        cv2.putText(image, caption, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, .7, (220, 230, 240), 1)
        cv2.putText(image, "SYNTHETIC UI TEST", (20, 235), cv2.FONT_HERSHEY_SIMPLEX, .65, (170, 185, 210), 1)
        cv2.rectangle(image, (180 + frame, 90), (270 + frame, 160), (75, 130, 190), 2)
        writer.write(image)
    writer.release()
    return read_video(path)


def fake_result(video, camera_id):
    folder = video.path.parent / camera_id
    folder.mkdir(exist_ok=True)
    output = folder / "annotated.mp4"
    output.write_bytes(video.path.read_bytes())
    detections = [{"frame_number": 2, "timestamp_seconds": .2, "class_name": "knife", "confidence": .8,
                   "box": [180, 90, 270, 160], "automated_validation_status": "VALIDATED_TEMPORAL"}]
    csv_path = folder / "detections.csv"
    with csv_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frame_number", "timestamp_seconds", "class_name", "confidence", "x1", "y1", "x2", "y2"])
        writer.writerow([2, .2, "knife", .8, 180, 90, 270, 160])
    result = VideoAnalysisResult(video, output, csv_path, detections, "SYNTHETIC_MODEL.pth", "cpu", .1, .5, 12)
    result.review_path = folder / "reviews.json"
    return result


class MultiCameraUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / "temp")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.videos = [make_video(self.folder / f"TEST_CAM_{i}.mp4", f"CAMERA {i}") for i in (1, 2)]
        self.window = MultiCameraDialog(self.videos)
        self.addCleanup(self.window.close)

    def test_batch_processing_playback_review_export_and_reset(self):
        self.window.scene.setText("TEST-SCENE")
        self.window.alignment.method = "Synthetic shared start"
        self.window.confirmed.setChecked(True)
        self.window.capture_settings()
        calls = []
        def analyze(video, threshold, progress, camera_id):
            calls.append(camera_id)
            progress("Synthetic fixture", 100)
            return fake_result(video, camera_id)
        self.window.bridge.analyze_video = analyze
        self.window.start_analysis()
        self.assertFalse(self.window.results)
        self.assertFalse(self.window.setup.isEnabled())
        deadline = time.monotonic() + 5
        while self.window.worker and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertIsNone(self.window.worker)
        self.window.pause()
        self.assertEqual(calls, ["CAM-01", "CAM-02"])
        self.assertEqual(self.window.timeline.rowCount(), 2)
        self.assertEqual(self.window.mccr_value.text(), "100.00%")
        self.assertEqual(self.window.total_metric.text(), "2")
        self.assertEqual(self.window.knife_metric.text(), "2")
        self.assertEqual(self.window.cards[0][0].path, self.window.results[0].output_path)
        self.window.seek.setValue(200)
        self.assertEqual([card[0].frame_number for card in self.window.cards], [2, 2])
        store = ReviewStore.for_result(self.window.results[0])
        store.save_review(store.observations[0]["observationId"], "Reject", "Synthetic reason")
        exported = export_session(self.window.sources, self.window.results, self.window.alignment, self.folder)
        manifest = json.loads((exported / "session.json").read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["metrics"]["mccr_percent"], 100)
        self.assertEqual(manifest["observations"][0]["analyst_decision"], "Reject")
        self.assertEqual(len(manifest["cameras"]), 2)
        self.assertTrue((exported / "forensic_report.pdf").is_file())
        self.assertEqual(set(exported.rglob("*.pdf")), {
            exported / "forensic_report.pdf",
            *(exported / camera["export_folder"] / "forensic_report.pdf" for camera in manifest["cameras"]),
        })
        document = QPdfDocument(self.app)
        self.assertEqual(document.load(str(exported / "forensic_report.pdf")), QPdfDocument.Error.None_)
        self.assertGreater(document.pagePointSize(0).width(), document.pagePointSize(0).height())
        report_text = "\n".join(document.getAllText(page).text() for page in range(document.pageCount()))
        for expected in ("Forensic Detection Report", "Video Metadata", "CAM-01", "CAM-02",
                         "Object Detection Observations and Reviews", "Model Performance Metrics",
                         "Analyst Review Information", "Source References and Traceability",
                         "Session time", "Cross-view"):
            self.assertIn(expected, report_text)
        headings = ("Video Metadata", "Video and Detection Information", "Interpretation",
                    "Model Performance Metrics", "Object Detection Observations and Reviews",
                    "Analyst Review Information", "Source References and Traceability")
        positions = [report_text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("TCR Information", report_text)
        self.assertNotIn("MCCR Information", report_text)
        document.close()
        document.deleteLater()
        del document
        self.app.processEvents()
        QTest.qWait(20)
        for camera in manifest["cameras"]:
            camera_folder = exported / camera["export_folder"]
            self.assertTrue((camera_folder / "annotated.mp4").is_file())
            self.assertTrue((camera_folder / "forensic_report.json").is_file())
            self.assertTrue((camera_folder / "forensic_records.csv").is_file())
            individual = QPdfDocument(self.app)
            self.assertEqual(individual.load(str(camera_folder / "forensic_report.pdf")), QPdfDocument.Error.None_)
            self.assertGreater(individual.pagePointSize(0).height(), individual.pagePointSize(0).width())
            text = "\n".join(individual.getAllText(page).text() for page in range(individual.pageCount()))
            self.assertIn(camera["camera_id"], text)
            self.assertIn("Temporal Consistency Rate (TCR)", text)
            self.assertNotIn("MCCR", text)
            self.assertNotIn("Cross-camera", text)
            individual.close()
            individual.deleteLater()
        self.window.configure()
        self.assertEqual(self.window.results, [])
        self.assertTrue(self.window.setup.isEnabled())

    def test_detection_progress_popup_is_centered_and_tracks_all_cameras(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.window.show()
        def analyze(video, threshold, progress, camera_id):
            progress(f"{camera_id} scanning", 40)
            gate.wait(3)
            return fake_result(video, camera_id)
        self.window.bridge.analyze_video = analyze
        self.window.start_analysis()
        try:
            deadline = time.monotonic() + 3
            while self.window.progress_bar.value() != 20 and time.monotonic() < deadline:
                QTest.qWait(10)
            popup = self.window._processing_dialog
            self.assertIsNotNone(popup)
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.camera_count, 2)
            deadline = time.monotonic() + 1
            while popup.progress.value() != 20 and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(popup.progress.value(), 20)
            self.assertIn("CAM-01", popup.message.text())
            self.assertEqual([step.property("state") for step in popup.stage_labels],
                             ["done", "active", "pending"])
            self.assertEqual(popup.engine_badge.text(), "FASTER R-CNN · LIVE")
            self.assertEqual(popup.scan_wave.bar_count, 9)
            phase = popup.scan_wave.phase
            QTest.qWait(40)
            self.assertNotEqual(popup.scan_wave.phase, phase)
            self.assertLessEqual((popup.frameGeometry().center() - self.window.frameGeometry().center()).manhattanLength(), 12)
        finally:
            gate.set()
            deadline = time.monotonic() + 5
            while self.window.worker and time.monotonic() < deadline:
                QTest.qWait(10)
        self.assertIsNone(self.window.worker)
        self.assertIsNone(self.window._processing_dialog)

    def test_enhanced_camera_is_the_input_to_detection(self):
        original = self.window.sources[0].video.path
        enhanced = make_video(self.folder / "enhanced_cam_1.mp4", "ENHANCED CAMERA 1")
        with patch("mockup_ui.app.VideoEnhancementDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = 1
            dialog.enhanced_video_path = enhanced.path
            self.window.enhance_camera(0)
            self.assertIn("output_path", dialog_type.call_args.kwargs)
        self.assertEqual(self.window.sources[0].video.path, enhanced.path)
        self.assertEqual(self.window.sources[0].original_video_path, original)
        self.assertEqual(self.window.sources[1].video.path, self.videos[1].path)
        self.assertIn("Enhanced", self.window.cards[0][2].text())
        inputs = []
        def analyze(video, threshold, progress, camera_id):
            inputs.append(video.path)
            return fake_result(video, camera_id)
        self.window.bridge.analyze_video = analyze
        self.window.start_analysis()
        deadline = time.monotonic() + 5
        while self.window.worker and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertEqual(inputs, [enhanced.path, self.videos[1].path])
        exported = export_session(self.window.sources, self.window.results, self.window.alignment, self.folder)
        cameras = json.loads((exported / "session.json").read_text())["cameras"]
        self.assertEqual(cameras[0]["source_video"], str(enhanced.path))
        self.assertEqual(cameras[0]["original_source_video"], str(original))
        self.assertIsNone(cameras[1]["original_source_video"])

    def test_multi_camera_is_embedded_and_uses_shared_toolbar(self):
        main = MainWindow()
        self.addCleanup(main.close)
        main.open_multi_camera([str(video.path) for video in self.videos])
        self.assertIs(main.pages.currentWidget(), main._multi_camera_window)
        self.assertEqual(main.mode_tabs.currentIndex(), 1)
        self.assertEqual(len([button for button in main.findChildren(QPushButton) if button.text() == "Import videos"]), 1)
        self.assertFalse(main.report_button.isEnabled())
        self.assertFalse(main.save_button.isEnabled())
        multi = main._multi_camera_window
        multi.scene.setText("TEST-SCENE")
        multi.confirmed.setChecked(True)
        multi.capture_settings()
        multi.bridge.analyze_video = lambda video, threshold, progress, camera_id: fake_result(video, camera_id)
        multi.start_analysis()
        deadline = time.monotonic() + 5
        while multi.worker and time.monotonic() < deadline:
            QTest.qWait(20)
        multi.pause()
        self.assertTrue(main.report_button.isEnabled())
        self.assertTrue(main.save_button.isEnabled())
        with patch.object(multi, "show_report") as report:
            main.show_forensic_report()
            report.assert_called_once()
        with patch.object(multi, "export") as export:
            main.save_current_result()
            export.assert_called_once()
        main.mode_tabs.setCurrentIndex(0)
        self.assertFalse(main.report_button.isEnabled())
        self.assertFalse(main.save_button.isEnabled())
        previous = main._multi_camera_window
        main.open_multi_camera([str(video.path) for video in self.videos])
        self.assertIsNot(main._multi_camera_window, previous)
        self.assertEqual(main.pages.count(), 2)
        self.assertFalse(main.report_button.isEnabled())
        self.assertFalse(main.save_button.isEnabled())

    def test_combined_report_includes_each_camera_and_timeline(self):
        results = [fake_result(video, f"CAM-{index:02d}") for index, video in enumerate(self.videos, 1)]
        self.window.scene.setText("TEST-SCENE")
        self.window.confirmed.setChecked(True)
        self.window.capture_settings()
        self.window.completed(results)
        self.window.pause()
        report = MultiCameraReportDialog(self.window.sources, results, self.window.alignment,
                                         self.window.rows, self.window)
        tables = report.findChildren(QTableWidget)
        self.assertEqual([table.rowCount() for table in tables], [2, 2])
        self.assertEqual(tables[0].columnCount(), 6)
        self.assertEqual(tables[0].item(0, 0).text(), "CAM-01")
        self.assertEqual(tables[1].item(0, 1).text(), "CAM-01")
        self.assertFalse(any(button.text() == "Open camera report" for button in report.findChildren(QPushButton)))
        report.close()

    def test_new_import_and_close_release_the_previous_session(self):
        main = MainWindow()
        self.addCleanup(main.close)
        main.load_video(str(self.videos[0].path))
        main._analysis_succeeded(fake_result(self.videos[0], "SINGLE"))
        main.player.canvas.set_zoom(2)
        first_capture = main.player.capture
        self.assertTrue(main.player.timer.isActive())

        main.open_multi_camera([str(video.path) for video in self.videos])
        self.assertFalse(first_capture.isOpened())
        self.assertIsNone(main.player.capture)
        self.assertIsNone(main.result)
        self.assertIsNone(main.video_info)
        self.assertEqual(main.player.canvas.zoom, 1)
        multi = main._multi_camera_window
        results = [fake_result(video, f"CAM-{i}") for i, video in enumerate(self.videos)]
        multi.completed(results)
        captures = [card[0].capture for card in multi.cards]
        preview = CameraPreviewDialog(results[0].output_path, "Test", parent=multi)
        preview_capture = preview.player.capture
        preview.reject()
        self.assertFalse(preview_capture.isOpened())

        main.load_video(str(self.videos[1].path))
        self.assertTrue(all(not capture.isOpened() for capture in captures))
        self.assertFalse(multi.play_timer.isActive())
        self.assertIsNone(main._multi_camera_window)
        self.assertEqual(main.pages.count(), 1)
        self.assertFalse(main.mode_tabs.isTabEnabled(1))
        self.assertIsNone(main.result)
        self.assertFalse(main.report_button.isEnabled())
        self.assertEqual(main.observation_model.rowCount(), 0)
        self.assertTrue(main.close_videos_button.isEnabled())
        self.assertEqual(main.player.path, self.videos[1].path)
        self.assertTrue(main.analyze_button.isEnabled())

        main._analysis_succeeded(fake_result(self.videos[1], "NEXT"))
        current_capture = main.player.capture
        main.close_videos_button.click()
        self.assertFalse(current_capture.isOpened())
        self.assertIsNone(main.player.path)
        self.assertIsNone(main.source_path)
        self.assertIsNone(main.video_info)
        self.assertIsNone(main.result)
        self.assertFalse(main.player.timer.isActive())
        self.assertFalse(main._model_retry.isActive())
        self.assertFalse(main.player.canvas.property("hasImage"))
        self.assertFalse(main.save_button.isEnabled())
        self.assertFalse(main.close_videos_button.isEnabled())
        self.assertTrue(main.open_button.isEnabled())
        self.assertTrue(results[0].output_path.exists())
        main.open_multi_camera([str(video.path) for video in self.videos])
        self.assertEqual(len(main._multi_camera_window.sources), 2)
        self.assertFalse(main._multi_camera_window.results)

    def test_invalid_import_preserves_session_and_busy_session_cannot_close(self):
        main = MainWindow()
        self.addCleanup(main.close)
        main.open_multi_camera([str(video.path) for video in self.videos])
        session = main._multi_camera_window
        with patch.object(main, "_show_error") as error:
            main.load_video(str(self.folder / "missing.mp4"))
            error.assert_called_once()
        self.assertIs(main._multi_camera_window, session)
        session.worker = object()
        try:
            main._sync_header_actions()
            self.assertFalse(main.close_videos_button.isEnabled())
            self.assertFalse(main.close_videos())
            self.assertIs(main._multi_camera_window, session)
            self.assertTrue(all(card[0].capture.isOpened() for card in session.cards))
        finally:
            session.worker = None

    def test_camera_views_and_timeline_adapt_to_window_width(self):
        main = MainWindow()
        self.addCleanup(main.close)
        main.resize(1450, 880)
        main.open_multi_camera([str(video.path) for video in self.videos])
        main.show()
        QTest.qWait(50)
        multi = main._multi_camera_window
        self.assertEqual(multi._grid_columns, 2)
        self.assertFalse(multi.timeline.isColumnHidden(2))
        main.resize(1060, 720)
        QTest.qWait(50)
        self.assertEqual(multi._grid_columns, 1)
        self.assertTrue(multi.timeline.isColumnHidden(2))
        self.assertTrue(multi.timeline.isColumnHidden(4))

    def test_multi_file_picker_routes_to_workspace(self):
        main = MainWindow()
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(v.path) for v in self.videos], "")), patch.object(main, "open_multi_camera") as opened:
            main.choose_video()
            opened.assert_called_once_with([str(v.path) for v in self.videos])
        main.close()

    def test_config_has_readable_controls_and_temporal_dependency(self):
        dialog = DetectionConfigDialog(self.videos[0], 60, videos=self.videos)
        self.assertEqual(dialog.slider.value(), 60)
        dialog.cctv_intel_checkbox.setChecked(False)
        self.assertFalse(dialog.temporal_checkbox.isChecked())
        self.assertFalse(dialog.temporal_checkbox.isEnabled())
        dialog.close()

    def test_simple_setup_and_preview_zoom_controls(self):
        button_texts = [button.text() for button in self.window.findChildren(QPushButton)]
        self.assertIn("Camera and timing details", button_texts)
        self.assertEqual(button_texts.count("Preview"), 2)
        self.assertNotIn("Maximize", button_texts)
        self.assertNotIn("Restore", button_texts)
        self.assertFalse(any(editor for editor in self.window.editors))
        details = CameraSetupDialog(self.window.sources, self.window.alignment, self.window)
        details.editors[0][1].setText("Entry view")
        details.editors[1][2].setValue(1.25)
        details.apply()
        self.assertEqual(self.window.sources[0].location, "Entry view")
        self.assertEqual(self.window.sources[1].offset_seconds, 1.25)
        details.close()
        preview = CameraPreviewDialog(self.videos[0].path, "TEST CAM", 2, self.window)
        preview.show()
        QTest.qWait(20)
        preview.player.canvas.set_zoom(2.0)
        self.assertEqual(preview.player.canvas.zoom, 2.0)
        QTest.mouseDClick(preview.player.canvas, Qt.MouseButton.LeftButton)
        self.assertEqual(preview.player.canvas.zoom, 1.0)
        preview_buttons = [button.text() for button in preview.findChildren(QPushButton)]
        self.assertEqual(preview_buttons, ["Play", "Start"])
        self.assertTrue(preview.player.start_button.isHidden())
        self.assertNotIn("Maximize", preview_buttons)
        self.assertNotIn("Restore", preview_buttons)
        preview.close()


if __name__ == "__main__":
    unittest.main()
