# Forensikada video detection mockup

The interface visualizes the existing weapon-detection system:
**Import videos → configure detection → scan with the selected trained model → review annotated
videos and detection summaries.** Select one recording for the single-video view,
or multiple recordings for the multi-camera view in the same application window. The service loads the trained model,
detects its **handgun** and **knife** classes, and produces the boxes and report.

Use **Close videos** in the top toolbar to end the current single-video or
multi-camera session. It stops playback, releases the video files, and clears the
displayed results. **Import videos** also replaces the previous session after the
new recordings have been validated. Save any report you need before moving on;
existing source files and saved exports are kept. Closing and importing are
disabled while detection is running.

Detection and enhancement dialogs use larger text and charcoal headers matching
the home screen. BasicVSR++ enhancement has a simplified **Enhance video** action,
source/result previews, and progress stages. Its processing animation illustrates
restoration; the actual enhanced frame appears when processing completes.

Both detection tabs share the same three-column layout, heading spacing, and
summary cards. Single-video enhancement is in the evidence sidebar. Use the
settings gear beside **Save video + report** to toggle **Dark mode**; the appearance
choice is remembered on this computer and also applies to analysis/review dialogs.

**Original project files modified: NONE.** Changes are confined to `mockup_ui/`.
No model was retrained, replaced, copied or modified. The original whole-video
detection function is imported and called directly.

## Run

From the repository root:

```powershell
.\run_mockup_ui.bat
```

The launcher always uses `mockup_ui\.venv`, where the UI dependencies are
installed. You can also run the same interpreter directly:

```powershell
.\mockup_ui\.venv\Scripts\python.exe -B .\mockup_ui\app.py
```

## Multi-camera workspace

Choose **Import videos**, then Ctrl-select two or more recordings. The main window
switches to the Multi-camera tab. It shows camera views together and offers a shared playback/seek control. Processing
uses the existing full-video `ModelBridge` separately for each camera, in sequence;
playback switches to the annotated recordings when all cameras finish.
The view follows the Single video screen: evidence and analysis controls on the
left, camera views and the observation timeline in the center, and detection and
cross-camera summaries on the right. At narrower window sizes, camera views stack
and the timeline shows its essential columns; full details remain in row tooltips
and the forensic report.

1. Enter an incident name or ID. The workspace assigns unique camera IDs for you.
   Use **Camera and timing details** only if you need to rename cameras, record their
   locations, document the alignment method, or adjust a recording's start offset.
   Use **Enhance** on an individual camera to preview and apply BasicVSR++ before
   detection. The enhanced recording becomes that camera's detection input, while
   the original source path remains in the session report.
2. Confirm correspondence only after verifying the same incident and alignment.
   Without this confirmation, cross-view observations are **Not Applicable**.
3. Choose **Analyze cameras**. Model, confidence, CCTV intelligence and temporal
   consistency settings apply to all cameras. Temporal validation remains separate
   within each recording. A centered progress dialog shows the active camera and
   overall scan progress; the same dialog is used for Single video analysis.
4. Use **Preview** on any camera to open a large player with a single Play/Pause
   control and timeline. Scroll over the video to zoom, drag to pan, and
   double-click to fit it again. The standard Windows title-bar controls remain
   available.
5. Review the combined timeline and each camera's **Review** panel. Selecting a
   timeline row seeks all views to that session time. A view outside its recording
   interval displays a placeholder rather than a stale frame.
6. The shared **Forensic report** button opens one report with every camera's
   details and the combined session timeline. **Save video + report** writes
   a landscape multi-camera PDF, `session.json`, `combined_observations.csv`, and
   an individual portrait PDF plus an annotated video with HTML/JSON/CSV/review records
   for each camera. Individual PDFs include TCR and omit cross-camera content and MCCR.
   The combined PDF places metrics before a chronological observation table with
   repeated column headers, followed by analyst reviews and source traceability.
   The observation table omits analyst decisions; the analyst review table lists
   only observations with a saved Accept, Reject or Uncertain decision. Review
   coverage still counts all detected observations, including those awaiting review.
   The session's MCCR, alignment information and analyst decisions are preserved
   in its PDF and structured records. Matching observation IDs and detailed
   corroboration reasons are available in `session.json` and `combined_observations.csv`.

