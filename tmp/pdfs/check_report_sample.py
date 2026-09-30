from pathlib import Path
import ast
import json
from pypdf import PdfReader

root = Path(__file__).resolve().parents[2]
output = root / 'output/pdf'
combined = PdfReader(output / 'combined_forensic_detection_report.pdf')
text = '\n'.join(page.extract_text() for page in combined.pages)
headings = ['Video Metadata', 'Video and Detection Information', 'Interpretation',
            'Model Performance Metrics', 'Object Detection Observations and Reviews',
            'Analyst Review Information', 'Source References and Traceability']
assert [text.index(s) for s in headings] == sorted(text.index(s) for s in headings)
assert all(page.mediabox.width > page.mediabox.height for page in combined.pages)
reports = [json.loads(path.read_text()) for path in (root / 'tmp/pdfs/report-refresh').glob('CAM-*/forensic_report.json')]
rows = [row for report in reports for row in report['detections']]
assert len(rows) == 58
reviewed = [row for row in rows if row['analyst_decision'] in ('Accept', 'Reject', 'Uncertain')]
assert all(text.count(row['observation_id']) == 1 for row in reviewed)
assert all(row['observation_id'] not in text for row in rows if row not in reviewed)
table = text.split(headings[4], 1)[1].split(headings[5], 1)[0]
for row in rows:
    assert f"[{row['x1']}, {row['y1']}, {row['x2']}, {row['y2']}]" in table
    assert f"{row['confidence_score']:.2%}" in table
for report in reports:
    path = output / f"{report['source']['camera_id']}_forensic_detection_report.pdf"
    document = PdfReader(path)
    content = '\n'.join(page.extract_text() for page in document.pages)
    assert all(page.mediabox.height > page.mediabox.width for page in document.pages)
    assert 'MCCR' not in content and 'Cross-camera' not in content
    assert all(content.count(row['observation_id']) == 2 for row in report['detections'])
    print(path.name, len(document.pages), 'portrait pages verified')
for name in ('multi_camera', 'multi_camera_panel', 'multi_camera_pdf', 'pdf_report', 'test_multi_camera', 'test_pdf_exports'):
    path = root / 'mockup_ui' / f'{name}.py'
    ast.parse(path.read_text(encoding='utf-8'))
print('Combined:', len(combined.pages), 'landscape pages; section order, all 58 observations, boxes and confidence scores verified.')
