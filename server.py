"""
Kaltura Genie MCP Server
========================
Wraps the Genie /assistant/converse endpoint so Claude can query Genie and
return its structured, LLM-processed answers directly to the user.

Two output modes (Genie decides format; caller can override):
  flashcards  — Default. Genie returns a set of flashcards, each with text and
                an optional video clip (entry_id + start/end seconds).
  text        — Plain markdown answer, no video clips.

Claude's role is to pass Genie's answer through faithfully, not to rewrite it.

Auth modes (use one):

  Mode A — Static KS (personal / development):
    Set GENIE_KS to a Kaltura Session token from your MediaSpace Genie page.
    Tokens expire (typically within 24 hours); grab a fresh one when you get 401s.

  Mode B — Enterprise / programmatic (recommended for teams):
    IT deploys KALTURA_PARTNER_ID, KALTURA_ADMIN_SECRET, and GENIE_ID in the
    shared config. No user ID lives in the config file.

    Each user runs the `genie_set_user` tool once to register their Kaltura
    userId (typically their SSO email). The server stores it locally in
    ~/.kaltura_genie_user and generates a per-user KS on every request,
    caching it for ~55 minutes before auto-refreshing.

    GENIE_ID is the numeric Genie knowledge-base ID configured in your KMS
    genieai module (visible in the KMS admin console under Genie settings).
    It is passed as the `genieid:<id>` KS privilege, which routes the session
    to the correct knowledge base.

Setup:
  1. pip install mcp httpx pyyaml
  2. Set env vars (see modes above)
  3. Add to claude_desktop_config.json (see install.sh / README)

Auth:  Authorization: KS <token>
URL:   https://genie.nvp1.ovp.kaltura.com/assistant/converse
"""

import os
import json
import re
import time
import httpx
from mcp.server.fastmcp import FastMCP

# ── Config ──────────────────────────────────────────────────────────────────
GENIE_URL = os.getenv("GENIE_URL", "https://genie.nvp1.ovp.kaltura.com/assistant/converse")

# Mode A — static KS
GENIE_KS = os.getenv("GENIE_KS", "")

# Mode B — enterprise / programmatic KS generation
# KALTURA_USER_ID is intentionally NOT in the config — each user sets it locally
# via the `genie_set_user` tool, which writes to ~/.kaltura_genie_user.
# IT deploys PARTNER_ID / ADMIN_SECRET / GENIE_ID org-wide; users never touch them.
KALTURA_PARTNER_ID   = os.getenv("KALTURA_PARTNER_ID", "")
KALTURA_ADMIN_SECRET = os.getenv("KALTURA_ADMIN_SECRET", "")
GENIE_ID             = os.getenv("GENIE_ID", "")  # numeric knowledge-base ID, e.g. "295190462"
KALTURA_SESSION_URL  = os.getenv("KALTURA_SESSION_URL",
                                  "https://www.kaltura.com/api_v3/service/session/action/start")

# Local file where each user stores their Kaltura userId (written by genie_set_user)
_USER_ID_FILE = os.path.expanduser("~/.kaltura_genie_user")

# KS expiry is set to 1 hour; we refresh 5 minutes early.
# Cache is keyed by user_id so switching users always gets a fresh token.
_KS_TTL_SECS    = 3600
_KS_BUFFER_SECS = 300
_ks_cache: dict = {}   # user_id -> {"token": str, "expires_at": float}

mcp = FastMCP("kaltura-genie")


# ── User identity ─────────────────────────────────────────────────────────────
def _get_user_id() -> str:
    """
    Resolve the Kaltura userId for the current user.
    Priority order:
      1. KALTURA_USER_ID env var (overrides everything — useful for testing)
      2. ~/.kaltura_genie_user file (written by genie_set_user tool)
    Raises ValueError with a helpful message if neither is set.
    """
    # Env var override (useful for dev / testing)
    env_uid = os.getenv("KALTURA_USER_ID", "").strip()
    if env_uid:
        return env_uid

    # Per-user local file (normal enterprise path)
    if os.path.isfile(_USER_ID_FILE):
        uid = open(_USER_ID_FILE).read().strip()
        if uid:
            return uid

    raise ValueError(
        "Kaltura user identity not set. "
        "Ask Claude to run: genie_set_user(user_id=\"your@email.com\") — "
        "you only need to do this once per machine."
    )


