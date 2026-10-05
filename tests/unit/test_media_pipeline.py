"""UT-MEDIA — pipeline de procesamiento de audio/video (Fase 3)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.media_pipeline import (
    _assign_speakers_to_segments,
    _extract_self_intro_names,
    _resolve_label_names,
    process_media,
)

pytestmark = pytest.mark.unit


def test_ut_media_00_self_intro_names_for_speakers_without_video():
    """'quien les habla, X' identifica al hablante sin video (p. ej. el juez)."""
    joined = [
        {"speaker_label": "SPEAKER_03", "text": "quien les habla, Álvaro Lúzico Álvarez, que en ejercicio como juez 21"},
        {"speaker_label": "SPEAKER_00", "text": "Gracias, doctora Paola."},
        {"speaker_label": "SPEAKER_01", "text": "Mi nombre es Paola Ibanez y soy la apoderada"},
    ]
    names = _extract_self_intro_names(joined)
    assert names["SPEAKER_03"] == "Álvaro Lúzico Álvarez"
    assert names["SPEAKER_01"] == "Paola Ibanez"
    assert "SPEAKER_00" not in names


def test_ut_media_01_assign_speakers_matches_best_overlap_and_name():
    asr_segments = [
        {"start_ms": 0, "end_ms": 5000, "text": "hola", "confidence": 0.9, "words": []},
        {"start_ms": 6000, "end_ms": 10000, "text": "adios", "confidence": 0.8, "words": []},
    ]
    diarization = [
        {"start_ms": 0, "end_ms": 4500, "label": "SPEAKER_00"},
        {"start_ms": 5500, "end_ms": 10500, "label": "SPEAKER_01"},
    ]
    label_names = {"SPEAKER_00": "Jose Alfredo Molina Ibarra"}

    joined = _assign_speakers_to_segments(asr_segments, diarization, label_names)
    assert len(joined) == 2
    assert joined[0]["speaker_label"] == "SPEAKER_00"
    assert joined[0]["visual_name"] == "Jose Alfredo Molina Ibarra"
    assert joined[1]["speaker_label"] == "SPEAKER_01"
    assert joined[1]["visual_name"] is None


def test_ut_media_02_resolve_label_names_por_votacion():
    """Cada SPEAKER_xx toma el nombre activo más frecuente durante sus turnos."""
    diarization = [
        {"start_ms": 0, "end_ms": 10000, "label": "SPEAKER_00"},
        {"start_ms": 10000, "end_ms": 20000, "label": "SPEAKER_01"},
    ]
    timeline = [
        {"timestamp_ms": 1000, "name": "Alba Lucy Cock Alvarez"},
        {"timestamp_ms": 5000, "name": "Alba Lucy Cock Alvarez"},
        {"timestamp_ms": 15000, "name": "Paola Ibanez"},
    ]
    names = _resolve_label_names(diarization, timeline)
    assert names == {"SPEAKER_00": "Alba Lucy Cock Alvarez", "SPEAKER_01": "Paola Ibanez"}


def test_ut_media_03_process_media_persists_segments_and_speakers():
    conn = MagicMock()

    def fake_one(conn, sql, **params):
        if "UPDATE media" in sql and "RETURNING" in sql and "processing_status = 'ASR_RUNNING'" in sql:
            return {"id": "media-1", "storage_uri": "local://key", "filename": "video.mp4", "mime_type": "video/mp4", "media_type": "video"}
        if "SELECT processing_status FROM media" in sql:
            return None
        if "SELECT id, display_name FROM speakers" in sql:
            return None
        if "INSERT INTO speakers" in sql and "RETURNING id" in sql:
            return {"id": "speaker-1"}
        if "INSERT INTO transcript_segments" in sql and "RETURNING id" in sql:
            return {"id": "seg-1"}
        if "UPDATE media" in sql and "duration_ms" in sql:
            return {"id": "media-1"}
        return None

    with patch("app.services.media_pipeline.one", side_effect=fake_one):
        with patch("app.services.media_pipeline.get_asr_provider") as mock_asr:
            mock_asr.return_value.transcribe.return_value = [
                {"start_ms": 0, "end_ms": 5000, "text": "hola", "confidence": 0.9, "words": []},
            ]
            with patch("app.services.media_pipeline.diarize") as mock_diarize:
                mock_diarize.return_value = [
                    {"start_ms": 0, "end_ms": 5000, "label": "SPEAKER_00"},
                ]
                with patch("app.services.media_pipeline.extract_active_speaker_timeline") as mock_visual:
                    mock_visual.return_value = [
                        {"timestamp_ms": 1000, "name": "Jose Alfredo Molina Ibarra"},
                    ]
                    with patch("app.services.media_pipeline.storage") as mock_storage:
                        mock_storage.return_value.get.return_value = b"fake video"
                        with patch("app.services.media_pipeline.key_from_uri", return_value="key"):
                            result = process_media(conn, "media-1", "org-1", "case-1", "user-1")

    assert result["segments"] == 1
    assert result["speakers"] == 1
    assert result["named_speakers"] == 1
    assert result["label_names"] == {"SPEAKER_00": "Jose Alfredo Molina Ibarra"}
    assert result["status"] == "ASR_COMPLETE"
    assert result["needs_review_count"] == 0
