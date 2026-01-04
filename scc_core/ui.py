from __future__ import annotations

import logging
import os
from typing import Optional

from flask import Flask, Response, jsonify, request

logger = logging.getLogger(__name__)

app = Flask(__name__)


def _configured_credentials() -> Optional[tuple[str, str]]:
    username = os.getenv("SCC_UI_USERNAME")
    password = os.getenv("SCC_UI_PASSWORD")
    if username and password:
        return username, password
    return None


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
    app.run(host="0.0.0.0", port=8081)
