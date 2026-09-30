"""Paginated forensic PDF with model observations and analyst review details."""
from pathlib import Path
from xml.sax.saxutils import escape


from mockup_ui.report_metrics import REPORT_DISCLAIMER as DISCLAIMER, metric_fields


def write_pdf(report, path, *, camera_id=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
    styles.add(ParagraphStyle(
        "ReportBody", fontName=regular, fontSize=9, leading=13, spaceAfter=6,
        splitLongWords=True, alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "ReportTitle", fontName=bold, fontSize=23, leading=28,
        textColor=colors.HexColor("#2f318e"), spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        "Section", fontName=bold, fontSize=12, leading=17,
        spaceBefore=14, spaceAfter=7, keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        "Observation", fontName=bold, fontSize=10, leading=14,
        textColor=colors.HexColor("#2f318e"), spaceBefore=9, spaceAfter=5,
        keepWithNext=True,
    ))
    styles.add(ParagraphStyle(
        "Disclaimer", fontName=bold, fontSize=9.5, leading=14,
        textColor=colors.HexColor("#493614"),
    ))
    body = styles["ReportBody"]

    def p(value, style=body):
        value = "Not recorded" if value is None or value == "" else str(value)
        return Paragraph(escape(value).replace("\n", "<br/>"), style)

    def pair(label, value):
        return [p(label), p(value)]

    width = A4[0] - 84

    def field_table(rows):
        table = Table(rows, colWidths=[145, width - 145], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f3f8")),
            ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#dce0e8")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    def section(title):
        return p(title, styles["Section"])

    source, processing, summary = report["source"], report["processing"], report["summary"]
    dimensions = (
        f"{source['width']} x {source['height']} pixels"
        if source["width"] and source["height"] else "Not recorded"
    )
    counts = summary["counts_per_class"]
    class_summary = "; ".join(f"{name.title()}: {count}" for name, count in sorted(counts.items())) or "None"
    decision_summary = "; ".join(
        f"{decision}: {count}" for decision, count in sorted(report["counts_by_analyst_decision"].items())
    ) or "No observations"

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
        p(f"Report: {report['report_id']}\nGenerated (UTC): {report['generated_at_utc']}"),
        disclaimer,
        section("Video Metadata"),
        field_table([
            pair("Video filename", source["name"]),
            pair("Resolution", dimensions),
            pair("Frame rate", f"{source['fps']:g} FPS"),
            pair("Duration", f"{source['duration_seconds']:.3f} seconds"),
            pair("Source frame count", source["frame_count"]),
            pair("Camera identifier", camera_id or source["camera_id"]),
            pair("Recording date/time", source["recording_start_timestamp"]),
        ]),
        section("Video and Detection Information"),
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
        section("Interpretation"),
        p("The model generated the listed handgun and knife observations at the configured confidence threshold. "
          "A confidence score describes model certainty and is not an analyst decision. Analyst decisions document "
          "a later human assessment and do not replace or delete the original model observation."),
        p("Frame numbers are zero-based. Times are video-relative offsets and are not recording dates. "
          "Bounding boxes use [x1, y1, x2, y2] source-frame pixel coordinates. Counts represent frame observations, "
          "so the same physical object may appear more than once."),
    ]

    for index, row in enumerate(report["detections"]):
        heading = p(f"Observation {row['observation_id']}", styles["Observation"])
        details = field_table([
            pair("Detection", f"{row['object_label'].title()} | Frame {row['frame_number']} | "
                 f"{row['video_relative_timestamp_seconds']:.2f} seconds"),
            pair("Confidence / bounding box", f"{row['confidence_score']:.2%} / "
                 f"[{row['x1']}, {row['y1']}, {row['x2']}, {row['y2']}]"),
            pair("Analyst Review Decision", row["analyst_decision"] or "Not reviewed"),
            pair("Review timestamp (UTC)", row["reviewed_at"]),
        ])
        elements = [heading, details]
        if index == 0:
            elements.insert(0, section("Object Detection Observations and Reviews"))
        story.append(KeepTogether(elements))
    if not report["detections"]:
        story.extend([
            section("Object Detection Observations and Reviews"),
            p("No handgun or knife observations met the configured confidence threshold."),
        ])

    story.extend([
        section("Model Performance Metrics"),
        field_table([pair(label, value) for label, value in metric_fields(report.get("metrics", {}), "tcr")]),
        section("Analyst Review Information"),
        field_table([
            pair("Review coverage", report["review_summary"]),
            pair("Decision totals", decision_summary),
        ]),
    ])
    for row in report["detections"]:
        story.append(KeepTogether([
            p(f"Observation {row['observation_id']}", styles["Observation"]),
            field_table([
                pair("Analyst Review Decision", row["analyst_decision"] or "Not reviewed"),
                pair("Analyst notes", row["analyst_notes"] or "No notes provided."),
                pair("Review timestamp (UTC)", row["reviewed_at"]),
            ]),
        ]))
    if not report["detections"]:
        story.append(p("There are no observations available for analyst review."))

    artifacts = report["artifacts"]
    story.extend([
        section("Source References"),
        field_table([
            pair("Source video", source["reference"]),
            pair("Model", processing["model_reference"]),
            pair("Annotated video", artifacts["annotated_video"]),
            pair("Saved summary", artifacts["saved_summary"]["path"]),
            pair("Pipeline detections", artifacts["pipeline_detections"]["path"]),
            pair("Metric input records", artifacts["metric_input"]["path"]),
            pair("Metric calculation script", artifacts["metric_engine"]["path"]),
        ]),
        section("Traceability Report"),
        field_table([
            pair("Report identifier", report["report_id"]),
            pair("Generated (UTC)", report["generated_at_utc"]),
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
        Spacer(1, 5),
        p(report["definitions"]["fingerprints"]),
    ])
    if report.get("reconstruction_note"):
        story.append(p(report["reconstruction_note"]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#66717d"))
        canvas.drawString(42, 23, "Forensikada | System-generated report with analyst review information")
        canvas.drawRightString(A4[0] - 42, 23, f"Page {doc.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=42, leftMargin=42,
        topMargin=36, bottomMargin=42, title="Forensic detection report", author="Forensikada",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return Path(path)