The manuscript's printed pages 70-72 (PDF pages 75-77) require same-incident
correspondence, different camera IDs, class agreement and a predefined alignment
window. The default window is ±1.5 seconds and is adjustable before processing.
There is no cross-camera bounding-box IoU or automatic physical-object identity claim.
Conflicting classes are **Uncertain**; eligible observations without support are
**Not Corroborated**. Uncertain and Not Applicable are counted separately and
excluded from the session MCCR denominator, following the manuscript's separate
reporting rule. This differs from the older two-camera benchmark's inclusion of
Uncertain in its denominator. Analyst decisions never alter automatic matches.

Camera setup is locked after completion so it cannot silently relabel existing
results. **New analysis** clears the displayed results before editing;
previous saved exports and review records remain intact. Session exports preserve
the source paths and offset settings, but reopening a whole multi-camera session
from a manifest is not implemented. Individual reviews can still be reopened.

Focused verification:
```powershell
.\mockup_ui\.venv\Scripts\python.exe -B -m unittest mockup_ui.test_multi_camera mockup_ui.test_observation_review -v
```

To use another detector environment, first confirm that it includes the packages
from `mockup_ui\requirements-ui.txt`. Then run:

```powershell
python -B mockup_ui/app.py
```

Optional video on startup:

```powershell
.\run_mockup_ui.bat --video "D:\your-videos\cctv.mp4"
```

Replace the example video path with your real file. The locally created `.venv/`
contains PySide6, OpenCV, Pillow, NumPy, PyTorch 2.13.0 CPU and torchvision 0.28.0
CPU. Your original `requirements.txt` and other environments are unchanged. Use
an existing working CUDA environment for GPU inference if available.

## Demonstration

1. **Import video** or drop a local video onto the viewer. The video loads without starting detection.
2. Select **Enhance video** to open the BasicVSR++ enhancement UI. It shows the
   source frame beside the enhanced-output preview. Enhancement is automatic,
   with no presets or manual adjustments. Select **Run BasicVSR++ enhancement**,
   then **Apply Enhanced Video** to use that recording as detection input.
3. When ready, choose **Analyze video** to open detection configuration. Review
   the video details and confidence threshold, then select **Start detection**.
4. The detection service loads the trained model and
   scans every video frame. The UI shows loading/scanning progress. Playback and
   result controls remain disabled; summary values stay pending while scanning.
5. Once processing and output validation finish, the annotated video automatically
   plays from the beginning at its source frame rate. It contains the boxes,
   labels and confidence scores drawn by the existing detection service.
6. The summary and observation table show results for the **entire processed video**.
   Play, pause, seek, or click a detection row to review a particular frame. Only
   **Current frame** details change during playback; full-video totals stay fixed.
7. **Forensic report** opens the completed analysis inside the application.
8. **Save video + report** asks for a destination and exports an annotated MP4, the original CSV/JSON results,
   and a readable forensic report with structured records.

After analysis, **Observation review** opens the analyst decision panel. Saved
reviews can be resumed with **Open saved review…**, including after restarting
the application. Opening a review never reruns detection.

Importing never starts analysis. The shared toolbar contains **Import videos**,
**Forensic report**, and **Save video + report**. An **Analyze video** control opens
the threshold confirmation whenever an imported video remains unprocessed.
The app does not open a model picker. If the required model is missing after the
user explicitly starts detection, the imported video waits while the required path
is checked every two seconds. Adding `best_weapon_detector_ninth_model.pth` to
`mockup_ui/models/` resumes that requested scan without reimporting the video.

