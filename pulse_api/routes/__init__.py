"""Route blueprints for the Flask API.

Each module here defines a Blueprint covering one category of endpoints.
`pulse_api.app` registers them on the Flask app at import time.
"""

from pulse_api.routes.account import account_bp
from pulse_api.routes.admin import admin_bp
from pulse_api.routes.artists import artists_bp
from pulse_api.routes.email_preferences import email_prefs_bp
from pulse_api.routes.events import events_bp
from pulse_api.routes.favourites import favourites_bp
from pulse_api.routes.pages import pages_bp
from pulse_api.routes.social import social_bp

__all__ = [
    "account_bp",
    "admin_bp",
    "artists_bp",
    "email_prefs_bp",
    "events_bp",
    "favourites_bp",
    "pages_bp",
    "social_bp",
]
