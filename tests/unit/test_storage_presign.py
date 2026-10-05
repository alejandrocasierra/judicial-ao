"""UT-STO — subida directa al storage (presign)."""
import pytest

from app.services.storage import LocalStorage

pytestmark = pytest.mark.unit


def test_ut_sto_01_local_storage_has_no_presign(tmp_path):
    """En storage local no hay subida directa: el cliente usa la subida normal."""
    st = LocalStorage(tmp_path)
    assert st.presign_put("cases/x/originals/abc", "video/mp4") is None
