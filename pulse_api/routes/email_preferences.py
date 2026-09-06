"""Email digest preference endpoints — get/put/delete and city/country lists."""

from flask import Blueprint, g, jsonify, request

from pulse_api.auth import require_auth
from pulse_api.db import supabase
from pulse_api.sync.geo import CITIES, resolve_pref_city

email_prefs_bp = Blueprint("email_preferences", __name__)


@email_prefs_bp.get("/cities")
@require_auth
def list_cities():
    """The canonical trackable cities — what the app's picker offers."""
    return jsonify([
        {"key": c["key"], "name": c["display"], "country": c["country"]}
        for c in CITIES
    ])


@email_prefs_bp.get("/me/email-preferences")
@require_auth
def get_email_preferences():
    """Get the current user's email digest preferences."""
    result = (
        supabase.table("user_email_preferences")
        .select("*")
        .eq("user_id", g.user_id)
        .execute()
    )
    if not result.data:
        # Trigger should have created a row on signup; this path only
        # fires for legacy users (pre-trigger) or users whose auth.users.email
        # was null. Surface a sensible London-default shape regardless.
        return jsonify({
            "email": None,
            "recipients": [],
            "digest_enabled": False,
            "push_enabled": False,
            "default_cities": ["London"],
            "default_countries": [],
        })
    return jsonify(result.data[0])


@email_prefs_bp.put("/me/email-preferences")
@require_auth
def update_email_preferences():
    """Set or update the current user's email and digest toggle.

    Body: { "email": "user@example.com", "digest_enabled": true }
    """
    body = request.get_json()
    email = body.get("email")
    recipients = body.get("recipients")
    digest_enabled = body.get("digest_enabled")
    push_enabled = body.get("push_enabled")
    default_cities = body.get("default_cities")
    default_countries = body.get("default_countries")

    fields = (email, recipients, digest_enabled, push_enabled, default_cities, default_countries)
    if all(v is None for v in fields):
        return jsonify({"error": "at least one field is required"}), 400

    if recipients is not None and len(recipients) > 5:
        return jsonify({"error": "maximum 5 recipients"}), 400

    update_data = {}
    if recipients is not None:
        update_data["recipients"] = recipients
        # Keep the legacy single-email column in step for older readers
        update_data["email"] = recipients[0] if recipients else None
    elif email is not None:
        update_data["email"] = email
        update_data["recipients"] = [email] if email else []
    if digest_enabled is not None:
        update_data["digest_enabled"] = digest_enabled
    if push_enabled is not None:
        update_data["push_enabled"] = push_enabled
    if default_cities is not None:
        resolved = [resolve_pref_city(c) for c in default_cities]
        unknown = [c for c, r in zip(default_cities, resolved) if r is None]
        if unknown:
            return jsonify({
                "error": f"unknown cities: {', '.join(unknown)}",
                "valid_cities": [c["display"] for c in CITIES],
            }), 400
        # Dedupe while preserving order (aliases can collapse to one city)
        update_data["default_cities"] = list(dict.fromkeys(resolved))
    if default_countries is not None:
        update_data["default_countries"] = [c.upper() for c in default_countries]

    existing = (
        supabase.table("user_email_preferences")
        .select("id")
        .eq("user_id", g.user_id)
        .execute()
    )

    if existing.data:
        update_data["updated_at"] = "now()"
        result = (
            supabase.table("user_email_preferences")
            .update(update_data)
            .eq("user_id", g.user_id)
            .execute()
        )
    else:
        result = (
            supabase.table("user_email_preferences")
            .insert({"user_id": g.user_id, **update_data})
            .execute()
        )

    return jsonify(result.data[0] if result.data else update_data)


@email_prefs_bp.delete("/me/email-preferences")
@require_auth
def delete_email_preferences():
    """Remove the current user's email preferences (opt out entirely)."""
    supabase.table("user_email_preferences").delete().eq(
        "user_id", g.user_id
    ).execute()
    return jsonify({"status": "removed"})


