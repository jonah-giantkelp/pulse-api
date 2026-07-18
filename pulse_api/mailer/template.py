"""HTML email template for the Pulse daily digest.

Matches the app's brand identity:
- Dark background (#0D0D0D)
- Lime/chartreuse accent (#A9D65C)
- Monospace typography (Courier New fallback, SF Mono if available)
- Dark card style with subtle borders (#2A2A2A)
"""

from datetime import datetime


def _format_date_badge(dt: datetime) -> str:
    """Format a date like 'FRI 22 MAY'."""
    return dt.strftime("%a %d %b").upper()


def _format_time(time_str: str | None) -> str:
    """Format a 24h time string for display."""
    if not time_str:
        return ""
    try:
        h, m = time_str.split(":")[:2]
        return f"{h}:{m}"
    except (ValueError, AttributeError):
        return ""


def _build_event_card(event: dict) -> str:
    """Build a single event card matching the app's card design."""
    title = event.get("title", "Untitled Event")
    venue = event.get("venue", "")
    city = event.get("city", "")
    country = event.get("country", "")
    time_str = _format_time(event.get("time"))
    source = event.get("source", "")
    ticket_url = event.get("ticket_url", "")
    artists = event.get("artists", [])

    # Date badge
    date_str = event.get("date", "")
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        date_badge = _format_date_badge(dt)
    except (ValueError, AttributeError):
        date_badge = "TBC"

    # Artist images and names (inline on desktop)
    artist_html = ""
    for a in artists[:3]:
        img_url = a.get("image_url", "")
        name = a.get("name", "")
        if img_url:
            artist_html += (
                f'<img src="{img_url}" alt="{name}" '
                f'width="32" height="32" '
                f'style="width:32px;height:32px;border-radius:50%;'
                f'object-fit:cover;margin-right:8px;vertical-align:middle;" />'
            )
        artist_html += (
            f'<span style="color:#FFFFFF;font-size:14px;'
            f'font-family:\'SF Mono\',\'Courier New\',monospace;'
            f'vertical-align:middle;margin-right:12px;">{name}</span>'
        )

    # Location line
    location_parts = [p for p in [venue, city, country] if p]
    location = " \u00b7 ".join(location_parts)

    # Time display
    time_html = ""
    if time_str:
        time_html = (
            f'<span style="color:#AAAAAA;font-size:12px;'
            f'font-family:\'SF Mono\',\'Courier New\',monospace;">'
            f'\u23f0 {time_str}</span>'
        )

    # Source badge
    source_display = source.replace("_", " ").title() if source else ""
    source_html = ""
    if source_display:
        source_html = (
            f'<span style="display:inline-block;padding:2px 8px;'
            f'border:1px solid #3A3A3A;border-radius:4px;'
            f'color:#999999;font-size:10px;'
            f'font-family:\'SF Mono\',\'Courier New\',monospace;'
            f'text-transform:uppercase;margin-left:8px;">'
            f'{source_display}</span>'
        )

    # CTA button
    cta_html = ""
    if ticket_url:
        cta_html = (
            f'<a href="{ticket_url}" target="_blank" '
            f'class="pulse-cta" '
            f'style="display:inline-block;margin-top:12px;'
            f'padding:8px 20px;background-color:#A9D65C;'
            f'color:#0D0D0D;text-decoration:none;'
            f'font-family:\'SF Mono\',\'Courier New\',monospace;'
            f'font-size:12px;font-weight:bold;border-radius:4px;'
            f'letter-spacing:1px;">TICKETS &rarr;</a>'
        )

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="margin-bottom:12px;">
      <tr>
        <td class="pulse-card" style="background-color:#1A1A1A;border:1px solid #2A2A2A;
                    border-radius:8px;padding:16px;">
          <!-- Header: artists + date badge -->
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td class="pulse-artists" style="vertical-align:middle;">
                {artist_html}
              </td>
              <td align="right" style="vertical-align:middle;white-space:nowrap;">
                <span class="pulse-date" style="display:inline-block;padding:4px 12px;
                             border:1px solid #A9D65C;border-radius:4px;
                             color:#A9D65C;font-size:11px;
                             font-family:'SF Mono','Courier New',monospace;
                             letter-spacing:1px;white-space:nowrap;">
                  {date_badge}
                </span>
              </td>
            </tr>
          </table>

          <!-- Event title -->
          <div class="pulse-title" style="margin-top:10px;color:#FFFFFF;font-size:15px;
                      font-family:'SF Mono','Courier New',monospace;
                      font-weight:bold;line-height:1.3;">
            {title}
          </div>

          <!-- Venue & location -->
          <div class="pulse-location" style="margin-top:6px;color:#888888;font-size:12px;
                      font-family:'SF Mono','Courier New',monospace;line-height:1.4;">
            \U0001f4cd {location}
            {time_html}
            {source_html}
          </div>

          <!-- Ticket CTA -->
          {cta_html}
        </td>
      </tr>
    </table>
    """


def build_digest_html(events: list[dict], user_email: str, city: str | None = None) -> str:
    """Build the full digest email HTML."""
    event_count = len(events)
    today = datetime.now().strftime("%A %d %B").upper()

    # Group events by date
    events_by_date: dict[str, list[dict]] = {}
    for event in events:
        date_str = event.get("date", "")
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            date_key = dt.strftime("%Y-%m-%d")
            date_display = _format_date_badge(dt)
        except (ValueError, AttributeError):
            date_key = "9999-99-99"
            date_display = "TBC"
        events_by_date.setdefault(date_key, []).append(
            {**event, "_date_display": date_display}
        )

    # Build event sections grouped by date
    events_html = ""
    for date_key in sorted(events_by_date.keys()):
        group = events_by_date[date_key]
        date_display = group[0]["_date_display"]
        events_html += f"""
        <div style="margin-top:24px;margin-bottom:8px;color:#A9D65C;
                    font-size:12px;font-family:'SF Mono','Courier New',monospace;
                    letter-spacing:2px;">
          {date_display}
        </div>
        """
        for event in group:
            events_html += _build_event_card(event)

    # Empty state
    if not events:
        events_html = """
        <div style="text-align:center;padding:40px 0;color:#666666;
                    font-family:'SF Mono','Courier New',monospace;
                    font-size:14px;">
          No new events discovered since yesterday.<br/>
          We'll keep scanning.
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Pulse Daily Digest</title>
  <style>
    @media only screen and (max-width: 480px) {{
      /* More breathing room in cards */
      .pulse-card {{
        padding: 14px 12px !important;
      }}
      /* Let artists wrap cleanly */
      .pulse-artists {{
        display: block !important;
        padding-bottom: 8px !important;
      }}
      .pulse-artists img {{
        width: 26px !important;
        height: 26px !important;
      }}
      .pulse-artists span {{
        font-size: 13px !important;
        line-height: 1.6 !important;
      }}
      /* Scale down date badge */
      .pulse-date {{
        font-size: 10px !important;
        padding: 3px 8px !important;
      }}
      /* Slightly smaller title */
      .pulse-title {{
        font-size: 14px !important;
      }}
      /* Tighter location text */
      .pulse-location {{
        font-size: 11px !important;
      }}
      /* Full-width CTA on mobile */
      .pulse-cta {{
        display: block !important;
        text-align: center !important;
        padding: 12px 16px !important;
      }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#0A0A0A;
             font-family:'SF Mono','Courier New',monospace;">

  <!-- Wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#0A0A0A;">
    <tr>
      <td align="center" style="padding:24px 16px;">

        <!-- Main container -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:560px;background-color:#0D0D0D;
                      border-radius:12px;overflow:hidden;">

          <!-- Header -->
          <tr>
            <td style="padding:32px 24px 12px;text-align:center;">
              <div style="color:#A9D65C;font-size:28px;
                          font-family:'SF Mono','Courier New',monospace;
                          font-weight:bold;letter-spacing:8px;">
                P U L S E
              </div>
              <div style="margin-top:8px;color:#555555;font-size:11px;
                          font-family:'SF Mono','Courier New',monospace;
                          letter-spacing:2px;">
                DAILY DIGEST &middot; {today}
              </div>
              {"" if not city else f'''<div style="margin-top:10px;color:#888888;font-size:12px;
                          font-family:'SF Mono','Courier New',monospace;">
                \U0001f4cd {city}
              </div>'''}
            </td>
          </tr>

          <!-- Divider -->
          <tr>
            <td style="padding:0 24px;">
              <div style="height:1px;background-color:#1F1F1F;"></div>
            </td>
          </tr>

          <!-- Summary line -->
          <tr>
            <td style="padding:16px 24px 8px;">
              <div style="color:#AAAAAA;font-size:13px;
                          font-family:'SF Mono','Courier New',monospace;">
                <span style="color:#A9D65C;font-weight:bold;">
                  {event_count}</span> new event{"s" if event_count != 1 else ""}
                discovered for your artists
              </div>
            </td>
          </tr>

          <!-- Events -->
          <tr>
            <td style="padding:8px 24px 24px;">
              {events_html}
            </td>
          </tr>

          <!-- Divider -->
          <tr>
            <td style="padding:0 24px;">
              <div style="height:1px;background-color:#1F1F1F;"></div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:20px 24px 28px;text-align:center;">
              <div style="color:#444444;font-size:10px;
                          font-family:'SF Mono','Courier New',monospace;
                          letter-spacing:1px;line-height:1.6;">
                You're receiving this because you enabled daily digests in Pulse.
                <br/>
                Sent to {user_email}
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>

</body>
</html>
"""


def build_digest_text(events: list[dict]) -> str:
    """Build a plain-text fallback for the digest."""
    if not events:
        return "PULSE DAILY DIGEST\n\nNo new events discovered since yesterday.\n"

    lines = ["PULSE DAILY DIGEST", "=" * 40, ""]
    for event in events:
        title = event.get("title", "Untitled")
        venue = event.get("venue", "")
        city = event.get("city", "")
        date_str = event.get("date", "")
        ticket_url = event.get("ticket_url", "")
        artists = event.get("artists", [])
        artist_names = ", ".join(a.get("name", "") for a in artists)

        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            date_display = _format_date_badge(dt)
        except (ValueError, AttributeError):
            date_display = "TBC"

        lines.append(f"{date_display}")
        if artist_names:
            lines.append(f"  {artist_names}")
        lines.append(f"  {title}")
        if venue:
            lines.append(f"  {venue}, {city}")
        if ticket_url:
            lines.append(f"  Tickets: {ticket_url}")
        lines.append("")

    return "\n".join(lines)
