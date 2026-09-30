# Restore point before the iOS UI refresh

Snapshot: `before-ios-refresh-20260930-165236.zip`

Created before the broader iOS styling and stronger animations. It includes the
previous animation implementation and all uncommitted source changes at that time.
All 401 archived files were checked against SHA-256 hashes in
`RESTORE-MANIFEST.json` inside the archive. The manifest also records the Git HEAD.

To request a rollback, say: **Restore the UI to before the iOS refresh.**

For a selective rollback, recover these existing files from the archive:

- `mockup_ui/app.py`
- `mockup_ui/page_shell.py`
- `mockup_ui/motion.py`
- `mockup_ui/multi_camera_panel.py`
- `mockup_ui/review_panel.py`
- `mockup_ui/settings_dialog.py`
- `mockup_ui/ui_theme.py`
- `mockup_ui/test_motion.py`
- `mockup_ui/test_multi_camera.py`
- `mockup_ui/test_ui_appearance.py`

Then remove the new `mockup_ui/ios_theme.qss`. Preserve any later unrelated edits
before restoring files. Do not reset to Git HEAD: the snapshot includes changes
that were not yet committed.

Ignored model weights, videos, Python environments and saved analyst reviews were
not copied and are left in place. No PDF source or exported PDF was modified by
the UI refresh. The ZIP is local and ignored by Git; keep a copy if you move this
project to another computer.
