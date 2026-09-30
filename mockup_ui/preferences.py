"""Persisted user defaults, separate from any running or completed analysis."""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings


@dataclass(frozen=True)
class Preferences:
    theme: str = "light"
    confidence_percent: int = 50
    cctv_intelligence: bool = True
    temporal_consistency: bool = True
    autoplay_results: bool = True
    export_folder: str = ""


KEYS = {
    "theme": "appearance/theme",
    "confidence_percent": "analysis/default_confidence_percent",
    "cctv_intelligence": "analysis/default_cctv_intelligence",
    "temporal_consistency": "analysis/default_temporal_consistency",
    "autoplay_results": "playback/autoplay_results",
    "export_folder": "export/default_folder",
}


def settings_store():
    return QSettings("ForensiKada", "VideoDetection")


def load_preferences(store=None):
    store = settings_store() if store is None else store
    defaults = Preferences()
    values = {field: store.value(key, getattr(defaults, field)) for field, key in KEYS.items()}
    values["theme"] = "dark" if values["theme"] == "dark" else "light"
    try:
        values["confidence_percent"] = max(10, min(95, int(values["confidence_percent"])))
    except (TypeError, ValueError, OverflowError):
        values["confidence_percent"] = defaults.confidence_percent
    for field in ("cctv_intelligence", "temporal_consistency", "autoplay_results"):
        value = str(values[field]).strip().lower()
        values[field] = value not in ("false", "0") if value in ("true", "false", "1", "0") else getattr(defaults, field)
    values["temporal_consistency"] = values["cctv_intelligence"] and values["temporal_consistency"]
    values["export_folder"] = str(values["export_folder"] or "")
    return Preferences(**values)


def save_preferences(preferences, store=None):
    store = settings_store() if store is None else store
    for field, key in KEYS.items():
        store.setValue(key, getattr(preferences, field))
    store.sync()
    if store.status() != QSettings.Status.NoError:
        raise OSError("Settings could not be saved. Check your user profile's write permissions.")


def export_start_directory():
    folder = load_preferences().export_folder
    if folder and Path(folder).is_dir():
        return folder
    return str(Path.home())
