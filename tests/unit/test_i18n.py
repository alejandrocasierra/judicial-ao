"""UT-I18N — compatibilidad español/inglés."""
import re
from pathlib import Path

import pytest

from app.core.i18n import catalogs, negotiate, t

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _keys(d, prefix=""):
    out = set()
    for k, v in d.items():
        out |= _keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
    return out


def test_ut_i18n_01_catalogs_have_same_keys():
    c = catalogs()
    assert _keys(c["es"]) == _keys(c["en"])


def test_ut_i18n_02_negotiation():
    assert negotiate("en-US,en;q=0.9") == "en"
    assert negotiate("es-CO") == "es"
    assert negotiate("fr-FR") == negotiate(None)  # fallback DEFAULT_LOCALE
    assert negotiate(None, "en") == "en"


def test_ut_i18n_03_every_error_code_is_translated():
    codes = set()
    for f in (ROOT / "apps/api/app").rglob("*.py"):
        codes |= set(re.findall(r'AppError\("([A-Z_]+)"', f.read_text(encoding="utf-8")))
    missing = {c for c in codes for loc in ("es", "en") if c not in catalogs()[loc]["errors"]}
    assert not missing, missing


def test_ut_i18n_04_translations_differ():
    assert t("errors.CASE_NOT_FOUND", "es") != t("errors.CASE_NOT_FOUND", "en")


def test_ut_i18n_05_enums_cover_db_values():
    sql = (ROOT / "apps/api/db/sql/010_schema.sql").read_text(encoding="utf-8")
    fact_states = set(re.search(r"status IN \('ALLEGED'.*?\)\)", sql).group(0).split("'")[1::2])
    assert fact_states == set(catalogs()["es"]["enums"]["fact_status"])