# ── KS resolution ─────────────────────────────────────────────────────────────
def _generate_ks(user_id: str) -> str:
    """
    Generate a fresh Kaltura Session scoped to the given userId.
    Uses type=2 (admin) with the minimal privilege set required by Genie:
      setrole:PLAYBACK_BASE_ROLE,sview:,enableentitlement,genieid:<GENIE_ID>
    The userId is passed so Genie's activity is attributed to the real user.
    """
    privs = f"setrole:PLAYBACK_BASE_ROLE,sview:,enableentitlement,genieid:{GENIE_ID}"
    params = {
        "secret":     KALTURA_ADMIN_SECRET,
        "userId":     user_id,
        "type":       "2",            # admin-impersonation session
        "partnerId":  KALTURA_PARTNER_ID,
        "expiry":     str(_KS_TTL_SECS),
        "privileges": privs,
        "format":     "1",
    }
    resp = httpx.post(KALTURA_SESSION_URL, data=params, timeout=15.0)
    resp.raise_for_status()
    return resp.text.strip().strip('"')


def _get_ks() -> str:
    """
    Return a valid KS for the current user.
    - Mode A (GENIE_KS set): return the static token as-is.
    - Mode B (enterprise): resolve user identity, then return a cached or
      freshly generated token scoped to that user.
    """
    # Mode A — static KS always takes precedence
    if GENIE_KS:
        return GENIE_KS

    # Mode B — enterprise programmatic KS
    if KALTURA_PARTNER_ID and KALTURA_ADMIN_SECRET and GENIE_ID:
        user_id = _get_user_id()   # raises ValueError if not configured
        now = time.time()
        cached = _ks_cache.get(user_id)
        if cached and now < cached["expires_at"]:
            return cached["token"]
        # Generate (or refresh) the token for this user
        token = _generate_ks(user_id)
        _ks_cache[user_id] = {
            "token":      token,
            "expires_at": now + _KS_TTL_SECS - _KS_BUFFER_SECS,
        }
        return token

    raise ValueError(
        "No Kaltura credentials configured. "
        "Set GENIE_KS (Mode A) or "
        "KALTURA_PARTNER_ID + KALTURA_ADMIN_SECRET + GENIE_ID (Mode B)."
    )


