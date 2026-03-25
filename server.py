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

Setup:
  1. pip install mcp httpx pyyaml
  2. Set env var GENIE_KS to a Kaltura Session token from your MediaSpace
  3. Add to claude_desktop_config.json (see install.sh)

Auth:  Authorization: KS <token>
URL:   https://genie.nvp1.ovp.kaltura.com/assistant/converse
"""

import os
import json
import re
import httpx
from mcp.server.fastmcp import FastMCP

# ── Config ──────────────────────────────────────────────────────────────────
GENIE_URL = os.getenv("GENIE_URL", "https://genie.nvp1.ovp.kaltura.com/assistant/converse")
GENIE_KS  = os.getenv("GENIE_KS", "")

mcp = FastMCP("kaltura-genie")


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
):
    ks = GENIE_KS
    if not ks:
        return {"error": "GENIE_KS is not set. Add it to the MCP server env config."}

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
            return {"error": "401 Unauthorized — KS token expired. Grab a fresh one from your MediaSpace Genie page and update GENIE_KS in the config."}
        if resp.status_code == 403:
            return {"error": "403 Forbidden — KS does not have Genie access."}
        if resp.status_code != 200:
            return {"error": f"Genie returned HTTP {resp.status_code}: {resp.text[:400]}"}

        result = parse_ndjson(resp.text)
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

    TEXT MODE (text_mode=True):
      Returns a plain markdown answer string instead of flashcards.

    STRUCTURED JSON (markdown_output=False):
      Returns a dict with "flashcards" list, "sources" list, and "thread_id".
      Each flashcard has: title (str), content (str), video_clips (list of
      {entry_id, start_time, end_time} — timestamps are pre-converted to M:SS).

    CRITICAL: Never reword Genie's text. Never recalculate timestamps.
    Never add emojis or decorations. Display exactly what is returned.

    Args:
        question:        Natural-language question
        text_mode:       False (default, flashcards) or True (plain text answer)
        markdown_output: True (default, pre-rendered markdown) or False (structured JSON)
        model_type:      "fast" (default) or "quality"
    """
    return _call_genie(question=question, text_mode=text_mode,
                       markdown_output=markdown_output, model_type=model_type)


@mcp.tool()
def genie_followup(
    question:        str,
    thread_id:       str,
    text_mode:       bool = False,
    markdown_output: bool = True,
    model_type:      str  = "fast",
):
    """
    Ask a follow-up question within an existing Genie conversation thread.

    Genie uses the thread context to give more relevant, coherent answers.

    Args:
        question:        Follow-up question
        thread_id:       thread_id returned by a previous genie_query or genie_followup
        text_mode:       False (flashcards, default) or True (plain text)
        markdown_output: False (default, structured JSON) or True (pre-rendered markdown)
        model_type:      "fast" (default) or "quality"
    """
    return _call_genie(question=question, thread_id=thread_id,
                       text_mode=text_mode, markdown_output=markdown_output,
                       model_type=model_type)


if __name__ == "__main__":
    mcp.run()
