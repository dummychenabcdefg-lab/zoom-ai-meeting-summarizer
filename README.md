# Meeting Summarizer — a sample project for Zoom AI Services

> **Naming note:** This project is about **Zoom AI Services** (the Zoom developer
> platform), including **Zoom Scribe** (speech-to-text) and the **Summarizer API**.
> The developer portal lives at [zoom.ai](https://www.zoom.ai/).

## What this does

`meeting_summarizer.py` takes the public URL of a meeting audio file and:

1. Transcribes it with the **Scribe Fast mode** API — synchronous speech-to-text
   with timestamps, punctuation, and speaker diarization.
2. Prints the transcript with speaker labels and timestamps.
3. Optionally (`--summarize`) sends the transcript to the **Summarizer Fast mode**
   API and prints a recap + detailed summary + action items (`task=full_summary`).

Example output:

```
$ python meeting_summarizer.py --audio-url "https://example.com/standup.mp3" --summarize

===== TRANSCRIPT =====

[     0.5s] speaker_1: Let's finalize the timeline.
[     4.2s] speaker_2: We need one more week for validation.

===== SUMMARY =====

Recap:

The team agreed to delay the launch by one week.

Summary:

...
```

## Setup

Requires **Python 3.10+**.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Get an API key (free trial available; a no-key playground exists too)
#    Sign up at: https://www.zoom.ai/

# 3. Export your key
export ZOOM_API_KEY="your_api_key_here"

# 4. Run it
python meeting_summarizer.py --audio-url "https://example.com/meeting.mp3"
```

The key is read only from the `ZOOM_API_KEY` environment variable — it never
appears in the code.

## Usage

```bash
python meeting_summarizer.py --help
python meeting_summarizer.py --audio-url "https://example.com/meeting.mp3"
python meeting_summarizer.py --audio-url "https://example.com/meeting.mp3" --language es-ES
python meeting_summarizer.py --audio-url "https://example.com/meeting.mp3" --summarize
```

- `--audio-url` (required): publicly reachable audio file — WAV, MP3, M4A, or MP4.
- `--language`: BCP-47 locale for transcription and summary output (default `en-US`).
- `--summarize`: also call the Summarizer API on the transcript.

Common errors are handled with plain-language messages:

- **401** — API key invalid or expired; get a new one at https://www.zoom.ai/
- **415/422** — bad input; check the audio URL is reachable and in a supported format
- **429** — rate limited; wait and retry

## APIs used (documented endpoints)

| Step        | Method | Endpoint                                              |
|-------------|--------|-------------------------------------------------------|
| Transcribe  | POST   | `https://api.zoom.us/v2/aiservices/scribe/transcribe` |
| Summarize   | POST   | `https://api.zoom.us/v2/aiservices/summarizer/summarize` |

Authentication: `x-api-key: <API_KEY>` header (the single auth scheme per the portal docs).

Official docs:

- https://developers.zoom.us/docs/ai-services/
- https://developers.zoom.us/docs/ai-services/scribe/
- https://developers.zoom.us/docs/ai-services/summarizer/fast-mode/

Pricing:

- https://zoom.us/pricing/developer

## Verification notes

Shapes below were verified against the official AI Services Developer Portal docs on 2026-10-05 (signed in), and the Scribe flow was run live in the portal playground (3-minute sample audio, diarization + text polishing on — 42 speaker-labeled segments returned).

- ✅ **Scribe Fast mode:** `POST https://api.zoom.us/v2/aiservices/scribe/transcribe` takes the **raw audio bytes** as the request body (`Content-Type: audio/wav` etc.) with options as query parameters (`model`, `language`, `diarization`, `text_polishing`, …). Response: `{request_id, duration_sec, result: {text_display, segments: [{id, start, end, channel, speaker, text, words}]}, model, usage}`. 5-minute file limit; 9 locales.
- ✅ **Summarizer Fast mode:** `POST https://api.zoom.us/v2/aiservices/summarizer/summarize` with flat JSON body `{summary_type: "CONVERSATION", task: "recap"|"summary"|"action_items"|"full_summary", transcript: "<JSON string of [{speaker, text}]>", language}` (max 96 KB input). Response: `{request_id, task, result: {text}}`.
- ✅ **Auth:** single `x-api-key` header. Keys are issued in the portal at https://www.zoom.ai/ (shown once — regenerate if lost).
- ⚠️ **Not exercised end-to-end from this script:** no live API call was made from this environment (the key never leaves the owner's portal), so run it yourself with `export ZOOM_API_KEY=...` to see it live. Syntax is verified via `python -m py_compile`.

## Bonus: `crm_pipeline_demo.py` — every call writes its own CRM entry

A second demo in this repo turns a sales call recording into a structured
CRM record using the **Speech Summarization API** — one streaming request
that transcribes the audio *and* produces the AI summary:

`POST https://api.zoom.us/v2/aiservices/speech-summarization/summarize`
(multipart/form-data: audio file + JSON config; Server-Sent Events
response: `transcribe_started` → `transcribe_complete` →
`summary_delta` → `done`).
Docs: https://developers.zoom.us/docs/api/ai-services/#tag/summarization/post/aiservices/speech-summarization/summarize

The script parses action items from the summary (best-effort owner
detection), builds a CRM-ready JSON record — contact, call date,
transcript, summary, action items with owners, next steps — and writes it
to `crm_record.json`.

```bash
export ZOOM_API_KEY="your_api_key_here"   # needs scope aiservices:write:transcribe-summarize:admin
python crm_pipeline_demo.py --audio-url "https://example.com/call.wav" \
    --contact "Jane Doe, Acme Corp"
python crm_pipeline_demo.py --audio-url "https://example.com/call.wav" \
    --contact "Jane Doe, Acme Corp" --out acme_call.json
```

Auth: the endpoint docs say `Authorization: Bearer <token>`; the script
retries with the portal's `x-api-key` header on 401 since both schemes work
on the sibling Scribe/Summarizer endpoints. Accepted files: WAV, MP3, M4A,
FLAC, OGG, WebM audio and MP4/MOV video (max 50 MB, max 2 hours).

Point the record writer at a real CRM API (e.g. HubSpot) to push the entry
for real — the JSON shape is ready to map onto a note/engagement object.

**Tested end-to-end Oct 19 2026** against the live API with a 50-second
synthetic sales call (`test_call.mp3`, two speakers): transcript, summary
(Key Outcomes + Action Items), owner parsing, and the CRM JSON all came
through. Two real-world notes baked into the code: the summary events are
named `summary_updated` (not `summary_delta` as the API reference says)
with the text in `summary_result`, and normal events carry `"error": null`,
so only a truthy error is treated as a failure.