Importing displays a still preview and metadata. The player is a result viewer:
its playback controls become available after detection completes. You can adjust
the threshold before importing a video and compare Original and Detected views afterward.
Playing or seeking the completed video does not rerun inference or change its
summary. Importing another video clears the previous result. Reimport a video
to scan it again with a changed threshold or retry after a processing error.

The **Enhance video** button becomes available after import and opens the automatic
BasicVSR++ workflow. It calls the project's existing WSL-based enhancement service;
that WSL environment and its configured BasicVSR++ script must be available for a
run to complete. Applying the output replaces the detection input for that video.
Multi-camera sources offer the same action separately for each recording.

Short clips are best for the defense, especially on CPU. Processing every frame
can take several minutes. The UI remains responsive, but only one analysis runs
at a time. The unchanged detection helper does not offer cancellation; allow the
scan to finish before closing the window. Processing speed affects how long you
wait for the result, while completed playback uses the original frame rate.

The player and OpenCV-generated annotated video are video-only; audio is not
played or included in the export. Timestamps use frame number divided by source
FPS, so fixed-frame-rate footage is preferable. Saved video retains the source
FPS regardless of how long analysis took.

## Trained checkpoint

The UI uses **only `mockup_ui/models/best_weapon_detector_ninth_model.pth`** for
every video. The Trained model panel displays its filename and full-path tooltip.
The third, baseline and retrained checkpoints have been removed. Only the ninth model is offered in the detection configuration.
There is no fallback: if the ninth checkpoint is missing, scanning waits for it.

The UI accepts only its installed ninth-model path; it cannot select another checkpoint.
Legacy `settings.json` paths and `FORENSIKADA_MODEL_PATH` overrides are ignored.
The application reads the required checkpoint in place without changing its weights
or filename. `mockup_ui/models/` is created automatically and is excluded from Git.
The application has no simulated-detection mode.

Class mapping remains the original service's mapping:
`1 = handgun`, `2 = knife`, with background ignored.

Inference creates the matching Faster R-CNN architecture without downloading
pretrained initialization weights, then loads the ninth checkpoint with strict
state-dictionary validation. Training still uses pretrained initialization by default.
The ninth checkpoint uses anchor scales `(16, 32, 64, 128, 256)`.

The installed file matches the Desktop source (SHA-256:
`baf3b5b81b312add936a182a8cf1e1e32d30f5b9d7c98a466befc8951e0a53f8`).
It loaded and processed a synthetic blank frame on CPU; this confirms runtime
compatibility, not detection accuracy on representative videos.

## Forensic report

After processing, **Forensic report** displays video information, detection
configuration, full-video totals, processing details, timeline rows, bounding boxes,
confidence scores, and review status in a large in-app dialog.

**Save video + report** opens a destination-folder chooser. Cancelling it writes
nothing. Confirming creates a uniquely named result folder there containing:

- `forensic_report.pdf`: paginated detection report with analyst review information.
- `forensic_report.html`: readable browser version with the same saved decisions.
- `forensic_report.json`: report metadata and every structured detection record.
- `forensic_records.csv`: one row per observation, including source and model references.
- `observation_reviews.json`: reopenable review snapshot with source references,
  detection details, decisions, notes, UTC timestamps, and review revision history.

Each record includes a report/record ID, source-video path/name, zero-based frame
number, video-relative time, object label, bounding box, confidence, and validation
status. Camera identifier and source recording timestamp fields are included but
marked **not recorded by the current pipeline**. File modification dates and
report-generation times are never substituted for a recording timestamp.

Every detection begins with **no analyst decision**. Completed processing and high
confidence do not select a decision. Repeated detections across frames do not count
unique weapons. Reports retain all observations, including Reject and Uncertain.
Detection totals and annotated boxes continue to represent the original model output.

## Observation Review

The panel lists every observation and shows its annotated frame, source path,
frame number, video offset, object class, confidence, box, and stable observation ID.
Select one of exactly three decision options:

| Decision | Notes | Save behavior |
| --- | --- | --- |
| Accept | Optional | Empty notes are valid. |
| Reject | Required | Empty or whitespace-only notes are blocked with a reason-required message. |
| Uncertain | Optional | Empty notes are valid. |