# ── NDJSON parser ────────────────────────────────────────────────────────────
def parse_ndjson(raw: str) -> dict:
    """
    Parse the NDJSON stream from Genie into a structured result.

    Flashcard mode returns:
        {
          "flashcards": [
            {
              "title":       str,
              "content":     str,
              "video_clips": [ {"entry_id": str, "start_time": int, "end_time": int} ]
            },
            ...
          ],
          "sources":    [ {entry_id, title, duration, type, ...} ],
          "thread_id":  str,
          "message_id": str,
        }

    Text mode returns the same shape but with an "answer" str instead of flashcards.
    """
    # Accumulate streamed content by segmentNumber so fragments reassemble correctly
    segments = {}   # segmentNumber -> {"runtime": str, "content": str}

    text_parts   = []
    sources      = []
    thread_id    = None
    message_id   = None

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type", "")
        content  = obj.get("content", "") or ""
        seg_num  = obj.get("segmentNumber")

        if not thread_id and obj.get("threadId"):
            thread_id = obj["threadId"]
        if not message_id and obj.get("messageId"):
            message_id = obj["messageId"]

        # Plain text answer (text mode)
        if msg_type == "text":
            text_parts.append(content)

        # Widget segments — accumulate by segmentNumber
        elif msg_type == "unisphere-tool":
            runtime = obj.get("metadata", {}).get("runtimeName", "")
            if seg_num is not None:
                if seg_num not in segments:
                    segments[seg_num] = {"runtime": runtime, "content": ""}
                segments[seg_num]["content"] += content

        # Inline citation clips (each carries a segmentNumber matching its flashcard)
        elif msg_type == "tool" or msg_type == "tool_response":
            pass  # internal Genie tool calls, not user-facing

    # ── Post-process accumulated segments ────────────────────────────────────
    import yaml

    # Group citation clips by segmentNumber for later joining to flashcards
    citation_by_seg = {}
    flashcard_by_seg = {}

    for seg_num, seg in segments.items():
        runtime = seg["runtime"]
        content = seg["content"].strip()
        if not content:
            continue

        if runtime == "flashcards-tool":
            flashcard_by_seg[seg_num] = content

        elif runtime == "sources-tool":
            try:
                parsed = yaml.safe_load(content)
                if isinstance(parsed, list):
                    sources = parsed
                elif isinstance(parsed, dict):
                    sources = parsed.get("sources", [])
            except Exception:
                eids   = re.findall(r"entry_id:\s*(\S+)", content)
                titles = re.findall(r"title:\s*['\"]?(.*?)['\"]?\s*$", content, re.MULTILINE)
                for i, eid in enumerate(eids):
                    sources.append({
                        "entry_id": eid,
                        "title":    titles[i].strip() if i < len(titles) else "",
                    })

        elif "citation" in runtime.lower() or content.startswith("citation:") or "clips:" in content:
            # Citation block: maps text character ranges to video clips
            try:
                parsed = yaml.safe_load(content)
                cit = parsed.get("citation", parsed) if isinstance(parsed, dict) else {}
                clips = cit.get("clips", [])
                citation_by_seg[seg_num] = [
                    {
                        "entry_id":   c.get("entry_id", ""),
                        "start_time": c.get("start_time"),
                        "end_time":   c.get("end_time"),
                    }
                    for c in clips if c.get("entry_id")
                ]
            except Exception:
                pass

    # ── Build flashcard objects ───────────────────────────────────────────────
    flashcards = []
    for seg_num in sorted(flashcard_by_seg.keys()):
        content = flashcard_by_seg[seg_num]
        clips   = citation_by_seg.get(seg_num, [])

        # Try to parse as YAML list of flashcard objects
        try:
            parsed = yaml.safe_load(content)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            # Top-level overview card (always text-only, no clips)
            overview_title   = parsed.get("title", "")
            overview_content = parsed.get("summary", parsed.get("content", parsed.get("text", "")))
            if overview_title or overview_content:
                flashcards.append({
                    "title":       overview_title,
                    "content":     overview_content,
                    "video_clips": [],
                })

            # Individual key-point cards (clips live under keypoint.citation.clips)
            keypoints = parsed.get("keypoints", parsed.get("flashcards", parsed.get("cards", [])))
            for fc in keypoints:
                if not isinstance(fc, dict):
                    continue
                fc_content = fc.get("summary", fc.get("content", fc.get("text", "")))
                citation   = fc.get("citation", {}) or {}
                fc_clips   = citation.get("clips", fc.get("clips", fc.get("video_clips", [])))
                flashcards.append({
                    "title":       fc.get("title", ""),
                    "content":     fc_content,
                    "video_clips": _normalise_clips(fc_clips),
                })

        elif isinstance(parsed, list):
            for fc in parsed:
                if not isinstance(fc, dict):
                    continue
                fc_content = fc.get("summary", fc.get("content", fc.get("text", "")))
                fc_clips   = fc.get("clips", fc.get("video_clips", clips))
                flashcards.append({
                    "title":       fc.get("title", ""),
                    "content":     fc_content,
                    "video_clips": _normalise_clips(fc_clips),
                })

        else:
            # Raw text — single card
            flashcards.append({
                "title":       "",
                "content":     content,
                "video_clips": _normalise_clips(clips),
            })

    result = {
        "sources":    sources,
        "thread_id":  thread_id,
        "message_id": message_id,
    }

    if flashcards:
        result["flashcards"] = flashcards
    else:
        # Text-mode response (no flashcard segments)
        result["answer"] = "".join(text_parts).strip()

    return result


