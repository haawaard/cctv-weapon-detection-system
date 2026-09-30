"""Headless export regression checks; fixtures contain no real predictions.

Requires the optional pypdf test reader (included in the Codex document runtime).
"""
import csv
import json
from pathlib import Path
import tempfile
import unittest

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from mockup_ui.model_bridge import VideoAnalysisResult, VideoInfo
from mockup_ui.multi_camera import Alignment, CameraSource, export_session
from mockup_ui.observation_review import ReviewStore


@unittest.skipIf(PdfReader is None, "Install pypdf to run headless PDF export checks")
class PdfExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def fixture(self, camera_id, count, offset):
        folder = self.folder / camera_id
        folder.mkdir()
        source = folder / f"SYNTHETIC_{camera_id}.mp4"
        source.write_bytes(b"Synthetic file used for export only; never decoded")
        annotated = folder / "annotated.mp4"
        annotated.write_bytes(source.read_bytes())
        video = VideoInfo(source, 1280, 720, 10, 1000, 100, None)
        detections = [{"frame_number": i, "timestamp_seconds": i / 10, "class_name": "knife",
                       "confidence": .9123, "box": [i, 20, i + 10, 40]} for i in range(count)]
        csv_path = folder / "detections.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["frame_number", "timestamp_seconds", "class_name", "confidence", "x1", "y1", "x2", "y2"])
            for row in detections:
                writer.writerow([row["frame_number"], row["timestamp_seconds"], row["class_name"], row["confidence"], *row["box"]])
        result = VideoAnalysisResult(video, annotated, csv_path, detections, "SYNTHETIC_MODEL.pth", "cpu", 1, .5, 1000)
        result.review_path = folder / "reviews.json"
        return CameraSource(video, camera_id, offset_seconds=offset), result

    def export(self, counts, reviews=("Reject",)):
        pairs = [self.fixture(f"CAM-0{i+1}", count, 2 if i == 0 else 0) for i, count in enumerate(counts)]
        sources, results = zip(*pairs)
        if results[0].detections and reviews:
            store = ReviewStore.for_result(results[0])
            for observation, decision in zip(store.observations, reviews):
                store.save_review(observation["observationId"], decision, "Synthetic <review> & reason\nSecond line.")
        folder = export_session(sources, results, Alignment("TEST-SCENE", True, "Synthetic clock", .25), self.folder)
        return folder, json.loads((folder / "session.json").read_text())

    def test_combined_table_and_individual_reports_preserve_observations_and_reviews(self):
        folder, manifest = self.export([85, 3])
        paths = {folder / "forensic_report.pdf", *(folder / c["export_folder"] / "forensic_report.pdf" for c in manifest["cameras"])}
        self.assertEqual(set(folder.rglob("*.pdf")), paths)
        document = PdfReader(folder / "forensic_report.pdf")
        for page in document.pages:
            self.assertGreater(page.mediabox.width, page.mediabox.height)
        pages = [page.extract_text() for page in document.pages]
        text = "\n".join(pages)
        headings = ("Video Metadata", "Video and Detection Information", "Interpretation",
                    "Model Performance Metrics", "Object Detection Observations and Reviews",
                    "Analyst Review Information", "Source References and Traceability")
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        table_text = text.split(headings[4], 1)[1].split(headings[5], 1)[0]
        review_text = text.split(headings[5], 1)[1].split(headings[6], 1)[0]
        self.assertEqual(table_text.count("91.23%"), 88)
        self.assertNotIn("Analyst", table_text)
        self.assertNotIn("Not reviewed", review_text)
        self.assertEqual(review_text.count("-OBS-"), 1)
        self.assertIn("1 of 88 observations reviewed", review_text)
        row_positions = []
        for row in manifest["observations"]:
            row_positions.append(table_text.index(
                f"{row['session_seconds']:.3f} s\n{row['camera_id']}\n{row['video_seconds']:.3f} s\n{row['frame_number']}\nKnife\n91.23%"))
            if row["analyst_decision"] == "Reject":
                self.assertIn(row["observation_id"], review_text)
            else:
                self.assertNotIn(row["observation_id"], review_text)
        self.assertEqual(row_positions, sorted(row_positions))
        self.assertGreater(sum("Session time" in page for page in pages), 1)
        for page in pages:
            if "91.23%" in page:
                self.assertIn("Session time", page)
                self.assertIn("Cross-view", page)
        self.assertIn("Synthetic <review> & reason", text)
        self.assertIn("Multi-Camera Corroboration Rate (MCCR)", text)
        for camera in manifest["cameras"]:
            document = PdfReader(folder / camera["export_folder"] / "forensic_report.pdf")
            self.assertGreater(document.pages[0].mediabox.height, document.pages[0].mediabox.width)
            text = "\n".join(page.extract_text() for page in document.pages)
            self.assertIn(camera["camera_id"], text)
            self.assertIn("Temporal Consistency Rate (TCR)", text)
            for forbidden in ("MCCR", "Multi-Camera", "Cross-camera", "Session time", "Cross-view"):
                self.assertNotIn(forbidden, text)
            for row in manifest["observations"]:
                if row["camera_id"] == camera["camera_id"]:
                    self.assertIn(row["observation_id"], text)
                else:
                    self.assertNotIn(row["observation_id"], text)

    def test_empty_camera_still_gets_its_own_report(self):
        folder, manifest = self.export([1, 0])
        camera = manifest["cameras"][1]
        document = PdfReader(folder / camera["export_folder"] / "forensic_report.pdf")
        text = "\n".join(page.extract_text() for page in document.pages)
        self.assertIn("No handgun or knife observations met", text)
        self.assertNotIn("MCCR", text)

    def test_empty_session_has_all_sections_and_no_observation_rows(self):
        folder, _ = self.export([0, 0])
        document = PdfReader(folder / "forensic_report.pdf")
        text = "\n".join(page.extract_text() for page in document.pages)
        self.assertIn("No handgun or knife observations met", text)
        self.assertIn("0 of 0 observations reviewed", text)
        self.assertIn("Source References and Traceability", text)

    def test_unreviewed_detections_have_no_analyst_rows(self):
        folder, _ = self.export([25, 25], reviews=())
        text = "\n".join(page.extract_text() for page in PdfReader(folder / "forensic_report.pdf").pages)
        review = text.split("Analyst Review Information", 1)[1].split("Source References and Traceability", 1)[0]
        self.assertIn("0 of 50 observations reviewed", review)
        self.assertIn("No reviewed observations to display.", review)
        self.assertNotIn("Observation ID", review)
        self.assertNotIn("-OBS-", review)
        self.assertEqual(text.count("91.23%"), 50)

    def test_all_human_decisions_and_full_precision_review_times_are_shown(self):
        from datetime import datetime, timezone
        folder, manifest = self.export([4, 1], reviews=("Accept", "Reject", "Uncertain"))
        text = "\n".join(page.extract_text() for page in PdfReader(folder / "forensic_report.pdf").pages)
        review = text.split("Analyst Review Information", 1)[1].split("Source References and Traceability", 1)[0]
        self.assertIn("3 of 5 observations reviewed", review)
        self.assertEqual(review.count("-OBS-"), 3)
        self.assertNotIn("Not reviewed", review)
        for row in manifest["observations"]:
            if row["analyst_decision"] in ("Accept", "Reject", "Uncertain"):
                self.assertIn(row["analyst_decision"], review)
                self.assertIn(row["observation_id"], review)
                time = datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc)
                self.assertIn(time.date().isoformat() + "\n" + time.time().isoformat(), review)
        self.assertNotIn("+00:00", review)


if __name__ == "__main__":
    unittest.main()
