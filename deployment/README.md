# Deployment scaffold

This directory separates the public guide from the service that calls the model. Browser code receives an API base URL. The Ollama key stays in Wix Secrets Manager or in the environment of an external backend. The maintained Wix implementation subset now lives at [`../wix-app/`](../wix-app/); the files under `deployment/wix/` preserve the earlier portability examples and roadmap.

The shared backend accepts `POST /api/warmup` from approved origins without user content or a credential. With CAIL configured, this refreshes calendar evidence and reports provider configuration without requesting a generation. Legacy Ollama-only configurations retain their empty preload behavior.

## Paths

- `wix/ROADMAP.md` describes a private Wix app that installs the guide across Fortune's site.
- `wix/embedded.html.example` is the small fragment an embedded-script extension adds at the end of each page.
- `wix/fortune-guide-element.example.js` is a retirement marker for the old portable example. Copy the maintained monochrome element from `../wix-app/site/fortune-guide-element.js` instead.
- `wix/backend/ollama-proxy.example.mjs` is a thin Wix relay to the canonical Website Guide API. It does not call a second model or author participant responses.
- `wix/copilot-studio-bridge/` is a deployable, sandboxed iframe and server-side Direct Line token broker for a public-information Copilot Studio pilot. It is a separate evaluation route, not an adapter for the shared source-bounded API contract below.
- `github-pages/ROADMAP.md` describes a public static demonstration backed by the same external API.
- `github-pages/config.example.js` contains public runtime settings only.
- `TRANSCRIPT-INGESTION.md` defines how a private project transcript can inform behavior and tests without becoming a public answer source.

## Shared API contract

The Wix and GitHub Pages clients send the same request shape. History persists in tab-scoped session storage and is capped at eight exchanges (sixteen messages):

```json
{
  "message": "I need help with a computer",
  "client_surface": "wix",
  "client_event_id": "b92181da-1552-4975-bbd8-9ac98d553ab5",
  "conversation_id": "d6b917ca-a830-4be7-a184-05cfdb683741",
  "conversation_token": "server-issued continuation token",
  "history": [{ "role": "user", "content": "I need help" }],
  "page_context": {
    "url": "https://www.fortunedigitalequity.org/trainings",
    "path": "/trainings",
    "title": "Trainings"
  }
}
```

The server returns this response shape:

```json
{
  "kind": "answer",
  "message": "Source-bounded response",
  "reason": "Short explanation of the route",
  "sources": [{ "id": "approved-page", "title": "Approved page", "url": "https://www.fortunedigitalequity.org/..." }],
  "related": [{ "title": "Next section", "url": "https://www.fortunedigitalequity.org/..." }],
  "choices": [],
  "handoff_url": "https://www.fortunedigitalequity.org/contact",
  "model": "z-ai/glm-5.3-flash",
  "model_provider": "cail",
  "model_generations": 1,
  "model_called": true,
  "conversation_id": "d6b917ca-a830-4be7-a184-05cfdb683741",
  "turn_id": "0a9f33fb-6068-4577-8a4d-96ad2d93ee13",
  "client_event_id": "b92181da-1552-4975-bbd8-9ac98d553ab5",
  "message_ids": {
    "user": "5d5bde3f-f732-4405-ad94-84d8b1656fb3",
    "assistant": "5e115191-3f06-4a62-aea9-c5b9b41e5409"
  },
  "conversation_token": "server-issued continuation token",
  "capture": { "mode": "none", "stored": false },
  "continuation": { "label": "Ask the live guide", "available": true }
}
```

The first request omits `conversation_id` and `conversation_token`. The server issues both; subsequent turns return them unchanged. A client retains one random `client_event_id` until it receives a definitive result, making network retries idempotent. Every successful non-private new request returns `model_called: true`. An ambiguous request returns a model-authored `kind: "clarify"` response with one short question in `message` and, when useful, validated `{ "label", "prompt" }` choices. Factual answers include an approved source; a clarification can omit sources. The pre-model privacy hold is the sole successful zero-call exception. Provider, quota, or invalid-output failures return an error and never become a fabricated Guide turn.

Conversation capture, Railway staging, retention, and the evaluator-dashboard sequence are defined in [CONVERSATION-CAPTURE.md](CONVERSATION-CAPTURE.md).
