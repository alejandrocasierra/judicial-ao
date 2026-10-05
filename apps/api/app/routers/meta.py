from fastapi import APIRouter, Request

from app.core.i18n import catalogs, negotiate
from app.domain.jurisdiction import jurisdictions

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/enums")
def enums(request: Request):
    """Etiquetas localizadas para la UI (es/en)."""
    loc = negotiate(request.headers.get("accept-language"))
    return {"locale": loc, "enums": catalogs()[loc]["enums"]}


@router.get("/jurisdictions")
def list_jurisdictions(request: Request):
    loc = negotiate(request.headers.get("accept-language"))
    return [{"code": j["code"], "name": j["name"][loc], "case_number_hint": j["case_number_hint"][loc]}
            for j in jurisdictions().values()]
