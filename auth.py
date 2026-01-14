# auth.py
from fastapi import Depends, Header, HTTPException
import base64
import os

BASIC_AUTH_ENABLED = os.getenv("BASIC_AUTH_ENABLED", "false").lower() == "true"
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "password")

def verify_auth(auth_header: str = Header(None, alias="Authorization")):
    """Verify Basic Authentication"""
    if not BASIC_AUTH_ENABLED:
        return True

    if not auth_header or not auth_header.startswith("Basic "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic realm='TBL Scanner API'"}
        )

    try:
        encoded = auth_header.split(" ")[1]
        decoded = base64.b64decode(encoded).decode()
        username, password = decoded.split(":", 1)

        if username != BASIC_AUTH_USERNAME or password != BASIC_AUTH_PASSWORD:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        return True
    except Exception:
        raise HTTPException(status_code=401, detail="Authentication failed")
