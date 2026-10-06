# Meeting Summarizer — Zoom AI Services demo

📄 **Companion tutorial:** [Add AI meeting summaries to any app with Zoom AI Services in 15 minutes](https://dev.to/dummy_chen_4cfe24c5fe88fc/add-ai-meeting-summaries-to-any-app-with-zoom-ai-services-in-15-minutes-6ke) — walks through every API call in this repo, step by step.

Audio file → diarized transcript → meeting summary, in ~60 lines of Python.
Two REST calls, no SDK:


1. **Scribe** (`POST /v2/aiservices/scribe/transcribe`) — raw audio bytes in, transcript with speakers + timestamps out.
2. **Summarizer** (`POST /v2/aiservices/summarizer/summarize`) — speaker-labeled transcript in, recap + summary + action items out.


Both are part of **Zoom AI Services** (developer portal: [zoom.ai](https://www.zoom.ai/)).


## Quickstart


```bash
pip install -r requirements.txt
export ZOOM_API_KEY="your_api_key_here"   # server-side only — never ship in client code
python meeting_summarizer.py meeting.wav --summarize
```


Get a key in the [AI Services Developer Portal](https://www.zoom.ai/) (API keys section; Scribe + Summarizer scopes). The key is shown once — store it server-side.


Options:


```bash
python meeting_summarizer.py meeting.wav                 # transcript only
python meeting_summarizer.py meeting.wav --summarize     # transcript + full summary
python meeting_summarizer.py meeting.wav --summarize --task action_items
```


Summarizer tasks: `recap`, `summary`, `action_items`, `full_summary`.


## Notes


- Scribe **Fast mode** handles files up to 5 minutes; longer recordings go through the Batch jobs API.
- Summarizer Fast mode accepts one inline transcript (up to 96 KB).
- Auth is a single `x-api-key` header on every call (per the portal docs).
- Pricing is usage-based via prepaid credits — check [zoom.us/pricing/developer](https://zoom.us/pricing/developer) for current rates.
- Reference implementations: [ai-services-quickstart](https://github.com/zoom/ai-services-quickstart), [scribe-quickstart](https://github.com/zoom/scribe-quickstart).
