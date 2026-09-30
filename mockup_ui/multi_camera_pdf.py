"""Combined multi-camera PDF using the single-camera forensic report format."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from xml.sax.saxutils import escape

from mockup_ui.report_metrics import REPORT_DISCLAIMER as DISCLAIMER, metric_fields
from mockup_ui.observation_review import DECISIONS


def _review_timestamp(value):
    """Keep the date and full-precision UTC time on predictable separate lines."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    if moment.tzinfo is None:
        return value
    moment = moment.astimezone(timezone.utc)
    return f"{moment.date().isoformat()}\n{moment.time().isoformat()}"


def _camera_reports(path: Path, manifest: dict) -> list[tuple[dict, dict]]:
    """Load the already verified report data written for every camera export."""
    reports = []
    for camera in manifest["cameras"]:
        report_path = path.parent / camera["export_folder"] / "forensic_report.json"
        reports.append((camera, json.loads(report_path.read_text(encoding="utf-8"))))
    return reports


def _session_mccr(manifest: dict) -> dict:
    metrics = manifest["metrics"]
    counts = metrics.get("counts", {})
    eligible = metrics.get("eligible", 0)
    value = metrics.get("mccr_percent")
    confirmed = manifest.get("alignment_confirmed_by_analyst", False)
    if value is not None:
        reason = "Calculated from aligned observations across the imported camera recordings."
    elif not confirmed:
        reason = "Recording correspondence and alignment have not been confirmed."
    else:
        reason = "No eligible aligned observations are available for this session."
    metric = {
        "status": "computed" if value is not None else "unavailable",
        "value_percent": value,
        "reason": reason,
        "n_cc": counts.get("Corroborated", 0),
        "not_corroborated": counts.get("Not Corroborated", 0),
        "uncertain": counts.get("Uncertain", 0),
        "not_applicable": counts.get("Not Applicable", 0),
        "n_mc": eligible,
        "concurrent_interval": f"Matching window +/- {manifest['matching_window_seconds']:.2f} seconds",
    }
    cameras = manifest.get("cameras", [])
    if len(cameras) == 2:
        metric.update(camera_a=cameras[0]["camera_id"], camera_b=cameras[1]["camera_id"])
    return {"mccr": metric}


