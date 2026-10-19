#!/usr/bin/env python3
"""crm_pipeline_demo.py - turn a sales call recording into a CRM record.

Demo of the "every call writes its own CRM entry" pipeline built on
Zoom AI Services (https://www.zoom.ai/) using the Speech Summarization
API: ONE streaming request that transcribes the audio AND produces an
AI-powered summary.

  POST https://api.zoom.us/v2/aiservices/speech-summarization/summarize
  (multipart/form-data: audio file + JSON config; the response is a
  Server-Sent Events stream: transcribe_started -> transcribe_complete
  -> summary_updated (xN, token-by-token) -> done)
  https://developers.zoom.us/docs/api/ai-services/#tag/summarization/post/aiservices/speech-summarization/summarize

The script then parses action items from the summary and writes a
structured CRM record (contact, summary, action items with owners,
next steps) to a JSON file. Point the writer at a real CRM API
(e.g. HubSpot) to push the entry for real.

Required API key scope (per the endpoint's documented granular scopes):
`aiservices:write:transcribe-summarize:admin`. Create the key at
https://www.zoom.ai/.

Usage:
    export ZOOM_API_KEY="your_api_key_here"
    python crm_pipeline_demo.py --audio-url "https://example.com/call.wav" \
        --contact "Jane Doe, Acme Corp"

Output: a JSON CRM record printed to stdout and written to
`crm_record.json` (override with --out).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

import requests

SPEECH_SUMMARIZATION_URL = (
    "https://api.zoom.us/v2/aiservices/speech-summarization/summarize"
)
REQUEST_TIMEOUT = 900  # seconds; files up to 2h / 50 MB are allowed

_CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mov": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


def get_api_key() -> str:
    """Read the API key from the environment. Never hardcode secrets."""
    key = os.environ.get("ZOOM_API_KEY", "").strip()
    if not key:
        sys.exit(
            "Error: ZOOM_API_KEY environment variable is not set.\n"
            "Create a key (scope: aiservices:write:transcribe-summarize:admin) "
            "at https://www.zoom.ai/ and run:\n"
            '  export ZOOM_API_KEY="your_api_key_here"'
        )
    return key


def check_response(response: requests.Response, api_name: str) -> None:
    """Exit with a friendly message on HTTP errors (200 = stream on)."""
    if response.status_code == 200:
        return
    if response.status_code == 401:
        sys.exit(
            "Error 401 (Unauthorized): the API key was rejected.\n"
            "Check that ZOOM_API_KEY is correct, still active, and has the "
            "aiservices:write:transcribe-summarize:admin scope."
        )
    if response.status_code == 413:
        sys.exit("Error 413: the file exceeds the 50 MB limit.")
    if response.status_code == 429:
        sys.exit("Error 429 (Rate limited): wait a bit and retry.")
    detail = ""
    try:
        detail = f" Response: {response.json()}"
    except ValueError:
        if response.text:
            detail = f" Response body: {response.text[:500]}"
    sys.exit(f"Error {response.status_code} calling {api_name}.{detail}")


def download_audio(audio_url: str) -> tuple[bytes, str, str]:
    """Fetch the audio file into memory; return (bytes, filename, content-type)."""
    try:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        sys.exit(f"Error: could not download audio from {audio_url}: {exc}")
    data = resp.content
    if not data:
        sys.exit(f"Error: downloaded zero bytes from {audio_url}.")
    if len(data) > 50 * 1024 * 1024:
        sys.exit("Error: file exceeds the 50 MB API limit.")
    filename = os.path.basename(audio_url.split("?")[0]) or "call.wav"
    ext = os.path.splitext(filename)[1].lower()
    return data, filename, _CONTENT_TYPES.get(ext, "audio/wav")


def summarize_call(
    api_key: str, audio_url: str, language: str
) -> tuple[str, str]:
    """One streaming call: audio -> transcript + summary.

    Returns (transcript_text, summary_text). Auth: the endpoint docs say
    `Authorization: Bearer <token>`; the portal's `x-api-key` header is
    tried as a fallback on 401 since both schemes work on sibling APIs.
    """
    audio_bytes, filename, content_type = download_audio(audio_url)
    config = {
        "transcribe": {
            "language": language,
            "model": "zoom-scribe-en",
            "diarization": True,
            "output_format": "json",
        },
        "summary": {
            "language": language,
            "summary_length": "long",
        },
    }
    files = {
        "file": (filename, audio_bytes, content_type),
        "config": (None, json.dumps(config), "application/json"),
    }

    def post(headers: dict) -> requests.Response:
        print(
            f"Sending {len(audio_bytes)} bytes ({content_type}) to the "
            f"Speech Summarization API..."
        )
        try:
            return requests.post(
                SPEECH_SUMMARIZATION_URL,
                headers=headers,
                files=files,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            sys.exit("Error: request timed out. Try a shorter audio file.")
        except requests.exceptions.RequestException as exc:
            sys.exit(f"Error: network problem calling the API: {exc}")

    response = post({"Authorization": f"Bearer {api_key}"})
    if response.status_code == 401:
        # Fallback: the portal's x-api-key scheme works on sibling endpoints.
        response.close()
        print("Bearer auth rejected (401); retrying with x-api-key header...")
        response = post({"x-api-key": api_key})
    check_response(response, "Speech Summarization")

    transcript_text = ""
    summary_text = ""
    current_event = ""
    try:
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line[len("data:"):].strip())
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                # NOTE: normal events carry "error": null - only a truthy
                # value (or an event literally named "error") is a failure.
                err = payload.get("error")
                if err or current_event == "error":
                    stage = payload.get("stage", "unknown")
                    sys.exit(
                        f"Error: API stream reported an error at stage "
                        f"'{stage}': {err}"
                    )
                if current_event == "transcribe_complete":
                    transcript_text = extract_transcript_text(
                        payload.get("transcript")
                    )
                elif current_event == "summary_updated":
                    # Each event carries the complete accumulated summary.
                    if isinstance(payload.get("summary_result"), str):
                        summary_text = payload["summary_result"]
    finally:
        response.close()

    if not summary_text.strip():
        sys.exit("Error: the stream ended without a summary.")
    return transcript_text, summary_text


def extract_transcript_text(transcript) -> str:
    """Best-effort plain text out of the transcribe_complete payload."""
    if isinstance(transcript, str):
        return transcript
    if not isinstance(transcript, dict):
        return ""
    # Real shape nests the result: {"transcript": {"request_id": ...,
    # "duration_sec": ..., "result": {"text_display": ..., "segments": [...]}}}
    if isinstance(transcript.get("result"), dict):
        return extract_transcript_text(transcript["result"])
    for key in ("text", "text_display", "transcript"):
        value = transcript.get(key)
        if isinstance(value, str) and value.strip():
            return value
    segments = transcript.get("segments")
    if isinstance(segments, list):
        lines = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = (seg.get("text") or "").strip()
            if text:
                lines.append(f"{seg.get('speaker') or 'Speaker'}: {text}")
        return "\n".join(lines)
    return ""


def parse_action_items(summary: str) -> list[dict]:
    """Best-effort parse of the Action Items section into owner/text pairs.

    Handles the real API format (owner sub-headers with checkbox tasks)::
        ## Action Items
        - **Speaker 0**
          - [ ] Send the revised proposal by Thursday.[^1]
        - **Tom**
          - [ ] Schedule the onboarding call.[^1]
    as well as the flat "- Jane: send the proposal" style. Citation
    markers like [^1] are stripped. Unparsed lines are kept verbatim so
    nothing is silently dropped.
    """
    items: list[dict] = []
    in_section = False
    current_owner = None
    for line in summary.splitlines():
        stripped = line.strip()
        if re.match(r"(?i)^#{0,6}\s*action items\s*:?\s*$", stripped):
            in_section = True
            current_owner = None
            continue
        if in_section and (
            re.match(r"(?i)^#{1,6}\s+\S", stripped)
            or re.match(
                r"(?i)^(recap|summary|key points|decisions|next steps)\s*:?\s*$",
                stripped,
            )
        ):
            break  # next section
        if not in_section:
            continue
        # Owner sub-header: "- **Name**" (no checkbox on the line).
        owner_match = re.match(r"^[-*•]\s*\*\*(.+?)\*\*\s*$", stripped)
        if owner_match and "[ ]" not in stripped:
            current_owner = owner_match.group(1).strip()
            continue
        # Task line: "- [ ] text" or "- text", possibly indented.
        task_match = re.match(r"^[-*•]\s*(?:\[[ xX]\]\s*)?(.+)$", stripped)
        if task_match:
            text = re.sub(r"\[\^\d+\]", "", task_match.group(1)).strip()
            owner = current_owner
            if not owner:
                flat = re.match(r"^([^:]{1,40}):\s*(.+)$", text)
                if flat:
                    owner, text = flat.group(1).strip(), flat.group(2).strip()
            if text:
                items.append({"owner": owner, "text": text})
    return items


def build_crm_record(
    contact: str,
    audio_url: str,
    transcript_text: str,
    summary: str,
    action_items: list[dict],
) -> dict:
    """Assemble the CRM-ready record from the pipeline outputs."""
    return {
        "contact": contact,
        "call_date": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_audio": audio_url,
        "transcript": transcript_text,
        "summary": summary.strip(),
        "action_items": action_items,
        "next_steps": [i["text"] for i in action_items],
        "source": "zoom-ai-services:speech-summarization",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Turn a sales call recording into a CRM record with Zoom AI "
            "Services: one streaming call to the Speech Summarization API "
            "transcribes the audio and produces a summary, then the script "
            "writes a structured JSON CRM entry."
        )
    )
    parser.add_argument(
        "--audio-url",
        required=True,
        help="Public URL of the call recording. Audio: WAV/MP3/M4A/FLAC/OGG/"
        "WebM; video: MP4/MOV. Max 50 MB, max 2 hours.",
    )
    parser.add_argument(
        "--contact",
        required=True,
        help='Who the call was with, e.g. "Jane Doe, Acme Corp".',
    )
    parser.add_argument(
        "--language",
        default="en-US",
        help="BCP-47 locale code for transcription and summary. "
        "Default: en-US.",
    )
    parser.add_argument(
        "--out",
        default="crm_record.json",
        help="Where to write the CRM record JSON. Default: crm_record.json.",
    )
    args = parser.parse_args()

    api_key = get_api_key()

    transcript_text, summary = summarize_call(api_key, args.audio_url, args.language)
    print(f"  Transcript received ({len(transcript_text)} chars).")
    print(f"  Summary received ({len(summary)} chars).")

    action_items = parse_action_items(summary)
    print(f"  Action items parsed: {len(action_items)}.")

    print("Building the CRM record...")
    record = build_crm_record(
        args.contact, args.audio_url, transcript_text, summary, action_items
    )

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    print(f"\nCRM record written to {args.out}\n")
    print(json.dumps(record, indent=2, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
