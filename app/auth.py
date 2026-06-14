import os
from fastapi import Security, HTTPException, status
from fastapi.security.api_key import APIKeyHeader

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_header)) -> None:
    expected = os.getenv("TRADE_SIGNALS_API_KEY", "")
    if not expected:
        return  # no key configured — open in dev mode
    if key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
