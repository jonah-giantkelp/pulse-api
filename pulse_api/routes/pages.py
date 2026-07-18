"""Public web pages required for App Store publishing.

Plain HTML served straight from the API so we don't need separate hosting:
a landing page, the privacy policy, and a support page. App Store Connect
links to /privacy (Privacy Policy URL) and /support (Support URL).
"""

from flask import Blueprint

pages_bp = Blueprint("pages", __name__)

CONTACT_EMAIL = "jonah@giantkelp.xyz"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Pulse GK</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; padding: 2.5rem 1.25rem 4rem;
    background: #0d0d0d; color: #d9d9d9;
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    line-height: 1.65;
  }}
  main {{ max-width: 640px; margin: 0 auto; }}
  h1, h2 {{ color: #b3e264; font-weight: 700; letter-spacing: 0.06em; }}
  h1 {{ font-size: 1.6rem; text-transform: uppercase; }}
  h2 {{ font-size: 1.05rem; margin-top: 2.2rem; text-transform: uppercase; }}
  a {{ color: #b3e264; }}
  .tagline {{ color: #8a8a8a; letter-spacing: 0.25em; text-transform: uppercase; font-size: 0.8rem; }}
  .footer {{ margin-top: 3rem; color: #666; font-size: 0.8rem; }}
</style>
</head>
<body>
<main>
{body}
<p class="footer">Pulse GK · GiantKelp · <a href="mailto:{email}">{email}</a></p>
</main>
</body>
</html>"""


def _render(title: str, body: str) -> str:
    return _PAGE.format(title=title, body=body, email=CONTACT_EMAIL)


@pages_bp.get("/")
def landing():
    return _render(
        "Pulse GK",
        f"""
<h1>Pulse</h1>
<p class="tagline">Gig tracker</p>
<p>Follow the artists you care about and Pulse keeps watch — surfacing
upcoming shows from ticketing platforms and the artists' own channels,
de-duplicated into one clean feed, with an optional daily email digest.</p>
<p><a href="/privacy">Privacy policy</a> · <a href="/support">Support</a></p>
""",
    )


@pages_bp.get("/privacy")
def privacy():
    return _render(
        "Privacy Policy",
        f"""
<h1>Privacy Policy</h1>
<p class="tagline">Last updated 18 July 2026</p>

<p>Pulse GK ("Pulse") is built by GiantKelp. This policy explains what we
collect and how it is used.</p>

<h2>What we collect</h2>
<p>When you create an account we store your <strong>email address</strong> and
a securely hashed password. As you use the app we store data you create:
the artists you follow, events you favourite, email digest preferences, and —
only if you enable notifications — a device push token.</p>

<h2>What we use it for</h2>
<p>Your email is used to sign you in and, if you opt in, to send you a daily
digest of new shows for artists you follow. Followed artists and favourites
exist solely to power your feed. Push tokens are used only to deliver the
notifications you asked for. We do not use your data for advertising, we do
not profile you, and we do not sell or share your data with third parties for
their own purposes.</p>

<h2>Tracking and analytics</h2>
<p>Pulse contains no third-party analytics, advertising SDKs, or trackers.</p>

<h2>Where your data lives</h2>
<p>Account data is stored with Supabase (our database and authentication
provider). The service runs on Railway. Digest emails are delivered via
Postmark. Each processes data only on our behalf to run the service.</p>

<h2>Event information</h2>
<p>Concert and event listings shown in the app are gathered from publicly
available sources such as ticketing platforms and artists' own websites.
This is public information about artists, not data about you.</p>

<h2>Deleting your account</h2>
<p>You can delete your account at any time from Settings inside the app.
This permanently removes your account and all associated data — followed
artists, favourites, preferences, and push tokens.</p>

<h2>Contact</h2>
<p>Questions or requests about your data:
<a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
""",
    )


@pages_bp.get("/support")
def support():
    return _render(
        "Support",
        f"""
<h1>Support</h1>
<p class="tagline">Pulse GK</p>
<p>Having trouble with Pulse, found a bug, or have a feature request?
Email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> and we'll get
back to you.</p>

<h2>Common questions</h2>
<p><strong>How do I delete my account?</strong> Open the app, go to Settings,
and choose Delete Account. This removes all your data permanently.</p>
<p><strong>How do I stop the daily email?</strong> Turn off the digest in the
app's email preferences, or use the unsubscribe link in any digest email.</p>
<p><strong>An event is wrong or missing.</strong> Listings come from public
ticketing sources; coverage varies by artist and venue. Let us know and
we'll look into it.</p>
""",
    )
