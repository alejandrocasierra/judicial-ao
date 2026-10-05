-- 032 — Normaliza la confianza OCR a 100% por página y por modo.
-- Decisión de producto: toda página arranca al 100% de confianza para el modo que
-- la procesó (basico / document_ai); el % sólo baja cuando una persona edita
-- contenido real (ver app/services/ocr_confidence.py). Se retira además la marca
-- "Revisar" del OCR (la confianza del motor ya no la activa).

UPDATE document_pages SET ocr_confidence = 1.0, needs_review = false;
UPDATE document_ocr_versions SET ocr_confidence = 1.0;
