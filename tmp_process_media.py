"""Prueba end-to-end del pipeline media_asr en un video de muestra."""
import sys
sys.path.insert(0, 'apps/api')
from dotenv import load_dotenv
load_dotenv('.env')

from app.core.db import tx
from app.services.media_pipeline import process_media

ORG_ID = 'b4e6d687-89b0-4b79-a0cc-69bd3532a7e9'
USER_ID = 'fc7ced6f-58e9-46c5-aa7c-b155c424c0fd'
CASE_ID = '182a09e0-deeb-4918-80f6-19cf350b4087'
MEDIA_ID = '57c68fb7-b2c3-4ff0-9f18-d755d40874a4'  # 0044Grabacion1Octubre2025.mp4

with tx(ORG_ID, USER_ID) as conn:
    result = process_media(conn, MEDIA_ID, ORG_ID, CASE_ID, USER_ID)
    print(result)
