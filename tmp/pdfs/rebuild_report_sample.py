"""Reformat the existing sample using only values recoverable from its PDF."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from mockup_ui.multi_camera_pdf import write_session_pdf
from mockup_ui.pdf_report import write_pdf

work = ROOT / 'tmp/pdfs/report-refresh'
work.mkdir(parents=True, exist_ok=True)
output = ROOT / 'output/pdf'
original = work / 'original_combined_report.pdf'
if not original.exists():
    shutil.copy2(output / 'combined_forensic_detection_report.pdf', original)
reader = PdfReader(original)
text = '\n'.join(page.extract_text() for page in reader.pages)
text = re.sub(r'Forensikada \| System-generated report with analyst review information\nPage \d+\n', '', text)
text = re.sub(r'\n\n+', '\n', text)

def block(start, end, content=text):
    return content.split(start, 1)[1].split(end, 1)[0].strip()

def fields(content, labels):
    result = {}
    for i, label in enumerate(labels):
        value = content.split(label + '\n', 1)[1]
        if i + 1 < len(labels):
            value = value.split('\n' + labels[i+1] + '\n', 1)[0]
        result[label] = value.strip()
    return result

def camera_block(content, index):
    parts = re.split(r'CAM-0[12] - CAM[12]_SCENE001\.(?:mov|mp4)\n', content)
    return parts[index + 1].strip()

metadata = block('Video Metadata\n', 'Video and Detection Information\n')
processing = block('Video and Detection Information\n', 'Interpretation\n')
detections = block('Object Detection Observations and Reviews\n', 'Model Performance Metrics\n')
metrics = block('Model Performance Metrics\n', 'Analyst Review Information\n')
references = block('Source References\n', 'Traceability Report\n')
traceability = text.split('Traceability Report\n', 1)[1]
old_generated = re.search(r'Generated \(UTC\): ([^\n]+)', text).group(1)
generated = datetime.now(timezone.utc).isoformat(timespec='seconds')
note = (
    'Reformatted from combined_forensic_detection_report.pdf (original generation: '
    + old_generated + '). The original export folder is unavailable. Printed observations, '
    'metrics, source references and historical fingerprints are preserved at their original displayed '
    'precision; source files and hashes have not been revalidated. Session offsets were not printed '
    'and session times are therefore shown as Not recorded. Original companion CSV/JSON files are unavailable.'
)
individual_note = (
    'Reformatted from the camera records printed in combined_forensic_detection_report.pdf '
    '(original generation: ' + old_generated + '). The original export folder is unavailable. '
    'Printed values retain their original displayed precision. Source references and fingerprints '
    'are historical values copied from that report; source files and hashes have not been revalidated.'
)
manifest = {
    'generated_at_utc': generated, 'scene_id': '', 'alignment_confirmed_by_analyst': False,
    'alignment_method': '', 'matching_window_seconds': 1.5,
    'metrics': {'counts': {'Not Applicable': 58}, 'eligible': 0, 'mccr_percent': None},
    'cameras': [], 'observations': [], 'reconstruction_note': note,
}

for index in range(2):
    camera_id = f'CAM-0{index+1}'
    meta = fields(camera_block(metadata, index), ['Video filename', 'Resolution', 'Frame rate', 'Duration',
                  'Source frame count', 'Camera identifier', 'Recording date/time'])
    proc = fields(camera_block(processing, index), ['Model used', 'Run status', 'Confidence threshold',
                  'Frames analyzed', 'Processing device / time', 'Analysis completed (UTC)',
                  'Detection summary', 'Analyst review coverage'])
    refs = fields(camera_block(references, index), ['Source video', 'Model', 'Annotated video', 'Saved summary',
                  'Pipeline detections', 'Metric input records', 'Metric calculation script'])
    fp_content = camera_block(traceability, index).split('SHA-256 values identify', 1)[0].strip()
    fp = fields(fp_content, ['Source video fingerprint', 'Source video check', 'Saved summary fingerprint',
                'Saved summary check', 'Pipeline detections fingerprint', 'Pipeline detections check',
                'Metric input fingerprint', 'Metric input check', 'Metric script fingerprint'])
    metric = camera_block(metrics, index).split('Metric\nMulti-Camera', 1)[0].strip()
    metric = metric.replace('Boundary observations\nexcluded', 'Boundary observations excluded')
    tcr = fields(metric, ['Metric', 'Result', 'Interpretation', 'Supported observations (N_TS)',
                 'Isolated observations', 'Interruptions', 'Boundary observations excluded', 'Eligible total (N_TE)'])
    rows = []
    pattern = (r'Observation (\S+)\nDetection\n(\w+) \| Frame (\d+) \| ([\d.]+) seconds\n'
               r'Confidence / bounding box\n([\d.]+)% / \[(\d+), (\d+), (\d+), (\d+)\]\n'
               r'Cross-camera comparison\n(.*?)\nAnalyst Review Decision\n(.*?)\nReview timestamp \(UTC\)\n([^\n]+)')
    for match in re.finditer(pattern, camera_block(detections, index), re.S):
        oid, label, frame, time, confidence, x1, y1, x2, y2, cross, decision, reviewed = match.groups()
        assert decision == 'Not reviewed' and reviewed == 'Not recorded'
        rows.append({'observation_id': oid, 'object_label': label.lower(), 'frame_number': int(frame),
                     'video_relative_timestamp_seconds': float(time), 'confidence_score': float(confidence)/100,
                     'x1': int(x1), 'y1': int(y1), 'x2': int(x2), 'y2': int(y2),
                     'analyst_decision': None, 'analyst_notes': '', 'reviewed_at': None})
        status, reason = cross.split('; ', 1)
        manifest['observations'].append({'observation_id': oid, 'camera_id': camera_id,
                                        'session_seconds': None, 'corroboration_status': status,
                                        'reason': reason, 'supporting_observations': []})
    assert len(rows) == int(proc['Detection summary'].split()[0])
    # Ensure every printed analyst record is unreviewed before preserving empty notes.
    review_section = block('Analyst Review Information\n', 'Source References\n')
    for row in rows:
        review = review_section.split('Observation ' + row['observation_id'] + '\n', 1)[1]
        assert review.startswith('Analyst Review Decision\nNot reviewed\nAnalyst notes\nNo notes provided.\nReview timestamp (UTC)\nNot recorded')
    width, height = map(int, re.findall(r'\d+', meta['Resolution']))
    def reference(name, prefix):
        return {'path': refs[name].replace('\n', ''), 'sha256': fp[prefix + ' fingerprint'],
                'status': fp.get(prefix + ' check', 'historical_fingerprint_from_original_report')}
    report = {
        'report_id': f'FR-{camera_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}',
        'generated_at_utc': generated, 'reconstruction_note': individual_note,
        'source': {'name': meta['Video filename'], 'reference': refs['Source video'].replace('\n', ''),
                   'width': width, 'height': height, 'fps': float(meta['Frame rate'].split()[0]),
                   'duration_seconds': float(meta['Duration'].split()[0]), 'frame_count': int(meta['Source frame count']),
                   'camera_id': camera_id, 'recording_start_timestamp': None,
                   'file_at_report_generation': {'sha256': fp['Source video fingerprint'], 'status': fp['Source video check']}},
        'processing': {'model_reference': proc['Model used'].replace('\n', ''),
                       'confidence_threshold': float(proc['Confidence threshold'].rstrip('%'))/100,
                       'analyzed_frames': int(proc['Frames analyzed']),
                       'device': proc['Processing device / time'].split(' / ')[0].lower(),
                       'elapsed_seconds': float(proc['Processing device / time'].split(' / ')[1].split()[0]),
                       'completed_at_utc': proc['Analysis completed (UTC)']},
        'summary': {'total_frame_detections': len(rows), 'frames_with_detections': len({r['frame_number'] for r in rows}),
                    'counts_per_class': dict(Counter(r['object_label'] for r in rows))},
        'review_summary': proc['Analyst review coverage'], 'counts_by_analyst_decision': {'Not reviewed': len(rows)},
        'detections': rows,
        'metrics': {'tcr': {'status': 'computed', 'value_percent': float(tcr['Result'].rstrip('%')),
                     'reason': tcr['Interpretation'], **{key: int(tcr[label]) for key, label in [
                         ('n_ts', 'Supported observations (N_TS)'), ('n_isolated', 'Isolated observations'),
                         ('n_interrupted', 'Interruptions'), ('n_not_evaluable', 'Boundary observations excluded'),
                         ('n_te', 'Eligible total (N_TE)')]}}},
        'artifacts': {'annotated_video': refs['Annotated video'].replace('\n', ''),
                      'saved_summary': reference('Saved summary', 'Saved summary'),
                      'pipeline_detections': reference('Pipeline detections', 'Pipeline detections'),
                      'metric_input': reference('Metric input records', 'Metric input'),
                      'metric_engine': reference('Metric calculation script', 'Metric script')},
        'definitions': {'fingerprints': 'SHA-256 values below are historical fingerprints from the original report; no new file verification or chain-of-custody history is asserted.'},
    }
    camera_folder = work / camera_id
    camera_folder.mkdir(exist_ok=True)
    (camera_folder / 'forensic_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    manifest['cameras'].append({'camera_id': camera_id, 'export_folder': str(camera_folder), 'offset_seconds': None})
    write_pdf(report, output / f'{camera_id}_forensic_detection_report.pdf')

(work / 'session.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
write_session_pdf(output / 'combined_forensic_detection_report.pdf', manifest)
print('Preserved', sum(len(json.loads((Path(c['export_folder']) / 'forensic_report.json').read_text())['detections']) for c in manifest['cameras']), 'observations')
for path in [output / 'combined_forensic_detection_report.pdf', *output.glob('CAM-*_forensic_detection_report.pdf')]:
    pdf = PdfReader(path)
    print(path.name, len(pdf.pages), 'pages', pdf.pages[0].mediabox)