def _secs_to_mmss(seconds) -> str:
    """Convert integer seconds to M:SS or H:MM:SS string."""
    if seconds is None:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _render_markdown(result: dict) -> str:
    """
    Pre-render a Genie result as markdown so any LLM can pass it through
    without needing to interpret the structure.
    Timestamps are pre-converted to M:SS so no LLM calculation is needed.
    """
    lines = []

    # ── Flashcard mode ───────────────────────────────────────────────────────
    if "flashcards" in result:
        for i, fc in enumerate(result["flashcards"], 1):
            title   = fc.get("title", "")
            content = fc.get("content", "")
            clips   = fc.get("video_clips", [])

            heading = f"**Flashcard {i}**" + (f" — {title}" if title else "")
            lines.append(heading)
            if content:
                lines.append(content)
            for clip in clips:
                eid   = clip.get("entry_id", "")
                start = clip.get("start_time")
                end   = clip.get("end_time")
                if eid and start is not None and end is not None:
                    lines.append(f"- `{eid}`  {_secs_to_mmss(start)} – {_secs_to_mmss(end)}")
            lines.append("")

    # ── Text mode ────────────────────────────────────────────────────────────
    elif "answer" in result:
        lines.append(result["answer"])
        lines.append("")

    # ── Sources ──────────────────────────────────────────────────────────────
    sources = result.get("sources", [])
    if sources:
        lines.append("---")
        lines.append("**Sources**")
        for s in sources:
            title    = s.get("title", "")
            eid      = s.get("entry_id", "")
            duration = s.get("duration")
            dur_str  = ""
            if duration:
                m, s2 = divmod(int(duration), 60)
                h, m  = divmod(m, 60)
                dur_str = f" ({h}:{m:02d}:{s2:02d})" if h else f" ({m}:{s2:02d})"
            lines.append(f"- **{title}** — `{eid}`{dur_str}")
        lines.append("")

    return "\n".join(lines).strip()


def _make_video_url(entry_id: str, ks: str) -> str:
    """
    Build a signed Kaltura playback URL for a given entry.
    Uses the playManifest MP4 endpoint — works in any <video> tag without
    an iframe or player widget, including sandboxed artifact environments.
    """
    pid = KALTURA_PARTNER_ID
    if not pid:
        return ""
    return (f"https://cdnapisec.kaltura.com/p/{pid}/sp/{pid}00/playManifest"
            f"/entryId/{entry_id}/format/url/protocol/https/a.mp4?ks={ks}")


