-- URL base del API por modelo (opcional): permite apuntar a un proxy, a otra
-- región o a una VPS de Google sin tocar el entorno. Vacío = la URL del proveedor.
ALTER TABLE ai_models ADD COLUMN IF NOT EXISTS api_base_url text;