The options are mutually exclusive. No option is preselected for a new observation.
Changing the decision immediately updates the notes label, placeholder, and validation
rules. Save without selecting a decision is blocked. **Save review** persists a
trimmed note and UTC review timestamp; previously saved decisions can be revised.
Unsaved drafts survive navigation within the panel and are explicitly discarded
only when closing with confirmation. Saved decisions survive reopening and app restart.

Completed analyses are stored under `mockup_ui/reviews/<run-id>.json`. Each new
analysis receives a distinct run ID so reviews cannot silently carry over to a
different model run. Use **Open saved review…** and choose that file, or choose
`observation_reviews.json` from an export. The saved source/video paths are retained;
keep the original annotated-video files for frame previews. Detection records and
reviews remain readable if those files are unavailable.

The review panel retains any pipeline-supplied automated validation status as
read-only evidence. The PDF, HTML, JSON and CSV report files present the analyst
decision without an automated-validation result field. The original status remains
in `observation_reviews.json`; an analyst choice never overwrites it.

Review files are written atomically and checked against the saved run's detection
details and source references. Export includes the latest saved decisions and is a
snapshot; export again after changing a decision to generate an updated PDF. Viewing
the in-app report does not create a PDF. The PDF is generated only on explicit export.

PDF generation uses ReportLab in the isolated app environment. To set up another
working detector environment with the UI/PDF dependencies:

```powershell
python -m pip install -r mockup_ui/requirements-ui.txt
```

The report records the model actually used by its saved run. Historical results
retain their original model reference, even though new processing uses only the
third checkpoint. The CSV and summary must agree before a report can be generated.
Available source and input-record SHA-256 hashes are measured at report generation;
they are not presented as an earlier ingestion record or chain-of-custody history.
Newly saved runs also record resolution, source frame count, and analysis completion
time. Older saved results leave unavailable fields blank with explicit statuses.

The report begins with the system-generated/analyst-review disclaimer, followed by
Video Metadata, Video and Detection Information, Interpretation, Object Detection
Observations and Reviews, Model Performance Metrics, and Analyst Review Information. Source References and the
Traceability Report appear at the end. The PDF does not print guide labels such as
section group numbers.

TCR and MCCR use the functions in `benchmarks/calculate_tcr_and_mccr.py`.
New runs preserve the unfiltered pipeline CSV as `metric_input.csv`, including
suppressed flickers needed for TCR. This file is copied into each saved export;
the report records its SHA-256 hash and the calculation script's hash. Analyst
decisions do not affect these calculations. PDF, HTML and JSON share the results.
TCR follows the benchmark's observed-frame boundary exclusion and class-based
gap counting. MCCR requires two cameras and assumes aligned video starts, with
a 1.5-second matching tolerance. The report describes these assumptions and shows
N/A when inputs or eligible observations are missing. Earlier exports without
the unfiltered records require processing the video again to obtain metrics.

Focused report verification:
```powershell
.\mockup_ui\.venv\Scripts\python.exe -B -m unittest mockup_ui.test_report_metrics mockup_ui.test_forensic_report mockup_ui.test_observation_review -v
```

Create a report from an existing export without rerunning inference:

```powershell
.\mockup_ui\.venv\Scripts\python.exe -B -m mockup_ui.forensic_report "mockup_ui\outputs\YOUR_SAVED_RUN\summary.json"
```

This creates a new report subfolder and preserves the previous export.

## How the UI calls the existing system

```text
Video upload and preview
  -> app.services.video_service.VideoService (validation and metadata)
  -> mockup_ui.model_bridge.ModelBridge.analyze_video()
  -> run_full_pipeline.detect_video_with_model()
     -> app.services.detection_service.DetectionService
     -> dataset_analysis.build_model.get_model()
     -> existing trained checkpoint
     -> DetectionService.detect_frame() for every frame
     -> draw_detections()
     -> complete annotated MP4 and detection CSV
  -> validate completed output and read the service's report
  -> automatically play annotated video and populate full-video summary
```

