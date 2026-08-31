# Wix adoption roadmap

## Recommended extension

Use a private Wix app with an embedded-script extension inserted at `BODY_END`. This extension can add one fixed, site-wide guide without requiring an editor to place it on every page. The script creates a portable custom element, and the element stays collapsed until a visitor opens it.

A site-widget or editor-placed custom element remains useful when Fortune wants the guide inside the layout of selected pages. Wix site widgets do not provide the same reliable fixed placement, so the embedded script is the primary route for the floating sidecar.

## Service boundary

```text
Fortune page
  -> embedded custom element with current public page context
  -> Wix backend web method or HTTP endpoint
  -> retrieval over Fortune's approved public index
  -> Ollama Cloud provider adapter
  -> validated answer, sources, related routes, and handoff
```

The browser sends the visitor's question, up to eight complete recent exchanges (sixteen messages), and a small page-context object:

```json
{
  "message": "I need a class",
  "history": [{ "role": "assistant", "content": "What kind of class are you looking for?" }],
  "page_context": {
    "url": "https://www.fortunedigitalequity.org/trainings",
    "path": "/trainings",
    "title": "Trainings"
  }
}
```

The canonical backend resolves `page_context` against the approved site index and never treats browser-supplied text as a factual source. It sanitizes `history` and searches current approved evidence across the site. Every successful non-private new request reaches the model with a bounded set of approved current records. The model writes the concise participant-facing answer or one clarifying question; the server validates its response shape and selected source without reclassifying the prose. When retrieval has no match, the model receives no factual records and responds conversationally rather than receiving a server-authored fallback.

## Optional Copilot Studio evaluation

[`copilot-studio-bridge/`](copilot-studio-bridge/README.md) provides a separately hosted Wix iframe and Direct Line token broker for evaluating the agent already provisioned in Microsoft Copilot Studio. The Direct Line secret remains in the server environment, while the browser receives one short-lived conversation token.

This bridge does not run the source-first retrieval ladder or privacy hold implemented by `server.py`. Use it only with a Copilot agent restricted to reviewed public information. Do not treat it as the production replacement for the shared guide until equivalent pre-provider privacy, authority, source, and handoff checks are demonstrated.

## Initial private-app setup

1. Create a private app with the current unified Wix CLI and generate an embedded-script extension. Keep the generated extension ID and manifest files from Wix; this scaffold does not invent them.
2. Configure the extension to load at `BODY_END`, package the custom-element file with the extension, and adapt `embedded.html.example` to the asset reference produced by the generated project.
3. Store the canonical Website Guide API URL in Wix configuration. Keep the provider key only in the canonical Railway backend; do not place it in Wix HTML, element attributes, dynamic parameters, client JavaScript, logs, or GitHub settings.
4. Expose a narrow backend web method or HTTP relay for chat. Wrap `backend/ollama-proxy.example.mjs` with the imports and permission declarations generated for the current Wix project; the relay must not run a second model or author responses.
5. Connect the endpoint to the approved site index and link graph. Allow retrieval only from canonical Fortune public URLs and reviewed knowledge entries.
6. Give the element a public API base URL. If the web method is same-origin, use that route. If an external backend is used, allow only the Fortune production and approved preview origins.
7. Install the private app on a Wix test site, activate the embedded script, and verify it across representative desktop and mobile pages before installing it on the production site.

## Required backend behavior

