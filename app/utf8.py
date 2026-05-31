"""UTF-8 JSON Response — ersetzt FastAPIs Standard-JSONResponse für alle Router."""
from __future__ import annotations

import json
from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    """
    JSONResponse mit ensure_ascii=False und explizitem charset=utf-8 im Content-Type.
    Ohne charset=utf-8 dekodiert PowerShell 5.1 mit Windows-1252 → Umlaute kaputt.
    """
    media_type = "application/json; charset=utf-8"

    def render(self, content: object) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")