def write_session_pdf(path: Path, manifest: dict) -> None:
    """Write a landscape A4 report with a chronological observation table."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    path = Path(path)
    camera_reports = _camera_reports(path, manifest)
    observations_by_id = {row["observation_id"]: row for row in manifest.get("observations", [])}

    regular, bold = "Helvetica", "Helvetica-Bold"
    font_dir = Path("C:/Windows/Fonts")
    if (font_dir / "arial.ttf").exists() and (font_dir / "arialbd.ttf").exists():
        if "Forensikada" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Forensikada", str(font_dir / "arial.ttf")))
            pdfmetrics.registerFont(TTFont("ForensikadaBold", str(font_dir / "arialbd.ttf")))
            pdfmetrics.registerFontFamily("Forensikada", normal="Forensikada", bold="ForensikadaBold")
        regular, bold = "Forensikada", "ForensikadaBold"

    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = regular
    styles.add(ParagraphStyle("ReportBody", fontName=regular, fontSize=9, leading=13, spaceAfter=6,
                              splitLongWords=True, alignment=TA_LEFT))
    styles.add(ParagraphStyle("ReportTitle", fontName=bold, fontSize=23, leading=28,
                              textColor=colors.HexColor("#2f318e"), spaceAfter=12))
    styles.add(ParagraphStyle("Section", fontName=bold, fontSize=12, leading=17,
                              spaceBefore=14, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle("Observation", fontName=bold, fontSize=10, leading=14,
                              textColor=colors.HexColor("#2f318e"), spaceBefore=9, spaceAfter=5,
                              keepWithNext=True))
    styles.add(ParagraphStyle("Disclaimer", fontName=bold, fontSize=9.5, leading=14,
                              textColor=colors.HexColor("#493614")))
    styles.add(ParagraphStyle("TableCell", fontName=regular, fontSize=8, leading=12,
                              spaceBefore=0, spaceAfter=0, splitLongWords=True))
    styles.add(ParagraphStyle("TableHeader", parent=styles["TableCell"], fontName=bold))
    styles.add(ParagraphStyle("FieldCell", parent=styles["ReportBody"], spaceBefore=0, spaceAfter=0))
    body = styles["ReportBody"]

    def p(value, style=body):
        value = "Not recorded" if value is None or value == "" else str(value)
        return Paragraph(escape(value).replace("\n", "<br/>"), style)

    def pair(label, value):
        return [p(label, styles["FieldCell"]), p(value, styles["FieldCell"])]

    page_size = landscape(A4)
    width = page_size[0] - 84
    table_padding = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]

    def record_table(headers, rows, column_widths):
        table = LongTable(
            [[p(value, styles["TableHeader"]) for value in headers]] +
            [[p(value, styles["TableCell"]) for value in row] for row in rows],
            colWidths=column_widths, repeatRows=1, hAlign="LEFT", splitInRow=1,
            spaceBefore=8, spaceAfter=8,
        )
        table.setStyle(TableStyle([
            *table_padding,
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e9f4")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fb")]),
            ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#dce0e8")),
        ]))
        return table

    def field_table(rows):
        table = Table(rows, colWidths=[145, width - 145], hAlign="LEFT", spaceAfter=8)
        table.setStyle(TableStyle([
            *table_padding,
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f3f8")),
            ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#dce0e8")),
        ]))
        return table

    def section(title):
        return p(title, styles["Section"])

    def camera_heading(camera, report):
        return p(f"{camera['camera_id']} - {report['source']['name']}", styles["Observation"])

    generated = manifest["generated_at_utc"]
    timestamp = datetime.fromisoformat(generated.replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%SZ")
    report_id = f"FR-MULTI-{timestamp}-{path.parent.name[-8:]}"
    disclaimer = Table([[p(DISCLAIMER, styles["Disclaimer"])]], colWidths=[width], hAlign="LEFT")
    disclaimer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4da")),
        ("BOX", (0, 0), (-1, -1), .8, colors.HexColor("#d8b45c")),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    story = [
        p("FORENSIKADA / FORENSIC ANALYSIS", styles["Section"]),
        p("Forensic Detection Report", styles["ReportTitle"]),
        p(f"Report: {report_id}\nGenerated (UTC): {generated}"),
        disclaimer,
        section("Video Metadata"),
    ]
    for camera, report in camera_reports:
        source = report["source"]
        dimensions = (f"{source['width']} x {source['height']} pixels"
                      if source["width"] and source["height"] else "Not recorded")
        story.extend([
            camera_heading(camera, report),
            field_table([
                pair("Video filename", source["name"]),
                pair("Resolution", dimensions),
                pair("Frame rate", f"{source['fps']:g} FPS"),
                pair("Duration", f"{source['duration_seconds']:.3f} seconds"),
                pair("Source frame count", source["frame_count"]),
                pair("Camera identifier", camera["camera_id"]),
                pair("Recording date/time", source["recording_start_timestamp"]),
            ]),
        ])

    story.append(section("Video and Detection Information"))
    for camera, report in camera_reports:
        processing, summary = report["processing"], report["summary"]
        counts = summary["counts_per_class"]
        class_summary = "; ".join(f"{name.title()}: {count}" for name, count in sorted(counts.items())) or "None"
        story.extend([
            camera_heading(camera, report),
            field_table([
                pair("Model used", processing["model_reference"]),
                pair("Run status", "Completed saved run"),
                pair("Confidence threshold", f"{processing['confidence_threshold']:.0%}"),
                pair("Frames analyzed", processing["analyzed_frames"]),
                pair("Processing device / time", f"{processing['device'].upper()} / {processing['elapsed_seconds']:.2f} seconds"),
                pair("Analysis completed (UTC)", processing["completed_at_utc"]),
                pair("Detection summary", f"{summary['total_frame_detections']} observations; "
                     f"{summary['frames_with_detections']} positive frames; {class_summary}"),
                pair("Analyst review coverage", report["review_summary"]),
            ]),
        ])

    story.extend([
        section("Interpretation"),
        p("The model generated the listed handgun and knife observations at the configured confidence threshold. "
          "A confidence score describes model certainty and is not an analyst decision. Analyst decisions document "
          "a later human assessment and do not replace or delete the original model observation."),
        p("Frame numbers are zero-based. Times are video-relative offsets and are not recording dates. "
          "Bounding boxes use [x1, y1, x2, y2] source-frame pixel coordinates. Counts represent frame observations, "
          "so the same physical object may appear more than once."),
    ])
    story.append(section("Model Performance Metrics"))
    for camera, report in camera_reports:
        story.extend([
            camera_heading(camera, report),
            field_table([pair(label, value) for label, value in metric_fields(report.get("metrics", {}), "tcr")]),
        ])
    story.extend([
        field_table([pair(label, value) for label, value in metric_fields(_session_mccr(manifest), "mccr")]),
        section("Object Detection Observations and Reviews"),
        p("Each row is a frame observation. Confidence and cross-view matching remain reviewable. "
          "Session time is video time plus the camera offset; it is not a recording date/time. "
          "The CSV and JSON files contain all record identifiers, matching details and source references."),
    ])

    # Join the saved per-camera observations to the session timeline by stable ID.
    # Never infer session offsets when rebuilding a report from incomplete records.
    entries = []
    for camera, report in camera_reports:
        for row in report["detections"]:
            session_row = observations_by_id.get(row["observation_id"], {})
            session_time = session_row.get("session_seconds")
            if session_time is None and camera.get("offset_seconds") is not None:
                session_time = row["video_relative_timestamp_seconds"] + camera["offset_seconds"]
            entries.append((camera, row, session_row, session_time))
    entries.sort(key=lambda entry: (
        entry[3] is None, entry[3] if entry[3] is not None else 0,
        entry[0]["camera_id"], entry[1]["frame_number"], entry[1]["observation_id"],
    ))
    if entries:
        story.append(record_table(
            ["Session time", "Camera", "Video time", "Frame", "Object", "Confidence",
             "Box [x1,y1,x2,y2]", "Cross-view"],
            [[f"{time:.3f} s" if time is not None else "Not recorded", camera["camera_id"],
              f"{row['video_relative_timestamp_seconds']:.3f} s", row["frame_number"],
              row["object_label"].title(), f"{row['confidence_score']:.2%}",
              f"[{row['x1']}, {row['y1']}, {row['x2']}, {row['y2']}]",
              session_row.get("corroboration_status", "Not recorded")]
             for camera, row, session_row, time in entries],
            [80, 75, 80, 55, 70, 80, 160, width - 600],
        ))
    else:
        story.append(p("No handgun or knife observations met the configured confidence threshold."))

    story.append(section("Analyst Review Information"))
    all_rows = [row for _, report in camera_reports for row in report["detections"]]
    reviewed_entries = [entry for entry in entries if entry[1]["analyst_decision"] in DECISIONS]
    decisions = Counter(row["analyst_decision"] for _, row, _, _ in reviewed_entries)
    reviewed = len(reviewed_entries)
    decision_summary = "; ".join(f"{decision}: {count}" for decision, count in sorted(decisions.items())) or "No reviews recorded"
    review_summary = field_table([
        pair("Review coverage", f"{reviewed} of {len(all_rows)} observations reviewed"),
        pair("Decision totals", decision_summary),
    ])
    review_summary.keepWithNext = True
    story.append(review_summary)
    if reviewed_entries:
        story.append(record_table(
            ["Observation ID", "Camera / frame", "Analyst decision", "Analyst notes", "Reviewed (UTC)"],
            [[row["observation_id"], f"{camera['camera_id']} / {row['frame_number']}",
              row["analyst_decision"], row["analyst_notes"] or "No notes provided.",
              _review_timestamp(row["reviewed_at"])] for camera, row, _, _ in reviewed_entries],
            [230, 90, 95, width - 530, 115],
        ))
    else:
        story.append(p("No reviewed observations to display."))

    story.append(section("Source References and Traceability"))
    if manifest.get("reconstruction_note"):
        story.append(p(manifest["reconstruction_note"]))
    for camera, report in camera_reports:
        source, processing, artifacts = report["source"], report["processing"], report["artifacts"]
        story.extend([
            camera_heading(camera, report),
            field_table([
                pair("Source video", source["reference"]),
                pair("Model", processing["model_reference"]),
                pair("Annotated video", artifacts["annotated_video"]),
                pair("Saved summary", artifacts["saved_summary"]["path"]),
                pair("Pipeline detections", artifacts["pipeline_detections"]["path"]),
                pair("Metric input records", artifacts["metric_input"]["path"]),
                pair("Metric calculation script", artifacts["metric_engine"]["path"]),
            ]),
        ])

    story.extend([
        p("Session traceability", styles["Observation"]),
        field_table([
            pair("Report identifier", report_id),
            pair("Generated (UTC)", generated),
            pair("Camera recordings", len(camera_reports)),
            pair("Incident identifier", manifest.get("scene_id") or "Not recorded"),
            pair("Alignment", "Confirmed by analyst" if manifest.get("alignment_confirmed_by_analyst") else "Not confirmed"),
            pair("Alignment method", manifest.get("alignment_method") or "Not recorded"),
            pair("Matching window", f"+/- {manifest['matching_window_seconds']:.2f} seconds"),
        ]),
    ])
    for camera, report in camera_reports:
        source, artifacts = report["source"], report["artifacts"]
        story.extend([
            camera_heading(camera, report),
            field_table([
                pair("Source video fingerprint", source["file_at_report_generation"]["sha256"] or "Unavailable"),
                pair("Source video check", source["file_at_report_generation"]["status"]),
                pair("Saved summary fingerprint", artifacts["saved_summary"]["sha256"] or "Unavailable"),
                pair("Saved summary check", artifacts["saved_summary"]["status"]),
                pair("Pipeline detections fingerprint", artifacts["pipeline_detections"]["sha256"] or "Unavailable"),
                pair("Pipeline detections check", artifacts["pipeline_detections"]["status"]),
                pair("Metric input fingerprint", artifacts["metric_input"]["sha256"] or "Unavailable"),
                pair("Metric input check", artifacts["metric_input"]["status"]),
                pair("Metric script fingerprint", artifacts["metric_engine"]["sha256"] or "Unavailable"),
            ]),
        ])
    if camera_reports:
        story.extend([Spacer(1, 5), p(camera_reports[0][1]["definitions"]["fingerprints"])])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#66717d"))
        canvas.drawString(42, 23, "Forensikada | Multi-camera forensic report")
        canvas.drawRightString(page_size[0] - 42, 23, f"Page {doc.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path), pagesize=page_size, rightMargin=42, leftMargin=42,
        topMargin=36, bottomMargin=42, title="Forensic detection report", author="Forensikada",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