def _render_html(result: dict, ks: str) -> str:
    """
    Render Genie flashcards as a self-contained HTML document using the full
    Kaltura V7 (PlayKit) player loaded as inline JavaScript — no iframe needed.
    The PlayKit library is loaded once from Kaltura's CDN, then each clip gets
    its own player instance seeked to the exact start timestamp.
    Works in sandboxed artifact environments (Claude chat, Cowork) because it
    uses <script> + <div>, not <iframe>.
    """
    pid      = KALTURA_PARTNER_ID
    uiconf   = os.getenv("KALTURA_UICONF_ID", "55937762")  # Genie player uiconf

    css = """<style>
      *{box-sizing:border-box;margin:0;padding:0}
      body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
           background:#f5f5f5;color:#1a1a1a;padding:20px;max-width:740px;margin:0 auto}
      .card{background:#fff;border-radius:10px;padding:16px;margin-bottom:16px;
            box-shadow:0 1px 4px rgba(0,0,0,.08)}
      .card-num{font-size:11px;font-weight:600;text-transform:uppercase;
                letter-spacing:.5px;color:#888;margin-bottom:4px}
      .card-body{font-size:14px;line-height:1.6;color:#333;margin-bottom:12px}
      .clip{margin-bottom:12px}
      .player-wrap{width:100%;border-radius:8px;overflow:hidden;background:#000;
                   aspect-ratio:16/9;position:relative}
      .player-wrap > div{width:100%!important;height:100%!important}
      .ts{display:inline-flex;align-items:center;gap:4px;font-size:12px;
          color:#006EFA;margin-top:6px}
      hr{border:none;border-top:1px solid #eee;margin:20px 0}
      .sources{font-size:13px;color:#555}
      .sources h3{font-size:12px;font-weight:600;text-transform:uppercase;
                  letter-spacing:.5px;color:#999;margin-bottom:8px}
      .sources li{list-style:none;margin-bottom:4px}
      .sources code{font-size:11px;background:#f0f0f0;padding:1px 4px;border-radius:3px}
    </style>"""

    # Collect all clips with player IDs so we can init them in one script block
    cards_html = ""
    player_inits = []   # list of JS init strings
    player_idx   = 0

    if "flashcards" in result:
        for i, fc in enumerate(result["flashcards"], 1):
            title   = fc.get("title", "")
            content = fc.get("content", "")
            clips   = fc.get("video_clips", [])
            heading = f"Flashcard {i}" + (f" — {title}" if title else "")

            clips_html = ""
            for clip in clips:
                eid   = clip.get("entry_id", "")
                start = clip.get("start_time")
                end   = clip.get("end_time")
                if not eid or start is None:
                    continue

                player_id  = f"kplayer_{player_idx}"
                player_idx += 1
                start_mmss = _secs_to_mmss(start)
                end_mmss   = _secs_to_mmss(end) if end is not None else ""
                ts_label   = f"{start_mmss} – {end_mmss}" if end_mmss else start_mmss

                clips_html += f"""
                <div class="clip">
                  <div class="player-wrap">
                    <div id="{player_id}"></div>
                  </div>
                  <div class="ts">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" stroke-width="2.5">
                      <polygon points="5 3 19 12 5 21 5 3"/></svg>
                    {ts_label}
                  </div>
                </div>"""

                # Build a pre-clipped HLS manifest URL — same approach Genie uses.
                # seekFrom/clipTo are in milliseconds in the manifest URL.
                seek_ms = int(start) * 1000
                clip_ms = int(end) * 1000 if end is not None else ""
                clip_param = f"&clipTo={clip_ms}" if clip_ms else ""
                manifest_url = (
                    f"https://cdnapisec.kaltura.com/p/{pid}/sp/{pid}00/playManifest"
                    f"/entryId/{eid}/protocol/https/format/applehttp"
                    f"/ks/{ks}/a.m3u8"
                    f"?seekFrom={seek_ms}{clip_param}"
                )
                poster_url = (
                    f"https://www.kaltura.com/p/{pid}/thumbnail"
                    f"/entry_id/{eid}/vid_sec/{int(start)}/width/768"
                )
                player_inits.append(f"""
                  (function(){{
                    var p = KalturaPlayer.setup({{
                      targetId: "{player_id}",
                      provider: {{ partnerId: {pid}, uiConfId: {uiconf}, ks: "{ks}" }},
                      playback: {{ autoplay: false }}
                    }});
                    p.setMedia({{
                      sources: {{
                        hls: [{{ id: "clip", url: "{manifest_url}", mimetype: "application/x-mpegURL" }}],
                        poster: "{poster_url}"
                      }}
                    }});
                  }})();""")

            cards_html += f"""
            <div class="card">
              <div class="card-num">{heading}</div>
              <div class="card-body">{content}</div>
              {clips_html}
            </div>"""

    elif "answer" in result:
        cards_html = f'<div class="card"><div class="card-body">{result["answer"]}</div></div>'

    sources = result.get("sources", [])
    sources_html = ""
    if sources:
        items = ""
        for s in sources:
            t   = s.get("title", "")
            eid = s.get("entry_id", "")
            dur = s.get("duration")
            dur_str = ""
            if dur:
                mm, ss = divmod(int(dur), 60)
                hh, mm = divmod(mm, 60)
                dur_str = f" ({hh}:{mm:02d}:{ss:02d})" if hh else f" ({mm}:{ss:02d})"
            items += f'<li><strong>{t}</strong> <code>{eid}</code>{dur_str}</li>'
        sources_html = f'<hr><div class="sources"><h3>Sources</h3><ul>{items}</ul></div>'

    init_script = ""
    if player_inits:
        inits = "\n".join(player_inits)
        init_script = f"""
        <script>
          // Wait for PlayKit library to finish loading before initialising players
          window.addEventListener('load', function() {{
            try {{
              {inits}
            }} catch(e) {{
              console.error('Kaltura player init error:', e);
            }}
          }});
        </script>"""

    # Use www.kaltura.com with partner_id in path — matches exactly what Genie uses
    playkit_src = f"https://www.kaltura.com/p/{pid}/embedPlaykitJs/partner_id/{pid}/uiconf_id/{uiconf}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  {css}
  <script src="{playkit_src}"></script>