The background worker calls the existing whole-video helper without altering it.
The helper owns model loading, inference, filtering, box drawing, video writing
and CSV generation. The adapter chooses isolated output paths, requests every
frame, translates console progress into UI updates, checks output completeness,
and reads the resulting CSV. Summary counts come from that completed report.
The existing `VideoService` supplies validation and metadata. Result playback
and seeking use OpenCV with a Qt timer after the worker produces a valid result.

The original helper initializes the detector once per video run.
It handles all frames within that run with the same loaded ninth model. Choosing a new
video or rerunning detection clears old results so they
cannot be mistaken for the current run. Failed/incomplete processing is not
presented as a completed result. Failed runs keep totals pending and disable
playback/export; any partial files remain isolated under `temp/`.

The application offers BasicVSR++ enhancement and multi-camera comparison, but it
does not train models, track physical objects across cameras, or manage cases.
Analyst decisions are entered manually.

## System settings

The gear button on the home screen opens a settings popup with **Save settings**,
**Cancel**, and **Restore defaults**. Preferences are saved for your Windows user
and restored when the app opens again.

- **Color theme:** light or dark appearance.
- **Result playback:** automatically play annotated videos after single-video or multi-camera analysis.
- **Default confidence threshold:** 10-95%, applied to newly imported recordings.
- **Detection checks:** default CCTV intelligence and temporal consistency choices
  in the analysis configuration. Temporal consistency requires CCTV intelligence.
- **Export location:** the initial folder in both Save video + report dialogs.

Saving defaults preserves imported recordings, completed results, and an active
run's configuration. Restore defaults changes the form until you click Save settings;
Cancel discards edits. Detection options can still be adjusted before each run.

## What the results mean

- **Detections:** the total number of handgun/knife observations across analyzed frames.
- **Handguns / Knives:** those same observations grouped by class.
- **Frames with detections:** how many distinct frames contain at least one observation.
- **Confidence:** the detector's score, rounded by the existing CSV exporter.
- **Frame:** zero-based frame number; the corresponding time is frame number divided by FPS.
- **Bounding box:** `[x1, y1, x2, y2]` in that frame's pixel coordinates.

These counts do not represent unique physical weapons. A knife detected in ten
frames contributes ten observations. The model is not an identity tracker. A
no-detection result means no supported object met the selected confidence
threshold. Changing the threshold affects the next run, not previous results.

## Files and storage

```text
mockup_ui/
├── app.py                       Video interface, results table and background worker
├── model_bridge.py              Adapter calling the original whole-video detection function
├── forensic_report.py           Readable report and structured detection-record exports
├── observation_review.py        Stable observation IDs and atomic review persistence
├── review_panel.py              Analyst decision controls, notes and frame preview
├── pdf_report.py                Paginated PDF with detection and analyst-review sections
├── requirements-ui.txt          Desktop UI and PDF dependencies
├── video_player.py              OpenCV playback, pause, timeline and exact-frame seeking
├── styles.qss                   Figma-derived Qt styles
├── test_mockup.py               Video integration and UI regression checks
├── test_forensic_report.py      Report traceability, missing metadata and consistency checks
├── test_observation_review.py   Decision rules, reopening, persistence, and PDF checks
├── README.md                    These instructions
├── .gitignore                   Excludes runtime files
├── models/                      Required location of best_weapon_detector_ninth_model.pth
├── reviews/                     Persistent completed runs and analyst review records
├── assets/
│   ├── forensikada-logo.png     Camera-frame brand mark used in the application header
│   ├── forensikada-app-icon.png FK monogram used for the window and taskbar icon
│   ├── import-image.png         Existing Figma folder icon, reused for video import
│   ├── run-analysis.svg         Original Figma analysis icon
│   ├── save-result.svg          Original Figma report icon
│   ├── InterVariable.ttf        Bundled Inter font
│   └── Inter-LICENSE.txt        Original SIL Open Font License
├── .venv/                       Isolated local runtime; unchanged by this video update
├── temp/                        Logs, test renders and per-run playback files
│   └── video-run-*/
│       ├── annotated.mp4         Completed/partial annotated video for that run
│       └── detections.csv        Per-frame observations from the original detector
└── outputs/
    └── <video-name>_<time>_<id>/
        ├── annotated.mp4
        ├── detections.csv
        ├── summary.json
        ├── forensic_report.html
        ├── forensic_report.pdf
        ├── forensic_report.json
        ├── observation_reviews.json
        └── forensic_records.csv
```

