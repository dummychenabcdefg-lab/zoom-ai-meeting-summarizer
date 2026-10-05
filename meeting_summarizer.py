#!/usr/bin/env python3
"""Audio file -> diarized transcript -> meeting summary, via Zoom AI Services.

Flow: Scribe (Fast) transcribes audio -> Summarizer (Fast) produces recap +
summary + action items. ~60 lines, stdlib + requests only.

Usage:
    export ZOOM_API_KEY=<your key>        # server-side only; never ship in client code
    python meeting_summarizer.py meeting.wav [--summarize] [--task full_summary]

Prerequisites: pip install -r requirements.txt
Docs: https://developers.zoom.us/docs/ai-services/
"""
import argparse
import json
import os
import sys

import requests

API_BASE = "https://api.zoom.us/v2/aiservices"


def get_api_key() -> str:
    key = os.environ.get("ZOOM_API_KEY", "").strip()
    if not key:
        sys.exit("Error: set ZOOM_API_KEY in your environment (server-side only).")
    return key


def check_response(response: requests.Response, label: str) -> dict:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = f" — {response.json()}"
        except ValueError:
            if response.text:
                detail = f" — {response.text[:300]}"
        sys.exit(f"Error: {label} request failed ({exc}){detail}")
    return response.json()


def transcribe(audio_path: str, api_key: str, language: str = "en-US") -> dict:
    """Scribe Fast mode: POST raw audio bytes, get diarized transcript back."""
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    print(f"Transcribing {audio_path} ({len(audio_bytes)} bytes)...", flush=True)
    response = requests.post(
        f"{API_BASE}/scribe/transcribe",
        headers={"x-api-key": api_key, "Content-Type": "audio/wav"},
        params={
            "model": "zoom-scribe-en",
            "language": language,
            "diarization": "true",
            "text_polishing": "true",
        },
        data=audio_bytes,
        timeout=300,
    )
    data = check_response(response, "Scribe")
    result = data.get("result") or {}
    segments = result.get("segments") or []
    print(
        f"Done: {data.get('duration_sec', '?')}s audio -> "
        f"{len(segments)} diarized segments.",
        flush=True,
    )
    return result


def segments_to_transcript(result: dict) -> str:
    """Flatten Scribe segments into 'Speaker: text' lines for the Summarizer."""
    lines = [
        f"{seg.get('speaker') or 'Speaker'}: {seg.get('text', '').strip()}"
        for seg in result.get("segments") or []
    ]
    text = "\n".join(lines)
    return text or result.get("text_display", "")


def summarize(transcript: str, api_key: str, task: str = "full_summary") -> str:
    """Summarizer Fast mode: speaker-labeled transcript -> recap/summary/actions."""
    turns = [
        {"speaker": speaker.strip(), "text": text.strip()}
        for speaker, _, text in (line.partition(":") for line in transcript.splitlines())
        if text.strip()
    ]
    if not turns:
        sys.exit("Error: no transcript turns to summarize.")
    print(f"Summarizing {len(turns)} turns (task={task})...", flush=True)
    response = requests.post(
        f"{API_BASE}/summarizer/summarize",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "summary_type": "CONVERSATION",
            "task": task,
            "transcript": json.dumps(turns),
            "language": "en-US",
        },
        timeout=120,
    )
    data = check_response(response, "Summarizer")
    result = data.get("result") or {}
    text = result.get("text") or data.get("text") or data.get("summary") or ""
    if not text:
        sys.exit("Error: summarizer returned no text.")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe a meeting recording and summarize it "
        "with Zoom AI Services (Scribe + Summarizer)."
    )
    parser.add_argument("audio", help="Path to a .wav file (<= 5 min for Fast mode)")
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Also run the Summarizer on the transcript",
    )
    parser.add_argument(
        "--task",
        default="full_summary",
        choices=["recap", "summary", "action_items", "full_summary"],
        help="Summarizer task (default: full_summary)",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    result = transcribe(args.audio, api_key)
    transcript = segments_to_transcript(result)

    print("\n----- TRANSCRIPT -----\n")
    print(transcript)

    if args.summarize:
        print("\n----- SUMMARY -----\n")
        print(summarize(transcript, api_key, task=args.task))


if __name__ == "__main__":
    main()
