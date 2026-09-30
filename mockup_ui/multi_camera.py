"""Recorded-camera sessions and manuscript-based cross-view observation matching."""
from collections import Counter
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import uuid4

from mockup_ui.model_bridge import VideoInfo, save_result
from mockup_ui.observation_review import ReviewStore
from mockup_ui.report_metrics import calculate_report_metrics


@dataclass
class CameraSource:
    video: VideoInfo
    camera_id: str
    location: str = ""
    offset_seconds: float = 0.0
    original_video_path: Path | None = None


@dataclass
class Alignment:
    scene_id: str = ""
    confirmed: bool = False
    method: str = ""
    window_seconds: float = 1.5


def validate_session(sources, alignment):
    if len(sources) < 2:
        raise ValueError("Import at least two camera recordings.")
    identifiers = [source.camera_id.strip() for source in sources]
    if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Give each recording a different, non-empty camera identifier.")
    if len({source.video.path.resolve() for source in sources}) != len(sources):
        raise ValueError("The same video cannot be used as two independent camera recordings.")
    if any(not math.isfinite(source.offset_seconds) for source in sources):
        raise ValueError("Camera offsets must be finite numbers.")
    if not math.isfinite(alignment.window_seconds) or alignment.window_seconds <= 0:
        raise ValueError("The matching window must be greater than zero.")
    if alignment.confirmed and (not alignment.scene_id.strip() or not alignment.method.strip()):
        raise ValueError("Enter the shared scene/incident ID and the alignment method before confirming correspondence.")


def build_timeline(sources, results, alignment):
    """One row per original observation; matching never changes its model/analyst label.

    Uncertain and Not Applicable observations are excluded from the MCCR denominator,
    following the manuscript's separate reporting rule (printed pp. 70-72).
    """
    validate_session(sources, alignment)
    if len(results) != len(sources):
        raise ValueError("Every camera must finish before cross-view comparison.")
    rows = []
    for source, result in zip(sources, results, strict=True):
        for index, detection in enumerate(result.detections):
            rows.append({
                "observation_id": f"{result.run_id}-OBS-{index + 1:05d}",
                "camera_id": source.camera_id, "source_video": str(source.video.path),
                "scene_id": alignment.scene_id, "frame_number": detection["frame_number"],
                "video_seconds": detection["timestamp_seconds"],
                "session_seconds": detection["timestamp_seconds"] + source.offset_seconds,
                "object_label": detection["class_name"], "confidence": detection["confidence"],
                "box": detection["box"], "temporal_status": detection.get("automated_validation_status", "not_performed"),
                "corroboration_status": "Not Applicable", "supporting_observations": [],
                "reason": "Recording correspondence and alignment have not been confirmed.",
            })
    rows.sort(key=lambda row: (row["session_seconds"], row["camera_id"], row["frame_number"]))
    if alignment.confirmed:
        times = [row["session_seconds"] for row in rows]
        for row in rows:
            time = row["session_seconds"]
            # Compare only where BOTH recordings actually have footage, not a clamped last frame.
            peers = [source for source in sources if source.camera_id != row["camera_id"] and
                     source.offset_seconds <= time < source.offset_seconds + source.video.duration]
            if not peers:
                row["reason"] = "No corresponding camera recording covers this session time."
                continue
            nearby = rows[bisect_left(times, time - alignment.window_seconds):bisect_right(times, time + alignment.window_seconds)]
            candidates = [other for other in nearby if other["camera_id"] in {s.camera_id for s in peers} and
                          abs(other["session_seconds"] - time) <= alignment.window_seconds and
                          any(s.camera_id == row["camera_id"] and s.offset_seconds <= other["session_seconds"] <
                              s.offset_seconds + s.video.duration for s in sources)]
            matches = [other for other in candidates if other["object_label"] == row["object_label"]]
            if matches:
                row.update(corroboration_status="Corroborated", reason="Same predicted class in another aligned camera within the matching window.",
                           supporting_observations=[other["observation_id"] for other in matches])
            elif candidates:
                row.update(corroboration_status="Uncertain", reason="Another aligned camera has a different predicted class within the matching window.")
            else:
                row.update(corroboration_status="Not Corroborated", reason="Corresponding footage is available, but no supporting observation was detected within the matching window.")
    counts = Counter(row["corroboration_status"] for row in rows)
    eligible = counts["Corroborated"] + counts["Not Corroborated"]
    return rows, {"counts": dict(counts), "eligible": eligible,
                  "mccr_percent": round(100 * counts["Corroborated"] / eligible, 2) if eligible else None,
                  "uncertain_policy": "Reported separately and excluded, along with Not Applicable, from the MCCR denominator."}