Uploaded videos are read from their original locations. They are not modified,
moved or duplicated. Each analysis gets a fresh directory under `temp/`, and
explicit exports get unique directories under `outputs/`. Earlier outputs are
not overwritten. Temporary run files remain available for playback; they can be
removed when the application is closed. Logs are in `temp/mockup.log`.

The visual reference remains the [Forensikada Figma dashboard](https://www.figma.com/design/woIsbmBEZTJ4RYb5b7tgcd/Forensikada-UI?node-id=2-6):
charcoal toolbar, white side panels, central video, observation table, Inter font
and indigo selection accents. The camera-frame header mark and FK application icon
come from the same Figma branding and are stored locally with the other UI assets.

## Verification

```powershell
.\mockup_ui\.venv\Scripts\python.exe -B -m unittest mockup_ui.test_mockup mockup_ui.test_forensic_report mockup_ui.test_observation_review -v
```

All **19 tests passed**, including six observation-review checks and three forensic-report checks. They cover video validation, Unicode paths, metadata,
missing checkpoints, changed inputs, incomplete processing, original per-frame
inference/preprocessing, actual MP4 encoding/decoding, bounding-box rendering,
CSV/JSON exports, playback/seeking, progress, repeated-click prevention, no
detections, exact-frame navigation, error recovery and clearing stale results.
Workflow checks verify that playback and totals wait until processing finishes,
the processed file starts playing automatically, and full-video totals remain
constant when reviewing other frames. They also cover manual threshold confirmation,
cancelled configuration retaining the imported video, finding a newly added
third checkpoint while a video waits, exclusive selection despite other available
weights or legacy overrides, and refusal to fall back if the required file is removed.

For these tests, only checkpoint loading/predictions are controlled fixtures;
the original whole-video loop, `detect_frame()` and renderer execute on a
generated clip. Separate UI fixtures check scan progress and result review.
No alternative trained weights are created or used, and the production
application has no test-prediction fallback. These tests verify software integration.
The uploaded third checkpoint passed a separate real-model loading and one-frame
processing check on CPU, producing an annotated MP4 and CSV. The log is
`temp/third-model-validation.log`, including the exact checkpoint and output paths.
That blank-frame check verifies compatibility and output generation. Detection
accuracy on representative CCTV footage has not yet been evaluated here.

The updated UI was rendered at 1450 × 880 and 1060 × 760. Previews are in
`temp/auto-import-preview.png` and `temp/auto-import-preview-small.png`; test output is in
`temp/third-model-tests.txt` for the current exclusive-model checks. Renders of the scan and completed-result states use
clearly marked test predictions in `temp/scanning-test.png` and
`temp/processed-result-test.png`.
Worker updates use explicitly queued GUI slots and
thread completion before disposal, following [Qt's thread-affinity guidance](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html#thread-affinity).

The current combined run is recorded in `temp/observation-review-tests.txt`.
Review checks cover all three decision rules, whitespace rejection, changing
decisions, no default selection, navigation, reopening from disk, new-run isolation,
failed-write recovery, source/status preservation, requested report order, the
disclaimer, omission of the automated-validation result, and matching report values.
The panel was rendered at 1120 × 800 and 920 × 700. All three pages of the synthetic
PDF layout sample were rendered and inspected. QA artifacts are under
`temp/pdf-layout-qa-20260910-final/` and contain explicitly synthetic observations
and test analyst decisions only.