def _get_or_init_prefs_row() -> dict:
    """Fetch the user's prefs row, creating an empty one if missing.

    Returns the row dict. Used by the atomic add/remove endpoints so
    legacy users (pre-trigger) get a row on first interaction.
    """
    existing = (
        supabase.table("user_email_preferences")
        .select("*")
        .eq("user_id", g.user_id)
        .execute()
    )
    if existing.data:
        return existing.data[0]
    # No row yet — caller will need to know we have no email on file.
    return {
        "user_id": g.user_id,
        "email": None,
        "default_cities": ["London"],
        "default_countries": [],
    }


@email_prefs_bp.post("/me/email-preferences/cities")
@require_auth
def add_default_city():
    """Append a city to the user's default_cities (idempotent, case-insensitive)."""
    body = request.get_json() or {}
    city = (body.get("city") or "").strip()
    if not city:
        return jsonify({"error": "city is required"}), 400
    resolved = resolve_pref_city(city)
    if resolved is None:
        return jsonify({
            "error": f"unknown city: {city}",
            "valid_cities": [c["display"] for c in CITIES],
        }), 400
    city = resolved

    row = _get_or_init_prefs_row()
    cities = list(row.get("default_cities") or [])
    if not any(c.lower() == city.lower() for c in cities):
        cities.append(city)

    if row.get("id"):
        result = (
            supabase.table("user_email_preferences")
            .update({"default_cities": cities, "updated_at": "now()"})
            .eq("user_id", g.user_id)
            .execute()
        )
        return jsonify(result.data[0] if result.data else {"default_cities": cities})

    # No row exists — can only happen for legacy users with no email on file.
    return jsonify({
        "error": "email preferences not initialised — set email via PUT /me/email-preferences first",
    }), 400


@email_prefs_bp.delete("/me/email-preferences/cities/<city>")
@require_auth
def remove_default_city(city):
    """Remove a city from the user's default_cities (case-insensitive)."""
    existing = (
        supabase.table("user_email_preferences")
        .select("default_cities")
        .eq("user_id", g.user_id)
        .execute()
    )
    if not existing.data:
        return jsonify({"error": "email preferences not initialised"}), 404

    # Accept an alias for the removal too ("nyc" removes "New York");
    # fall back to raw comparison for legacy non-canonical strings.
    resolved = resolve_pref_city(city)
    targets = {city.lower()} | ({resolved.lower()} if resolved else set())
    cities = [
        c for c in (existing.data[0].get("default_cities") or [])
        if c.lower() not in targets
    ]
    result = (
        supabase.table("user_email_preferences")
        .update({"default_cities": cities, "updated_at": "now()"})
        .eq("user_id", g.user_id)
        .execute()
    )
    return jsonify(result.data[0] if result.data else {"default_cities": cities})


@email_prefs_bp.post("/me/email-preferences/countries")
@require_auth
def add_default_country():
    """Append a country (ISO code, uppercased) to default_countries."""
    body = request.get_json() or {}
    country = (body.get("country") or "").strip().upper()
    if not country:
        return jsonify({"error": "country is required"}), 400

    row = _get_or_init_prefs_row()
    countries = list(row.get("default_countries") or [])
    if country not in countries:
        countries.append(country)

    if row.get("id"):
        result = (
            supabase.table("user_email_preferences")
            .update({"default_countries": countries, "updated_at": "now()"})
            .eq("user_id", g.user_id)
            .execute()
        )
        return jsonify(result.data[0] if result.data else {"default_countries": countries})

    return jsonify({
        "error": "email preferences not initialised — set email via PUT /me/email-preferences first",
    }), 400


@email_prefs_bp.delete("/me/email-preferences/countries/<country>")
@require_auth
def remove_default_country(country):
    """Remove a country from default_countries (matched case-insensitively)."""
    target = country.upper()
    existing = (
        supabase.table("user_email_preferences")
        .select("default_countries")
        .eq("user_id", g.user_id)
        .execute()
    )
    if not existing.data:
        return jsonify({"error": "email preferences not initialised"}), 404

    countries = [
        c for c in (existing.data[0].get("default_countries") or [])
        if c.upper() != target
    ]
    result = (
        supabase.table("user_email_preferences")
        .update({"default_countries": countries, "updated_at": "now()"})
        .eq("user_id", g.user_id)
        .execute()
    )
    return jsonify(result.data[0] if result.data else {"default_countries": countries})
