from pathlib import Path
import json
import shutil
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from pypdf import PdfReader
from mockup_ui.multi_camera_pdf import write_session_pdf
from mockup_ui.test_pdf_exports import PdfExportTests

work = root / 'tmp/pdfs/review-layout'
work.mkdir(parents=True, exist_ok=True)
manifest = json.loads((root / 'tmp/pdfs/report-refresh/session.json').read_text())
sample = root / 'output/pdf/combined_forensic_detection_report.pdf'
write_session_pdf(sample, manifest)

# Separate synthetic fixture: fifty detections, exactly one saved human review.
fixture = PdfExportTests()
fixture.setUp()
try:
    folder, _ = fixture.export([48, 2], reviews=('Accept',))
    shutil.copy2(folder / 'forensic_report.pdf', work / 'one-review-qa.pdf')
finally:
    fixture.doCleanups()

for path in (sample, work / 'one-review-qa.pdf'):
    reader = PdfReader(path)
    pages = [p.extract_text() for p in reader.pages]
    print(path.name, len(pages), 'pages')
    print('Observation pages:', [i+1 for i, p in enumerate(pages) if 'Session time' in p])
    print('Analyst pages:', [i+1 for i, p in enumerate(pages) if 'Analyst Review Information' in p])
    text = '\n'.join(pages)
    observation = text.split('Object Detection Observations and Reviews', 1)[1].split('Analyst Review Information', 1)[0]
    assert 'Analyst' not in observation
    review = text.split('Analyst Review Information', 1)[1].split('Source References and Traceability', 1)[0]
    if path == sample:
        assert 'No reviewed observations to display.' in review
    else:
        assert '1 of 50 observations reviewed' in review and review.count('-OBS-') == 1
        assert 'Not reviewed' not in review
print('Current sample and 1-of-50 review layout verified.')
