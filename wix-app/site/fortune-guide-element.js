/**
 * Portable Wix embedded-script example.
 *
 * This browser component receives a public backend URL. It never receives an
 * Ollama key. Copy it into the generated Wix extension as
 * `fortune-guide-element.js`, then adjust labels and styling with Fortune.
 */
(() => {
  const TAG_NAME = "fortune-digital-equity-guide";
  const CONTACT_URL = "https://www.fortunedigitalequity.org/contact";
  const MAX_CONTEXT_MESSAGES = 6;
  const CONVERSATION_STORAGE_KEY = "fortune-website-guide:wix:v20";
  const STARTERS = Object.freeze([
    { label: "Page summary", prompt: "What is the main information here?" },
    { label: "Page options", prompt: "What can I do from this page?" }
  ]);

  if (customElements.get(TAG_NAME)) return;

  const cleanText = (value) => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, " ")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();

  const normalizeDigits = (value) => {
    const ranges = [[0x0660, 0x0669], [0x06f0, 0x06f9], [0xff10, 0xff19]];
    return String(value || "").normalize("NFKC").replace(/\p{Nd}/gu, (character) => {
      const code = character.codePointAt(0);
      for (const [start, end] of ranges) {
        if (code >= start && code <= end) return String(code - start);
      }
      return character;
    });
  };

  const personalInformationDetected = (value) => {
    const normalized = normalizeDigits(value);
    const patterns = [
      /(?<!\d)\d{6}(?!\d)/,
      /(?<!\d)\d{3}[-‐‑‒–—.\s]?\d{3}(?!\d)/,
      /\b\d{3}[-. ]?\d{2}[-. ]?\d{4}\b/,
      /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i,
      /\b(?:my|their|participant'?s?)\s+(?:fortune\s+)?(?:id|case number|name|address|phone|email)\b/i,
      /\b(?:social security|ssn|date of birth|dob|password|passcode|my health|my diagnosis)\b/i,
      /\b(?:call me at|my number is|i live at)\b/i
    ];
    return patterns.some((pattern) => pattern.test(normalized));
  };

  const redactSixDigitValues = (value) => normalizeDigits(value)
    .replace(/(?<!\d)\d{3}[-‐‑‒–—.\s]?\d{3}(?!\d)/g, "[six-digit ID removed]")
    .replace(/(?<!\d)\d{6}(?!\d)/g, "[six-digit ID removed]");

  const asHttpUrl = (value) => {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, window.location.href);
      return url.protocol === "https:" || url.protocol === "http:" ? url : null;
    } catch {
      return null;
    }
  };

  const isSafeLink = (value) => Boolean(asHttpUrl(value));

  const isFortuneLink = (value) => {
    const url = asHttpUrl(value);
    return Boolean(url && /^(?:www\.)?fortunedigitalequity\.org$/i.test(url.hostname));
  };

  const comparableUrl = (value) => {
    const url = asHttpUrl(value);
    if (!url) return "";
    return `${url.hostname.replace(/^www\./i, "").toLowerCase()}${url.pathname.replace(/\/+$/, "") || "/"}`;
  };

  const destinationLabel = (value) => {
    const label = cleanText(value)
      .replace(/\s*[|·]\s*FS Digital Equity\s*$/i, "")
      .replace(/\s*[|·]\s*Digital Equity Program\s*$/i, "") || "Open page";
    return /^(?:go to|contact|confirm|ask|view|review|register|browse|open|find|see|check)\b/i.test(label)
      ? label
      : `Go to ${label}`;
  };

  class FortuneDigitalEquityGuide extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;

      this.history = [];
      this.turns = [];
      this.conversationId = "";
      this.conversationToken = "";
      this.pendingClientEventId = "";
      this.pendingQuestion = "";
      this.lastQuestion = "";
      this.editingQuestion = "";
      this.answering = false;
      this.capturePolicyReady = false;
      this.capturePolicyPromise = null;
      this.warmupPromise = null;

      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          :host {
            --guide-ink: #0b0b0b;
            --guide-muted: #6b6b6b;
            --guide-line: #dddddd;
            --guide-pale: #f1f1f1;
            --guide-paper: #ffffff;
            --guide-display: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
            font-family: "Avenir Next", Avenir, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            position: fixed;
            inset: auto 18px 18px auto;
            z-index: 2147483000;
          }
          *, *::before, *::after { box-sizing: border-box; }
          button, textarea { font: inherit; }
          :focus-visible { outline: 3px solid var(--guide-ink); outline-offset: 3px; }
          .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
          .toggle {
            min-width: 56px;
            min-height: 44px;
            padding: 0 16px;
            border: 1px solid var(--guide-ink);
            border-radius: 3px;
            background: var(--guide-ink);
            color: var(--guide-paper);
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            transition: background .14s ease, color .14s ease;
          }
          .toggle:hover { color: var(--guide-ink); background: var(--guide-paper); }
          .panel {
            width: min(440px, calc(100vw - 24px));
            max-height: min(620px, calc(100dvh - 24px));
            display: flex;
            flex-direction: column;
            overflow: hidden;
            border: 1px solid var(--guide-ink);
            border-radius: 3px;
            background: var(--guide-paper);
            color: var(--guide-ink);
            contain: layout paint;
            transition: height .16s ease, max-height .16s ease;
          }
          .panel.expanded { height: min(760px, calc(100dvh - 24px)); max-height: calc(100dvh - 24px); }
          .panel[hidden] { display: none; }
          .head {
            min-height: 56px;
            flex: 0 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding: 0 16px;
            border-bottom: 1px solid var(--guide-line);
            background: var(--guide-paper);
          }
          h2 { margin: 0; font-family: var(--guide-display); font-size: 19px; font-weight: 600; letter-spacing: -.012em; line-height: 1.2; }
          .close, .edit-question, .cancel-edit {
            min-height: 44px;
            padding: 0 8px;
            border: 1px solid transparent;
            border-radius: 3px;
            background: transparent;
            color: var(--guide-muted);
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
          }
          .close:hover, .edit-question:hover, .cancel-edit:hover { border-color: var(--guide-line); color: var(--guide-ink); background: var(--guide-pale); }
          .transcript {
            min-height: 0;
            max-height: 240px;
            display: grid;
            align-content: start;
            gap: 14px;
            padding: 16px;
            overflow-y: auto;
            overscroll-behavior: contain;
            scrollbar-gutter: stable;
          }
          .transcript:empty { display: none; }
          .panel.expanded .transcript { max-height: none; flex: 1 1 auto; }
          .turn { display: grid; gap: 14px; }
          .turn + .turn { padding-top: 14px; border-top: 1px solid var(--guide-line); }
          .message { display: grid; gap: 7px; }
          .message.user {
            margin-left: 28px;
            padding: 10px 12px;
            border: 1px solid var(--guide-line);
            border-left: 3px solid var(--guide-ink);
            border-radius: 2px;
            background: var(--guide-pale);
          }
          .message.user.is-editing { border-color: var(--guide-ink); background: #fafafa; box-shadow: inset 3px 0 var(--guide-ink); }
          .message-meta { min-height: 24px; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
          .speaker {
            margin: 0;
            color: var(--guide-muted);
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 9.5px;
            font-weight: 700;
            letter-spacing: .09em;
            line-height: 1.3;
            text-transform: uppercase;
          }
          .copy { margin: 0; color: var(--guide-ink); font-size: 15.5px; line-height: 1.55; white-space: pre-wrap; text-wrap: pretty; }
          .edit-question { margin: -10px -6px -10px 0; color: var(--guide-ink); }
          .suggestion {
            min-height: 44px;
            padding: 0 12px;
            border: 1px solid var(--guide-line);
            border-radius: 3px;
            color: var(--guide-ink);
            background: var(--guide-paper);
            font-size: 13px;
            font-weight: 700;
            line-height: 1.35;
            text-align: left;
            cursor: pointer;
          }
          .suggestion:hover { border-color: var(--guide-ink); color: var(--guide-paper); background: var(--guide-ink); }
          .suggestion:disabled { opacity: .55; cursor: wait; }
          .choice-select {
            width: min(100%, 220px);
            min-height: 40px;
            margin-top: 3px;
            padding: 0 4px;
            border: 0;
            border-bottom: 1px solid var(--guide-line);
            border-radius: 0;
            color: var(--guide-muted);
            background: transparent;
            cursor: pointer;
            font: 700 12px/1.35 "Avenir Next", Avenir, "Segoe UI", sans-serif;
          }
          .choice-select:hover { border-color: var(--guide-ink); color: var(--guide-ink); }
          .choice-select:disabled { opacity: .55; cursor: wait; }
          .destination {
            min-height: 44px;
            display: inline-flex;
            align-items: center;
            justify-self: start;
            margin-top: 6px;
            padding: 0 13px;
            border: 1px solid var(--guide-ink);
            border-radius: 3px;
            color: var(--guide-paper);
            background: var(--guide-ink);
            font-size: 13px;
            font-weight: 700;
            text-decoration: none;
            transition: background .14s ease, color .14s ease;
          }
          .destination:hover { color: var(--guide-ink); background: var(--guide-paper); }
          .suggestions {
            flex: 0 0 auto;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 7px;
            padding: 12px 16px;
            border-bottom: 1px solid var(--guide-line);
          }
          .suggestions:empty { display: none; }
          .suggestion { width: 100%; text-align: center; }
          form {
            flex: 0 0 auto;
            display: grid;
            gap: 6px;
            padding: 12px 16px 9px;
            border-top: 1px solid var(--guide-line);
            background: var(--guide-paper);
          }
          .row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 7px; }
          form.is-editing .row { grid-template-columns: minmax(0, 1fr) auto auto; }
          textarea {
            width: 100%;
            min-width: 0;
            min-height: 46px;
            max-height: 92px;
            overflow-y: hidden;
            resize: none;
            padding: 10px 12px;
            border: 1px solid var(--guide-muted);
            border-radius: 2px;
            color: var(--guide-ink);
            background: var(--guide-paper);
            font-size: 16px;
            line-height: 1.42;
          }
          textarea:hover { border-color: var(--guide-ink); }
          textarea:focus { border-color: var(--guide-ink); }
          .send {
            min-width: 74px;
            min-height: 46px;
            padding: 0 12px;
            border: 1px solid var(--guide-ink);
            border-radius: 3px;
            color: var(--guide-paper);
            background: var(--guide-ink);
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            transition: background .14s ease, color .14s ease;
          }
          .send:hover { color: var(--guide-ink); background: var(--guide-paper); }
          .send:disabled { opacity: .65; cursor: wait; }
          .cancel-edit { min-width: 62px; min-height: 46px; color: var(--guide-ink); background: var(--guide-paper); }
          .edit-status { margin: 0; color: var(--guide-ink); font-size: 11px; font-weight: 700; line-height: 1.4; }
          .edit-status[hidden] { display: none; }
          .privacy { margin: 0; color: var(--guide-muted); font-size: 11px; line-height: 1.35; }
          .status { margin: 0; color: var(--guide-muted); font-size: 12px; line-height: 1.4; }
          .status:empty { display: none; }
          .footer {
            min-height: 48px;
            flex: 0 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 0 16px;
            border-top: 1px solid var(--guide-line);
            background: var(--guide-paper);
          }
          .meta { position: relative; color: var(--guide-ink); }
          .meta summary { min-height: 44px; display: flex; align-items: center; padding: 0 6px; cursor: pointer; font-size: 12px; font-weight: 700; }
          .info {
            position: absolute;
            z-index: 4;
            bottom: calc(100% + 8px);
            left: -8px;
            width: min(310px, calc(100vw - 48px));
            padding: 12px;
            border: 1px solid var(--guide-ink);
            border-radius: 2px;
            background: var(--guide-paper);
          }
          .info p { margin: 0; color: var(--guide-muted); font-size: 11px; line-height: 1.45; }
          .info p + p { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--guide-line); }
          .reset,
          .contact {
            min-height: 44px;
            display: inline-flex;
            align-items: center;
            padding: 0 6px;
            color: var(--guide-ink);
            border: 0;
            background: transparent;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
            cursor: pointer;
          }
          .reset:hover,
          .contact:hover { text-decoration: underline; text-underline-offset: 4px; }
          .reset:disabled { color: var(--guide-muted); cursor: wait; }
          .contact { margin-left: auto; }
          @media (max-width: 520px) {
            :host { inset: auto 8px 8px 8px; }
            .panel { width: 100%; max-height: calc(100dvh - 16px); }
            .panel.expanded { height: calc(100dvh - 16px); max-height: calc(100dvh - 16px); }
            .panel.expanded .transcript { padding: 14px; }
            form { padding: 10px 14px 8px; }
            .row { grid-template-columns: minmax(0, 1fr) 74px; }
            form.is-editing .row { grid-template-columns: minmax(0, 1fr) 70px 62px; }
            .send { width: 74px; padding-inline: 8px; }
            .footer { padding: 0 14px; }
          }
          @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
              scroll-behavior: auto !important;
              transition-duration: .01ms !important;
              animation-duration: .01ms !important;
            }
          }
        </style>
        <button class="toggle" id="fortune-guide-toggle" type="button" aria-controls="fortune-guide-panel" aria-expanded="false">Website Guide</button>
        <section class="panel" id="fortune-guide-panel" role="dialog" aria-modal="false" aria-labelledby="fortune-guide-title" aria-hidden="true" hidden>
          <header class="head">
            <h2 id="fortune-guide-title">Website Guide</h2>
            <button class="close" type="button" aria-label="Close Website Guide">Close</button>
          </header>
          <div class="transcript result" aria-live="polite" aria-label="Guide conversation"></div>
          <div class="suggestions" aria-label="Questions about this page"></div>
          <form>
            <label class="sr-only" id="fortune-guide-question-label" for="fortune-guide-question">Question</label>
            <div class="row">
              <textarea id="fortune-guide-question" name="question" autocomplete="off" maxlength="600" required rows="1" placeholder="Ask about this page" aria-describedby="fortune-guide-privacy fortune-guide-key-hint"></textarea>
              <button class="send" type="submit">Send</button>
              <button class="cancel-edit" type="button" hidden>Cancel</button>
            </div>
            <p class="edit-status" role="status" aria-live="polite" hidden></p>
            <p class="privacy" id="fortune-guide-privacy">Don’t include personal information.</p>
            <span class="sr-only" id="fortune-guide-key-hint">Press Enter to send. Press Shift+Enter for a new line.</span>
            <p class="status" role="status" aria-live="polite"></p>
          </form>
          <footer class="footer">
            <details class="meta">
              <summary>Info</summary>
              <div class="info">
                <p class="context-count">Context · conversation · 0/3</p>
                <p class="capture-notice">Starting…</p>
                <p>Don’t include your Fortune ID, name, contact, case, or health information.</p>
                <p class="model-status">Starting…</p>
              </div>
            </details>
            <button class="reset" type="button" aria-label="Start a new conversation" hidden>Start over</button>
            <a class="contact" href="${CONTACT_URL}">Contact</a>
          </footer>
        </section>
      `;

      this.toggleButton = root.querySelector(".toggle");
      this.panel = root.querySelector(".panel");
      this.closeButton = root.querySelector(".close");
      this.transcript = root.querySelector(".transcript");
      this.suggestions = root.querySelector(".suggestions");
      this.form = root.querySelector("form");
      this.questionLabel = root.querySelector("#fortune-guide-question-label");
      this.input = root.querySelector("textarea");
      this.sendButton = root.querySelector(".send");
      this.cancelEditButton = root.querySelector(".cancel-edit");
      this.editStatus = root.querySelector(".edit-status");
      this.privacyNotice = root.querySelector("#fortune-guide-privacy");
      this.captureNotice = root.querySelector(".capture-notice");
      this.contextCount = root.querySelector(".context-count");
      this.modelStatus = root.querySelector(".model-status");
      this.status = root.querySelector(".status");
      this.resetButton = root.querySelector(".reset");
      this.contactLink = root.querySelector(".contact");

      const configuredContact = this.getAttribute("contact-url") || CONTACT_URL;
      if (isSafeLink(configuredContact)) {
        this.contactLink.href = configuredContact;
      } else {
        this.contactLink.hidden = true;
      }

      this.toggleButton.addEventListener("click", () => this.open());
      this.closeButton.addEventListener("click", () => this.close());
      this.cancelEditButton.addEventListener("click", () => this.cancelEdit());
      this.resetButton.addEventListener("click", () => this.resetConversation());
      this.form.addEventListener("submit", (event) => {
        event.preventDefault();
        this.ask(this.input.value);
      });
      this.input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
        event.preventDefault();
        this.form.requestSubmit();
      });
      this.input.addEventListener("input", () => this.resizeQuestionField());
      this.suggestions.addEventListener("click", (event) => {
        const button = event.target.closest("[data-prompt]");
        if (button) this.ask(button.dataset.prompt, { restoreFocus: event.detail === 0 });
      });
      this.transcript.addEventListener("click", (event) => {
        const edit = event.target.closest(".edit-question");
        if (edit) {
          this.beginEdit();
          return;
        }
        const choice = event.target.closest("[data-prompt]");
        if (choice) this.ask(choice.dataset.prompt, { restoreFocus: event.detail === 0 });
      });
      this.transcript.addEventListener("change", (event) => {
        const select = event.target.closest(".choice-select");
        if (!select?.value) return;
        const prompt = select.value;
        select.value = "";
        this.ask(prompt, { restoreFocus: true });
      });
      root.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !this.panel.hidden) this.close();
      });

      if (this.restoreConversation()) {
        this.panel.classList.add("expanded");
        this.renderConversation();
      } else {
        this.renderSuggestions();
      }
      this.updateContextCount();
      this.loadCapturePolicy();
      this.warmModel();
    }

    open() {
      this.panel.hidden = false;
      this.panel.setAttribute("aria-hidden", "false");
      this.toggleButton.hidden = true;
      this.toggleButton.setAttribute("aria-expanded", "true");
      this.warmModel();
      requestAnimationFrame(() => this.closeButton.focus({ preventScroll: true }));
    }

    close() {
      this.panel.hidden = true;
      this.panel.setAttribute("aria-hidden", "true");
      this.toggleButton.hidden = false;
      this.toggleButton.setAttribute("aria-expanded", "false");
      this.toggleButton.focus({ preventScroll: true });
    }

    apiUrl(path) {
      const base = this.getAttribute("api-base-url");
      if (!base) throw new Error("Guide backend unavailable.");
      return new URL(path.replace(/^\//, ""), `${base.replace(/\/$/, "")}/`).toString();
    }

    pageContext() {
      return {
        url: window.location.href,
        path: window.location.pathname,
        title: document.title
      };
    }

    conversationStorage() {
      try {
        return window.sessionStorage;
      } catch {
        return null;
      }
    }

    storedPayload(payload = {}, answer = "") {
      const safeRows = (rows) => (Array.isArray(rows) ? rows : []).slice(0, 4).map((row) => ({
        id: cleanText(row?.id).slice(0, 160),
        title: cleanText(row?.title).slice(0, 240),
        url: String(row?.url || "").slice(0, 1000)
      })).filter((row) => row.title && row.url);
      const choices = (Array.isArray(payload?.choices) ? payload.choices : []).slice(0, 3).map((choice) => ({
        label: cleanText(choice?.label).slice(0, 160),
        prompt: cleanText(choice?.prompt || choice?.label).slice(0, 600)
      })).filter((choice) => choice.label && choice.prompt);
      return {
        kind: ["answer", "clarify", "handoff"].includes(payload?.kind) ? payload.kind : "answer",
        message: redactSixDigitValues(cleanText(answer || payload?.message)).slice(0, 4000),
        retrieval_scope: ["page", "site", "staff"].includes(payload?.retrieval_scope)
          ? payload.retrieval_scope
          : "site",
        choices,
        sources: safeRows(payload?.sources),
        related: safeRows(payload?.related),
        model_called: payload?.model_called === true
      };
    }

    storedTurn(value) {
      const question = cleanText(value?.question).slice(0, 600);
      const answer = redactSixDigitValues(cleanText(value?.answer)).slice(0, 4000);
      if (!question || !answer || personalInformationDetected(question)) return null;
      const payload = this.storedPayload(value?.payload, answer);
      if (!payload.model_called) return null;
      return {
        question,
        answer,
        payload,
        editable: true
      };
    }

    clearPersistedConversation() {
      try {
        this.conversationStorage()?.removeItem(CONVERSATION_STORAGE_KEY);
      } catch {
        // Storage is optional; the in-memory conversation still works.
      }
    }

    persistConversation() {
      const storage = this.conversationStorage();
      if (!storage) return;
      if (!this.turns.length) {
        this.clearPersistedConversation();
        return;
      }
      try {
        storage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify({
          version: 1,
          turns: this.turns.slice(-3).map((turn) => this.storedTurn(turn)).filter(Boolean),
          conversationId: this.conversationId,
          conversationToken: this.conversationToken
        }));
      } catch {
        // A storage quota or policy failure must not break the guide.
      }
    }

    restoreConversation() {
      const storage = this.conversationStorage();
      if (!storage) return false;
      try {
        const saved = JSON.parse(storage.getItem(CONVERSATION_STORAGE_KEY) || "null");
        if (saved?.version !== 1 || !Array.isArray(saved.turns)) return false;
        const restored = saved.turns.slice(-3).map((turn) => this.storedTurn(turn));
        if (!restored.length || restored.some((turn) => !turn)) {
          this.clearPersistedConversation();
          return false;
        }
        this.turns = restored;
        this.history = restored.flatMap((turn) => [
          { role: "user", content: turn.question },
          { role: "assistant", content: turn.answer }
        ]).slice(-MAX_CONTEXT_MESSAGES);
        this.lastQuestion = restored.at(-1)?.question || "";
        this.conversationId = /^[0-9a-f-]{36}$/i.test(String(saved.conversationId || ""))
          ? String(saved.conversationId)
          : "";
        this.conversationToken = /^[A-Za-z0-9_-]{32,128}$/.test(String(saved.conversationToken || ""))
          ? String(saved.conversationToken)
          : "";
        this.suggestions.replaceChildren();
        return true;
      } catch {
        this.clearPersistedConversation();
        return false;
      }
    }

    renderSuggestions() {
      this.suggestions.replaceChildren();
      STARTERS.forEach(({ label, prompt }) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "suggestion";
        button.textContent = label;
        button.dataset.prompt = prompt;
        this.suggestions.append(button);
      });
    }

    updateContextCount() {
      const count = Math.min(MAX_CONTEXT_MESSAGES / 2, Math.floor(this.history.length / 2));
      this.contextCount.textContent = `Context · conversation · ${count}/${MAX_CONTEXT_MESSAGES / 2}`;
    }

    resizeQuestionField() {
      this.input.style.height = "auto";
      const maxHeight = Number.parseFloat(window.getComputedStyle(this.input).maxHeight) || 92;
      const borderHeight = this.input.offsetHeight - this.input.clientHeight;
      const contentHeight = this.input.scrollHeight + borderHeight;
      const nextHeight = Math.min(contentHeight, maxHeight);
      this.input.style.height = `${nextHeight}px`;
      this.input.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
    }

    setEditStatus(message = "") {
      this.editStatus.textContent = message;
      this.editStatus.hidden = !message;
    }

    loadCapturePolicy() {
      if (this.capturePolicyPromise) return this.capturePolicyPromise;
      let healthUrl;
      try {
        healthUrl = this.apiUrl("/health");
      } catch {
        this.privacyNotice.textContent = "Don’t include personal information.";
        this.captureNotice.textContent = "Privacy unavailable.";
        this.modelStatus.textContent = "Unavailable";
        this.capturePolicyReady = false;
        return Promise.resolve(null);
      }

      this.capturePolicyPromise = fetch(healthUrl, { cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) throw new Error("Capture policy unavailable.");
          const payload = await response.json();
          const mode = payload.conversation_logging?.capture_mode;
          if (mode === "transcript") {
            this.privacyNotice.textContent = "Recorded for team review. Don’t include personal information.";
            this.captureNotice.textContent = "Questions and answers are recorded for team review.";
          } else if (mode === "metadata") {
            this.privacyNotice.textContent = "Don’t include personal information.";
            this.captureNotice.textContent = "This review build stores IDs and response data. Chat stays in this tab across pages.";
          } else {
            this.privacyNotice.textContent = "Don’t include personal information.";
            this.captureNotice.textContent = "Up to 3 exchanges stay in this tab across pages.";
          }
          this.modelStatus.textContent = payload.model_enabled ? "Starting…" : "Unavailable";
          this.capturePolicyReady = true;
          return payload;
        })
        .catch(() => {
          this.privacyNotice.textContent = "Don’t include personal information.";
          this.captureNotice.textContent = "Privacy unavailable.";
          this.modelStatus.textContent = "Unavailable";
          this.capturePolicyReady = false;
          return null;
        })
        .finally(() => {
          this.capturePolicyPromise = null;
        });
      return this.capturePolicyPromise;
    }

    setBusy(value) {
      this.answering = value;
      this.panel.setAttribute("aria-busy", String(value));
      this.sendButton.disabled = value;
      this.input.readOnly = value;
      this.cancelEditButton.disabled = value;
      this.resetButton.disabled = value;
      this.suggestions.querySelectorAll("button").forEach((button) => { button.disabled = value; });
      this.transcript.querySelectorAll("button").forEach((button) => { button.disabled = value; });
      this.transcript.querySelectorAll("select").forEach((select) => { select.disabled = value; });
      this.sendButton.textContent = value ? "Sending…" : this.editingQuestion ? "Update" : "Send";
    }

    privacyHold(editing) {
      this.pendingClientEventId = "";
      this.pendingQuestion = "";
      this.input.value = "";
      this.resizeQuestionField();

      if (editing) {
        this.setEditStatus("Not sent. Remove personal information.");
        return;
      }

      this.history = [];
      this.turns = [{
        question: "Not sent",
        answer: "Remove personal information and try again.",
        payload: {},
        editable: false
      }];
      this.lastQuestion = "";
      this.editingQuestion = "";
      this.conversationId = "";
      this.conversationToken = "";
      this.clearPersistedConversation();
      this.form.classList.remove("is-editing");
      this.cancelEditButton.hidden = true;
      this.questionLabel.textContent = "Question";
      this.setEditStatus();
      this.suggestions.replaceChildren();
      this.panel.classList.add("expanded");
      this.status.textContent = "";
      this.updateContextCount();
      this.renderConversation();
      this.revealResult();
    }

    async ask(rawQuestion, options = {}) {
      const question = cleanText(rawQuestion);
      if (!question || this.answering) {
        if (!question) this.status.textContent = "Enter a question.";
        return;
      }

      const editing = Boolean(this.editingQuestion);
      const restoreComposerFocus = options.restoreFocus || this.form.contains(this.shadowRoot.activeElement);
      this.input.value = "";
      this.resizeQuestionField();
      if (personalInformationDetected(question)) {
        this.privacyHold(editing);
        return;
      }

      const safeQuestion = redactSixDigitValues(question);
      const requestHistory = editing ? this.history.slice(0, -2) : this.history;
      this.turns = this.turns.filter((turn) => !turn.transient);
      this.setBusy(true);
      this.panel.classList.add("expanded");
      this.suggestions.replaceChildren();
      this.status.textContent = "";

      if (this.pendingQuestion !== safeQuestion || !this.pendingClientEventId) {
        this.pendingQuestion = safeQuestion;
        this.pendingClientEventId = window.crypto.randomUUID();
      }

      try {
        if (!this.capturePolicyReady) await this.loadCapturePolicy();
        if (!this.capturePolicyReady) throw new Error("Guide unavailable.");
        const response = await fetch(this.apiUrl("/api/chat"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: safeQuestion,
            history: requestHistory,
            page_context: this.pageContext(),
            client_surface: "wix",
            client_event_id: this.pendingClientEventId,
            conversation_id: editing ? undefined : this.conversationId || undefined,
            conversation_token: editing ? undefined : this.conversationToken || undefined
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          const error = new Error("Guide unavailable.");
          error.payload = payload;
          error.status = response.status;
          throw error;
        }
        if (payload.kind === "privacy") {
          this.privacyHold(editing);
          return;
        }
        if (
          !["answer", "clarify", "handoff"].includes(payload.kind)
          || payload.model_called !== true
          || !cleanText(payload.message)
        ) {
          const error = new Error("The guide returned an invalid response.");
          error.payload = payload;
          error.status = response.status;
          throw error;
        }

        this.pendingClientEventId = "";
        this.pendingQuestion = "";
        const answer = redactSixDigitValues(payload.message);
        const turn = {
          question: safeQuestion,
          answer,
          payload: { ...payload, message: answer },
          editable: true
        };

        this.history = requestHistory.concat(
          { role: "user", content: safeQuestion },
          { role: "assistant", content: answer }
        ).slice(-MAX_CONTEXT_MESSAGES);
        this.turns = editing
          ? this.turns.slice(0, -1).concat(turn).slice(-3)
          : this.turns.concat(turn).slice(-3);
        this.lastQuestion = safeQuestion;
        this.conversationId = String(payload.conversation_id || (editing ? "" : this.conversationId));
        this.conversationToken = String(payload.conversation_token || (editing ? "" : this.conversationToken));
        this.editingQuestion = "";
        this.form.classList.remove("is-editing");
        this.cancelEditButton.hidden = true;
        this.questionLabel.textContent = "Question";
        this.setEditStatus();
        this.status.textContent = "";
        this.updateContextCount();
        this.renderConversation();
        this.persistConversation();
        this.revealResult();
      } catch (error) {
        const status = Number(error?.status || 0);
        const retryInProgress = status === 409
          && error?.payload?.idempotency_complete === false;
        if (error && Object.prototype.hasOwnProperty.call(error, "payload") && !retryInProgress) {
          this.pendingClientEventId = "";
          this.pendingQuestion = "";
        }
        if (![409, 429, 502].includes(status)) this.modelStatus.textContent = "Unavailable";
        const failureMessage = status === 409 && error?.payload?.idempotency_complete === false
          ? (editing ? "Still working. Try again or cancel." : "Still working. Try again.")
          : error?.payload?.idempotency_complete === true
            ? (editing ? "Try again or cancel." : "Try again.")
            : status === 429
              ? (editing ? "Guide busy. Try again shortly or cancel." : "Guide busy. Try again shortly.")
              : status === 502
                ? (editing ? "Try rephrasing or cancel." : "Try rephrasing.")
                : editing && status && status !== 503
                  ? "Couldn’t update. Try again or cancel."
                  : editing
                    ? "Guide unavailable. Try again or cancel."
                    : "Guide unavailable. Try again.";
        if (editing) {
          this.input.value = safeQuestion;
          this.resizeQuestionField();
          this.setEditStatus(failureMessage);
        } else {
          this.input.value = safeQuestion;
          this.resizeQuestionField();
          this.status.textContent = failureMessage;
        }
      } finally {
        this.setBusy(false);
        if (restoreComposerFocus && !this.panel.hidden) this.input.focus({ preventScroll: true });
      }
    }

    beginEdit() {
      if (!this.lastQuestion || this.answering) return;
      this.editingQuestion = this.lastQuestion;
      this.pendingClientEventId = "";
      this.pendingQuestion = "";
      this.input.value = this.lastQuestion;
      this.resizeQuestionField();
      this.sendButton.textContent = "Update";
      this.form.classList.add("is-editing");
      this.cancelEditButton.hidden = false;
      this.questionLabel.textContent = "Edit question";
      this.setEditStatus();
      this.status.textContent = "";
      this.renderConversation();
      this.input.focus({ preventScroll: true });
      this.input.setSelectionRange(this.input.value.length, this.input.value.length);
    }

    cancelEdit() {
      if (this.answering) return;
      this.editingQuestion = "";
      this.pendingClientEventId = "";
      this.pendingQuestion = "";
      this.input.value = "";
      this.resizeQuestionField();
      this.sendButton.textContent = "Send";
      this.form.classList.remove("is-editing");
      this.cancelEditButton.hidden = true;
      this.questionLabel.textContent = "Question";
      this.setEditStatus();
      this.status.textContent = "";
      this.renderConversation();
    }

    resetConversation() {
      if (this.answering) return;
      this.editingQuestion = "";
      this.pendingClientEventId = "";
      this.pendingQuestion = "";
      this.lastQuestion = "";
      this.history = [];
      this.turns = [];
      this.conversationId = "";
      this.conversationToken = "";
      this.input.value = "";
      this.resizeQuestionField();
      this.sendButton.textContent = "Send";
      this.form.classList.remove("is-editing");
      this.cancelEditButton.hidden = true;
      this.questionLabel.textContent = "Question";
      this.setEditStatus();
      this.status.textContent = "";
      this.panel.classList.remove("expanded");
      this.clearPersistedConversation();
      this.renderConversation();
      this.renderSuggestions();
      this.updateContextCount();
      this.input.focus({ preventScroll: true });
    }

    warmModel() {
      if (this.warmupPromise) return this.warmupPromise;
      let warmupUrl;
      try {
        warmupUrl = this.apiUrl("/api/warmup");
      } catch {
        this.modelStatus.textContent = "Unavailable";
        return Promise.resolve(null);
      }

      this.warmupPromise = fetch(warmupUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      })
        .then(async (response) => {
          if (!response.ok) throw new Error("Model warm-up failed.");
          const payload = await response.json();
          this.modelStatus.textContent = payload.status === "ready" ? "Ready" : "Unavailable";
          return payload;
        })
        .catch(() => {
          this.modelStatus.textContent = "Unavailable";
          return null;
        })
        .finally(() => {
          this.warmupPromise = null;
        });
      return this.warmupPromise;
    }

    distinctDestination(payload) {
      const sources = Array.isArray(payload?.sources) ? payload.sources : [];
      const related = Array.isArray(payload?.related) ? payload.related : [];
      const rows = ["site", "staff"].includes(payload?.retrieval_scope)
        ? [...sources, ...related]
        : [...related, ...sources];
      const current = comparableUrl(window.location.href);
      return rows.find((item) => item?.title
        && isFortuneLink(item.url)
        && comparableUrl(item.url) !== current) || null;
    }

    addAssistantContent(container, turn) {
      const payload = turn.payload || {};
      const copy = document.createElement("p");
      copy.className = "copy";
      copy.textContent = redactSixDigitValues(turn.answer);
      container.append(copy);

      if (payload.kind === "clarify") {
        const choiceSelect = document.createElement("select");
        choiceSelect.className = "choice-select";
        choiceSelect.setAttribute("aria-label", "Choose");
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Choose";
        placeholder.disabled = true;
        placeholder.selected = true;
        choiceSelect.append(placeholder);
        (Array.isArray(payload.choices) ? payload.choices : []).slice(0, 3).forEach((choice) => {
          const label = cleanText(choice?.label);
          const prompt = cleanText(choice?.prompt || label);
          if (!label || !prompt) return;
          const option = document.createElement("option");
          option.textContent = label;
          option.value = prompt;
          choiceSelect.append(option);
        });
        if (choiceSelect.options.length > 1) container.append(choiceSelect);
      }

      const destination = Array.isArray(payload?.choices) && payload.choices.length
        ? null
        : this.distinctDestination(payload);
      if (destination) {
        const link = document.createElement("a");
        link.className = "destination";
        link.href = destination.url;
        link.textContent = destinationLabel(destination.title);
        container.append(link);
      }
    }

    renderConversation() {
      this.transcript.replaceChildren();
      this.resetButton.hidden = !this.turns.length;
      const latestEditable = [...this.turns].reverse().find((turn) => turn.editable);

      this.turns.forEach((turn) => {
        const turnElement = document.createElement("div");
        turnElement.className = "turn";

        if (turn.question) {
          const user = document.createElement("article");
          user.className = "message user";
          if (this.editingQuestion && turn === latestEditable) user.classList.add("is-editing");
          const meta = document.createElement("div");
          meta.className = "message-meta";
          const speaker = document.createElement("p");
          speaker.className = "speaker";
          speaker.textContent = "You";
          meta.append(speaker);

          if (turn === latestEditable && turn.editable) {
            const edit = document.createElement("button");
            edit.type = "button";
            edit.className = "edit-question";
            edit.textContent = "Edit";
            edit.setAttribute("aria-label", "Edit question");
            meta.append(edit);
          }

          const copy = document.createElement("p");
          copy.className = "copy";
          copy.textContent = redactSixDigitValues(turn.question);
          user.append(meta, copy);
          turnElement.append(user);
        }

        const assistant = document.createElement("article");
        assistant.className = "message assistant";
        assistant.dataset.modelCalled = String(turn.payload?.model_called === true);
        const meta = document.createElement("div");
        meta.className = "message-meta";
        const speaker = document.createElement("p");
        speaker.className = "speaker";
        speaker.textContent = "Guide";
        meta.append(speaker);
        assistant.append(meta);
        this.addAssistantContent(assistant, turn);
        turnElement.append(assistant);
        this.transcript.append(turnElement);
      });
    }

    revealResult() {
      requestAnimationFrame(() => {
        const latest = this.transcript.lastElementChild;
        if (!latest) return;
        const top = Math.max(0, latest.offsetTop - this.transcript.offsetTop - 8);
        this.transcript.scrollTo({ top, behavior: "smooth" });
      });
    }

  }

  customElements.define(TAG_NAME, FortuneDigitalEquityGuide);
})();
