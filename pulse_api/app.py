"""Flask application entrypoint.

Routes are defined as blueprints in `pulse_api.routes`. This module wires
them onto the Flask app, configures logging, and exposes `run()` for the
`pulse` CLI script (see pyproject.toml).
"""

import gzip
import logging

from flask import Flask, request
from flask_cors import CORS

from pulse_api.routes import (
    account_bp,
    admin_bp,
    artists_bp,
    email_prefs_bp,
    events_bp,
    favourites_bp,
    notifications_bp,
    pages_bp,
    social_bp,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__)
CORS(app)

logger = logging.getLogger(__name__)

app.register_blueprint(account_bp)
app.register_blueprint(artists_bp)
app.register_blueprint(events_bp)
app.register_blueprint(favourites_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(social_bp)
app.register_blueprint(email_prefs_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(pages_bp)


@app.after_request
def compress_response(response):
    """Gzip JSON responses for clients that accept it (URLSession does by
    default). Event lists run to hundreds of KB uncompressed — on cellular
    that transfer time keeps the app's pull-to-refresh request in flight
    long enough for SwiftUI to cancel it."""
    if (
        response.status_code < 300
        and not response.direct_passthrough
        and response.mimetype == "application/json"
        and (response.content_length or 0) > 1024
        and "gzip" not in response.headers.get("Content-Encoding", "")
        and "gzip" in request.headers.get("Accept-Encoding", "").lower()
    ):
        response.set_data(gzip.compress(response.get_data(), compresslevel=6))
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(response.content_length)
        response.headers.add("Vary", "Accept-Encoding")
    return response


@app.after_request
def log_response(response):
    logger.info(
        "[RESPONSE] %s %s → %d",
        request.method,
        request.path,
        response.status_code,
    )
    if response.status_code in (403, 405):
        logger.warning(
            "[RESPONSE] Unexpected %d — response body: %s",
            response.status_code,
            response.get_data(as_text=True)[:500],
        )
    return response


def run():
    import os
    port = int(os.environ.get("PORT", "3000"))
    if os.environ.get("FLASK_ENV") == "development":
        app.run(host="0.0.0.0", port=port, debug=True)
    else:
        import subprocess
        import sys
        workers = os.environ.get("WEB_CONCURRENCY", "4")
        subprocess.run([
            sys.executable, "-m", "gunicorn",
            "pulse_api.app:app",
            "--bind", f"0.0.0.0:{port}",
            "--workers", workers,
            "--threads", "2",
            "--timeout", "120",
        ])


if __name__ == "__main__":
    run()