- Hold messages containing likely personal information before the model call and guide the visitor toward staff.
- Route vague or ambiguous requests to the model with bounded approved navigation records and require one short clarifying question.
- Treat the current page as an isolated first retrieval scope. Search the broader approved index only after the current page fails to supply usable evidence.
- Require `model_called: true` for every successful non-private new turn. The pre-model privacy hold is the only successful zero-call path.
- Validate every source and related-link ID against the index after the model responds.
- Add a safe related route when the response lacks one. Use a page-specific continuation when available and the Digital Equity contact page for unresolved questions.
- Ground low-confidence, eligibility, enrollment, schedule, and staff-bound wording in approved sources; reject unsupported output instead of substituting fixed participant copy.
- Avoid query logging by default. If Fortune later approves evaluation logging, document purpose, fields, retention, access, and deletion before enabling it.
- Return the shared answer fields plus server-issued conversation, turn, event, and message IDs, the signed continuation token, and capture status described in [`../CONVERSATION-CAPTURE.md`](../CONVERSATION-CAPTURE.md).
- Preserve `model_called` in the client transcript metadata. A successful non-private new turn is invalid unless it is `true`.

## No-dead-end interface checks

Every chat response must end with all of the following:

1. An answer or one clarifying question.
2. A validated source link when website evidence exists.
3. A validated page or staff destination that continues the visitor's path.
4. The chat input, ready for a follow-up question.
5. A staff contact route when the question remains unresolved.

Automated tests should fail when an evidence-backed response has an empty `sources` array, any response lacks a validated continuation route, a URL is unapproved, a response asks more than one clarifying question, a choice is malformed, or a low-confidence response lacks `handoff_url`. Contract tests should also reject unexpected field names such as `answer`, `context`, `handoff`, or `model_active`. A link check should request each current public destination and report redirects, errors, and removed pages.

## Delivery phases

### 1. Local and hosted demonstration

Complete the page-first retrieval ladder, broader-site routing, ambiguity fixtures, source validation, and no-dead-end tests in the current demo. The static GitHub Pages remain readable when chat is unavailable, but the browser must not substitute an unlogged chat answer. Enabling the live model on the public demonstration also requires a reachable backend with the Pages origin explicitly allowed.

### 2. Wix test installation

Create the private Wix app, install the embedded script on a test site, and connect it to the same API contract. Verify the compact closed state, readable type, current-page context, mobile behavior, keyboard behavior, and model-status display.

### 3. Fortune review

Walk Jacob and the Fortune team through the source inventory, excluded material, ambiguity choices, handoff language, transcript boundary, provider boundary, and staff ownership. Record approval for the pilot pages and the review schedule.

### 4. Limited production pilot

Enable the extension on selected site paths or for a defined pilot period. Review broken links and unanswered routes with staff. Keep removal and non-deployment available throughout the pilot.

### 5. Production decision

If Fortune continues, establish a named content owner, a technical owner, key rotation, model-cost limits, incident handling, accessibility checks, and a recurring source review. Keep the provider call behind an adapter so Fortune can move from Ollama Cloud to Microsoft Copilot or Azure without rebuilding the visitor interface.

## Acceptance checklist

- The closed control does not cover page navigation or content.
- Opening the guide does not change the page zoom or move keyboard focus unexpectedly.
- The guide reports the current page to the backend, searches that page first, and expands to another approved page only when needed.
- Each evidence-backed answer includes a working source; every response includes another useful page or staff route and an available chat input.
- Known ambiguous requests receive one focused, model-authored clarification before an answer.
- Personal-information tests never reach Ollama Cloud.
- The browser, source bundle, GitHub repository, and network responses contain no API key.
- Disabling the Wix extension removes the guide without changing page content.

## Official Wix references

- [Embedded scripts](https://dev.wix.com/docs/wix-cli/guides/extensions/site-extensions/embedded-scripts/about-embedded-scripts)
- [Embedded-script files and code](https://dev.wix.com/docs/wix-cli/guides/extensions/site-extensions/embedded-scripts/embedded-script-extension-files-and-code)
- [Site-widget extensions](https://dev.wix.com/docs/build-apps/develop-your-app/extensions/site-extensions/site-widgets/about-site-widget-extensions)
- [Web methods](https://dev.wix.com/docs/sdk/core-modules/web-methods/introduction)
- [Secrets Manager](https://dev.wix.com/docs/develop-websites-sdk/code-your-site/developer-environments/secrets/about-the-secrets-manager)
