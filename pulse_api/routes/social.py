"""Social endpoints — recent posts and AI summaries for an artist."""

from flask import Blueprint, jsonify

from pulse_api.auth import require_auth
from pulse_api.db import supabase

social_bp = Blueprint("social", __name__)


@social_bp.get("/artists/<artist_id>/social")
@require_auth
def list_artist_social(artist_id):
    """List recent social posts for an artist."""
    posts = (
        supabase.table("social_posts")
        .select("*")
        .eq("artist_id", artist_id)
        .order("posted_at", desc=True)
        .limit(50)
        .execute()
    )
    return jsonify(posts.data)


@social_bp.get("/artists/<artist_id>/social/summary")
@require_auth
def get_social_summary(artist_id):
    """Get the latest AI summary for an artist's social activity."""
    summary = (
        supabase.table("social_summaries")
        .select("*")
        .eq("artist_id", artist_id)
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    if not summary.data:
        return jsonify({"error": "No summaries yet"}), 404
    return jsonify(summary.data[0])
