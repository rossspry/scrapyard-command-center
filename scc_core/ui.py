from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from flask import Flask, Response, jsonify, request

logger = logging.getLogger(__name__)

app = Flask(__name__)


def _load_credentials() -> Tuple[str, str]:
    username = os.getenv("SCC_UI_USERNAME", "").strip()
    password = os.getenv("SCC_UI_PASSWORD", "").strip()

    if not username or not password:
        raise RuntimeError("SCC_UI_USERNAME and SCC_UI_PASSWORD must be set.")

    if username == "changeme" or password == "changeme":
        raise RuntimeError("SCC_UI_USERNAME/SCC_UI_PASSWORD cannot remain 'changeme'.")

    return username, password


_CREDENTIALS: Optional[Tuple[str, str]] = None


def _configured_credentials() -> tuple[str, str]:
    global _CREDENTIALS
    if _CREDENTIALS is None:
        _CREDENTIALS = _load_credentials()
    return _CREDENTIALS


# Fail fast on import if credentials are missing or placeholders
_configured_credentials()


def _authenticate() -> Response:
    return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="SCC"'})


@app.before_request
def _require_auth():
    credentials = _configured_credentials()
    if not credentials:
        return None

    auth = request.authorization
    if not auth or (auth.username, auth.password) != credentials:
        return _authenticate()

    return None


@app.get("/")
def index():
    return jsonify({
        "status": "ok",
        "message": "Scrapyard Command Center UI",
        "endpoints": ["/", "/healthz"],
    })


@app.get("/healthz")
def healthcheck():
    return jsonify({"status": "ok"})


if __name__ == "__main__":  # pragma: no cover
    bind_address = os.getenv("SCC_UI_BIND", "127.0.0.1")
    app.run(host=bind_address, port=8081)