def export_session(sources, results, alignment, destination):
    """Write a combined PDF and an individual PDF with saved records per camera."""
    rows, metrics = build_timeline(sources, results, alignment)
    folder = Path(destination).resolve() / f"camera-session-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "status": "exporting", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "scene_id": alignment.scene_id, "alignment_confirmed_by_analyst": alignment.confirmed,
                "alignment_method": alignment.method, "matching_window_seconds": alignment.window_seconds,
                "time_basis": "Session seconds = video-relative seconds + analyst-supplied offset. Not a recording date/time.",
                "metrics": metrics, "cameras": []}
    manifest_path = folder / "session.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for source, result in zip(sources, results, strict=True):
        exported = save_result(result, folder, include_pdf=False)
        tcr = calculate_report_metrics(exported / "metric_input.csv")["tcr"]
        camera_report = json.loads((exported / "forensic_report.json").read_text(encoding="utf-8"))
        from mockup_ui.pdf_report import write_pdf
        write_pdf(camera_report, exported / "forensic_report.pdf", camera_id=source.camera_id)
        fingerprints = {
            "source_video": camera_report["source"]["file_at_report_generation"]["sha256"],
            "detections_csv": camera_report["artifacts"]["pipeline_detections"]["sha256"],
            "summary_json": camera_report["artifacts"]["saved_summary"]["sha256"],
        }
        reviews = {r["observationId"]: r["analystReview"] for r in ReviewStore.for_result(result).observations}
        for row in rows:
            if row["camera_id"] == source.camera_id:
                review = reviews.get(row["observation_id"]) or {}
                row.update(analyst_decision=review.get("decision", "Not reviewed"), analyst_notes=review.get("notes", ""),
                           reviewed_at=review.get("reviewedAt"))
        manifest["cameras"].append({"camera_id": source.camera_id, "location": source.location,
                                    "source_video": str(source.video.path), "offset_seconds": source.offset_seconds,
                                    "original_source_video": str(source.original_video_path) if source.original_video_path else None,
                                    "width": source.video.width, "height": source.video.height,
                                    "fps": source.video.fps, "frame_count": source.video.frame_count,
                                    "duration_seconds": source.video.duration,
                                    "observation_count": len(result.detections),
                                    "analyzed_frames": result.analyzed_frames,
                                    "frames_with_detections": result.positive_frames,
                                    "confidence_threshold": result.threshold,
                                    "device": result.device,
                                    "tcr": tcr,
                                    "fingerprints": fingerprints,
                                    "model_path": result.model_path, "run_id": result.run_id,
                                    "export_folder": str(exported.relative_to(folder))})
    with (folder / "combined_observations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        keys = list(rows[0]) if rows else ["observation_id", "camera_id", "source_video", "scene_id", "frame_number", "video_seconds", "session_seconds", "object_label", "confidence", "box", "temporal_status", "corroboration_status", "supporting_observations", "reason", "analyst_decision", "analyst_notes", "reviewed_at"]
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows({**row, "box": json.dumps(row["box"]), "supporting_observations": json.dumps(row["supporting_observations"])} for row in rows)
    manifest["observations"] = rows
    from mockup_ui.multi_camera_pdf import write_session_pdf
    write_session_pdf(folder / "forensic_report.pdf", manifest)
    manifest["status"] = "completed"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return folder