</head>
<body>
  {cards_html}
  {sources_html}
  {init_script}
</body>
</html>"""


def _normalise_clips(clips) -> list:
    """Normalise clip objects to {entry_id, start_time, end_time}."""
    if not clips:
        return []
    out = []
    for c in clips:
        if isinstance(c, dict) and c.get("entry_id"):
            out.append({
                "entry_id":   c["entry_id"],
                "start_time": c.get("start_time"),
                "end_time":   c.get("end_time"),
            })
    return out


# ── Shared HTTP call ─────────────────────────────────────────────────────────
def _call_genie(
    question:        str,
    thread_id:       str  = "",
    model_type:      str  = "fast",
    text_mode:       bool = False,
    markdown_output: bool = False,
    video_output:    bool = False,
):
    try:
        ks = _get_ks()
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Failed to generate Kaltura Session: {exc}"}

    payload = {
        "sse":              False,
        "userMessage":      question,
        "model_type":       model_type,
        "force_experience": "markdown" if text_mode else "flashcards",
    }
    if thread_id:
        payload["threadId"] = thread_id

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"KS {ks}",
        "Accept":        "*/*",
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(GENIE_URL, json=payload, headers=headers)

        if resp.status_code == 401:
            # Invalidate cached token so the next call regenerates
            if not GENIE_KS:
                try:
                    uid = _get_user_id()
                    _ks_cache.pop(uid, None)
                except Exception:
                    pass
            msg = ("401 Unauthorized — KS token expired. "
                   "Grab a fresh token from your MediaSpace Genie page and update GENIE_KS."
                   if GENIE_KS else
                   "401 Unauthorized — auto-generated KS rejected. "
                   "Check KALTURA_PARTNER_ID / KALTURA_ADMIN_SECRET / GENIE_ID.")
            return {"error": msg}
        if resp.status_code == 403:
            return {"error": "403 Forbidden — KS does not have Genie access."}
        if resp.status_code != 200:
            return {"error": f"Genie returned HTTP {resp.status_code}: {resp.text[:400]}"}

        result = parse_ndjson(resp.text)
        if video_output:
            return _render_html(result, ks)
        return _render_markdown(result) if markdown_output else result

    except httpx.TimeoutException:
        return {"error": "Genie request timed out after 60 seconds."}
    except Exception as exc:
        return {"error": f"Unexpected error: {exc}"}


# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def genie_query(
    question:        str,
    text_mode:       bool = False,
    markdown_output: bool = True,
    video_output:    bool = False,
    model_type:      str  = "fast",
):
    """
    Ask Kaltura Genie a natural-language question about your video content library.

    Genie performs RAG search over your Kaltura media and returns a structured,
    LLM-processed answer with supporting video sources.

    DEFAULT MODE — flashcards (markdown_output=True):
      Returns a pre-rendered markdown string. YOU MUST display this string
      VERBATIM — do NOT rewrite, summarise, paraphrase, add emojis, reformat,
      or omit any part of it. The string already contains all headings, clip
      timestamps, and sources in the correct format. Just output it as-is.

    VIDEO MODE (video_output=True):
      Returns a self-contained HTML document with inline <video> players for
      each clip, seeked to the exact timestamp. Render this as an HTML artifact
      so the user sees playable video inline. Use this mode when the user wants
      to watch the relevant clips directly in the chat.

    TEXT MODE (text_mode=True):
      Returns a plain markdown answer string instead of flashcards.

    STRUCTURED JSON (markdown_output=False):
      Returns a dict with "flashcards" list, "sources" list, and "thread_id".

    CRITICAL: Never reword Genie's text. Never recalculate timestamps.
    Never add emojis or decorations. Display exactly what is returned.

    Args:
        question:        Natural-language question
        text_mode:       False (default, flashcards) or True (plain text answer)
        markdown_output: True (default, pre-rendered markdown) or False (structured JSON)
        video_output:    True to return inline HTML with playable <video> clips
        model_type:      "fast" (default) or "quality"
    """
    return _call_genie(question=question, text_mode=text_mode,
                       markdown_output=markdown_output, video_output=video_output,
                       model_type=model_type)


@mcp.tool()
def genie_followup(
    question:        str,
    thread_id:       str,
    text_mode:       bool = False,
    markdown_output: bool = True,
    video_output:    bool = False,
    model_type:      str  = "fast",
):
    """
    Ask a follow-up question within an existing Genie conversation thread.

    Genie uses the thread context to give more relevant, coherent answers.

    Args:
        question:        Follow-up question
        thread_id:       thread_id returned by a previous genie_query or genie_followup
        text_mode:       False (flashcards, default) or True (plain text)
        markdown_output: True (default, pre-rendered markdown) or False (structured JSON)
        video_output:    True to return inline HTML with playable <video> clips
        model_type:      "fast" (default) or "quality"
    """
    return _call_genie(question=question, thread_id=thread_id,
                       text_mode=text_mode, markdown_output=markdown_output,
                       video_output=video_output, model_type=model_type)


@mcp.tool()
def genie_set_user(user_id: str):
    """
    Set the Kaltura userId for the current user on this machine.

    Enterprise mode only (when KALTURA_PARTNER_ID / ADMIN_SECRET / GENIE_ID are
    configured). This is a one-time setup step — run it once and the server
    remembers your identity across all future sessions.

    Your userId is typically your work email address (the one you use to log
    into Kaltura / your company SSO). It is stored locally in
    ~/.kaltura_genie_user and never leaves your machine except as the userId
    field in Kaltura API session requests.

    Args:
        user_id: Your Kaltura userId, e.g. "jane.doe@example.com"
    """
    if not KALTURA_PARTNER_ID:
        return {
            "status": "skipped",
            "message": "This server is running in static-KS mode (GENIE_KS is set). "
                       "genie_set_user is only needed in enterprise mode.",
        }

    user_id = user_id.strip()
    if not user_id:
        return {"status": "error", "message": "user_id cannot be empty."}

    # Write to the local identity file
    with open(_USER_ID_FILE, "w") as f:
        f.write(user_id)

    # Evict any cached token for the previous user
    _ks_cache.clear()

    return {
        "status":  "ok",
        "message": f"User identity set to '{user_id}'. "
                   f"Kaltura sessions will now be generated on your behalf. "
                   f"You can verify this is working by asking Genie a question.",
    }


if __name__ == "__main__":
    mcp.run()
