#!/usr/bin/env python3
"""speech_summarizer_console.py - interactive console demo for the
Zoom AI Services Speech Summarization API.

Run it, paste your API key, point it at an .mp3 or .mp4 file, and it
prints the transcript and AI summary. One streaming API call does the
transcription and summarization together:

  POST https://api.zoom.us/v2/aiservices/speech-summarization/summarize
  (multipart/form-data: audio file + JSON config; Server-Sent Events
  response: transcribe_started -> transcribe_complete ->
  summary_updated (xN, token-by-token) -> done)

Required API key scope (per the endpoint's documented granular scopes):
`aiservices:write:transcribe-summarize:admin`. Create a key at
https://www.zoom.ai/.

Usage:
    python speech_summarizer_console.py

The key is asked for with hidden input (getpass) and is never echoed,
saved, or printed. Pressing Enter at the key prompt falls back to the
ZOOM_API_KEY environment variable if one is set.
"""

import getpass
import json
import os
import sys

import requests

SPEECH_SUMMARIZATION_URL = (
    "https://api.zoom.us/v2/aiservices/speech-summarization/summarize"
)
REQUEST_TIMEOUT = 900  # seconds; files up to 2h / 50 MB are allowed
MAX_FILE_BYTES = 50 * 1024 * 1024

_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
}


def ask_api_key() -> str:
    """Prompt for the API key with hidden input; never echo or store it."""
    key = getpass.getpass(
        "Enter your Zoom AI Services API key "
        "(or press Enter to use $ZOOM_API_KEY): "
    ).strip()
    if not key:
        key = os.environ.get("ZOOM_API_KEY", "").strip()
    if not key:
        sys.exit(
            "Error: no API key provided. Get one at https://www.zoom.ai/ "
            "(scope: aiservices:write:transcribe-summarize:admin)."
        )
    return key


def ask_audio_file() -> tuple[str, bytes, str]:
    """Prompt for an .mp3/.mp4 path; return (filename, bytes, content_type)."""
    path = input("Enter the path to an .mp3 or .mp4 file: ").strip().strip('"')
    if not path:
        sys.exit("Error: no file path provided.")
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        sys.exit(f"Error: file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    content_type = _CONTENT_TYPES.get(ext)
    if content_type is None:
        sys.exit(
            f"Error: unsupported file type '{ext}'. "
            "This demo accepts .mp3 and .mp4 files."
        )
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        sys.exit(f"Error: {path} is empty.")
    if len(data) > MAX_FILE_BYTES:
        sys.exit("Error: file exceeds the 50 MB API limit.")
    return os.path.basename(path), data, content_type


def check_response(response: requests.Response) -> None:
    """Exit with a friendly message on HTTP errors (200 = stream on)."""
    if response.status_code == 200:
        return
    if response.status_code == 401:
        sys.exit(
            "Error 401 (Unauthorized): the API key was rejected.\n"
            "Check that the key is correct, still active, and has the "
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
    sys.exit(f"Error {response.status_code} calling the API.{detail}")


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


def summarize(
    api_key: str, filename: str, audio_bytes: bytes, content_type: str
) -> tuple[str, str]:
    """One streaming call: audio -> (transcript_text, summary_text).

    Auth: the endpoint docs say `Authorization: Bearer <token>`; the
    portal's `x-api-key` header is tried as a fallback on 401 since both
    schemes work on sibling APIs.
    """
    config = {
        "transcribe": {
            "language": "en-US",
            "model": "zoom-scribe-en",
            "diarization": True,
            "output_format": "json",
        },
        "summary": {
            "language": "en-US",
            "summary_length": "long",
        },
    }
    files = {
        "file": (filename, audio_bytes, content_type),
        "config": (None, json.dumps(config), "application/json"),
    }

    def post(headers: dict) -> requests.Response:
        try:
            return requests.post(
                SPEECH_SUMMARIZATION_URL,
                headers=headers,
                files=files,
                stream=True,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            sys.exit("Error: request timed out. Try a shorter file.")
        except requests.exceptions.RequestException as exc:
            sys.exit(f"Error: network problem calling the API: {exc}")

    print(f"\nUploading {filename} ({len(audio_bytes)} bytes)...")
    response = post({"Authorization": f"Bearer {api_key}"})
    if response.status_code == 401:
        response.close()
        print("Bearer auth rejected (401); retrying with x-api-key header...")
        response = post({"x-api-key": api_key})
    check_response(response)

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
                if current_event == "transcribe_started":
                    print("Transcribing...")
                elif current_event == "transcribe_complete":
                    print("Transcription done. Summarizing...")
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


def main() -> None:
    print("Zoom AI Services - Speech Summarization console demo")
    print("====================================================")
    api_key = ask_api_key()
    filename, audio_bytes, content_type = ask_audio_file()

    transcript_text, summary_text = summarize(
        api_key, filename, audio_bytes, content_type
    )

    if transcript_text.strip():
        print("\n===== TRANSCRIPT =====\n")
        print(transcript_text)
    print("\n===== SUMMARY =====\n")
    print(summary_text)


if __name__ == "__main__":
    main()
