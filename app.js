(() => {
  "use strict";

  const Core = window.FortuneGuideCore;
  if (!Core) throw new Error("FortuneGuideCore must load before app.js");
  const panel = document.querySelector("#guide-panel");
  const toggle = document.querySelector("#guide-toggle");
  const closeButton = document.querySelector("#guide-close");
  const title = document.querySelector("#guide-title");
  const transcript = document.querySelector("#chat-transcript");
  const suggestions = document.querySelector("#chat-suggestions");
  const form = document.querySelector("#question-form");
  const questionLabel = document.querySelector("#question-label");
  const questionField = document.querySelector("#question");
  const submitButton = form.querySelector('button[type="submit"]');
  const editStatus = document.querySelector("#edit-status");
  const editCancel = document.querySelector("#edit-cancel");
  const privacyCopy = document.querySelector("#privacy-copy");
  const modelStatus = document.querySelector("#model-status");
  const contextWindowText = document.querySelector("#context-window-text");
  const contextWindowCopy = document.querySelector("#context-window-copy");
  const resetButton = document.querySelector("#guide-reset");
  const API_BASE = String(window.FORTUNE_GUIDE_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
  const CONTACT_URL = "https://www.fortunedigitalequity.org/contact";
  const MAX_CONTEXT_MESSAGES = 6;
  const MAX_CONTEXT_EXCHANGES = MAX_CONTEXT_MESSAGES / 2;
  const CONVERSATION_STORAGE_KEY = "fortune-website-guide:replica:v20";

  let history = [];
  let turns = [];
  let latestTurn = null;
  let editTarget = null;
  let apiReady = false;
  let modelReady = false;
  let captureMode = "none";
  let conversationId = "";
  let conversationToken = "";
  let pendingClientEventId = "";
  let pendingQuestion = "";
  let answering = false;
  let activePageId = "";
  let warmupPromise = null;

  function apiUrl(path) {
    return `${API_BASE}${path}`;
  }

  function cleanText(value) {
    return Core.cleanText(value);
  }

  function personalInformationDetected(value) {
    return Core.personalInformationDetected(value);
  }

  function redactSixDigitValues(value) {
    return Core.redactSixDigitValues(value);
  }

  function currentPage() {
    return window.FortuneMockSite?.getCurrentPage?.() || null;
  }

  function pageContext() {
    const page = currentPage();
    return {
      url: page?.url || "",
      path: page?.url ? new URL(page.url).pathname : "",
      title: window.FortuneMockSite?.cleanTitle?.(page?.title) || "Digital Equity",
    };
  }

  function contextExchangeCount() {
    return Math.min(MAX_CONTEXT_EXCHANGES, Math.floor(history.length / 2));
  }

  function updateContextWindow() {
    const count = contextExchangeCount();
    contextWindowText.textContent = `Context · conversation · ${count}/${MAX_CONTEXT_EXCHANGES}`;
  }

  function conversationStorage() {
    try {
      if (window.parent && window.parent !== window) return window.parent.sessionStorage;
    } catch {
      // Cross-origin embeds cannot use their host page's tab storage.
    }
    try {
      return window.sessionStorage;
    } catch {
      return null;
    }
  }

  function storedPayload(data = {}, answer = "") {
    const safeRows = rows => (Array.isArray(rows) ? rows : []).slice(0, 4).map(row => ({
      id: cleanText(row?.id).slice(0, 160),
      title: cleanText(row?.title).slice(0, 240),
      url: String(row?.url || "").slice(0, 1000),
    })).filter(row => row.title && row.url);
    const choices = (Array.isArray(data?.choices) ? data.choices : []).slice(0, 3).map(choice => ({
      label: cleanText(choice?.label).slice(0, 160),
      prompt: cleanText(choice?.prompt || choice?.label).slice(0, 600),
    })).filter(choice => choice.label && choice.prompt);
    return {
      kind: ["answer", "clarify", "handoff"].includes(data?.kind) ? data.kind : "answer",
      message: redactSixDigitValues(cleanText(answer || data?.message)).slice(0, 4000),
      retrieval_scope: ["page", "site", "staff"].includes(data?.retrieval_scope)
        ? data.retrieval_scope
        : "site",
      choices,
      sources: safeRows(data?.sources),
      related: safeRows(data?.related),
      model_called: data?.model_called === true,
    };
  }

  function storedTurn(value) {
    const question = cleanText(value?.question).slice(0, 600);
    const answer = redactSixDigitValues(cleanText(value?.answer)).slice(0, 4000);
    if (!question || !answer || personalInformationDetected(question)) return null;
    const payload = storedPayload(value?.payload, answer);
    if (!payload.model_called) return null;
    return { question, answer, payload };
  }

  function clearPersistedConversation() {
    try {
      conversationStorage()?.removeItem(CONVERSATION_STORAGE_KEY);
    } catch {
      // Storage is optional; the in-memory conversation still works.
    }
  }

  function persistConversation() {
    const storage = conversationStorage();
    if (!storage) return;
    if (!turns.length) {
      clearPersistedConversation();
      return;
    }
    try {
      storage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify({
        version: 1,
        turns: turns.slice(-MAX_CONTEXT_EXCHANGES).map(turn => storedTurn(turn)).filter(Boolean),
        conversationId,
        conversationToken,
      }));
    } catch {
      // A storage quota or policy failure must not break the guide.
    }
  }

  function resizeQuestionField() {
    questionField.style.height = "auto";
    const maxHeight = Number.parseFloat(window.getComputedStyle(questionField).maxHeight) || 92;
    const borderHeight = questionField.offsetHeight - questionField.clientHeight;
    const contentHeight = questionField.scrollHeight + borderHeight;
    const nextHeight = Math.min(contentHeight, maxHeight);
    questionField.style.height = `${nextHeight}px`;
    questionField.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
  }

  function openGuide(options = {}) {
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");
    toggle.hidden = true;
    if (modelReady) warmModel();
    if (options.moveFocus !== false) closeButton.focus({ preventScroll: true });
  }

  function closeGuide(options = {}) {
    panel.hidden = true;
    panel.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.hidden = false;
    if (options.restoreFocus !== false) toggle.focus();
  }

  function scrollConversation() {
    requestAnimationFrame(() => transcript.scrollTo({ top: transcript.scrollHeight, behavior: "smooth" }));
  }

  function revealResponse(article) {
    panel.classList.add("is-expanded");
    requestAnimationFrame(() => {
      const articleRect = article.getBoundingClientRect();
      const transcriptRect = transcript.getBoundingClientRect();
      const top = transcript.scrollTop + articleRect.top - transcriptRect.top;
      transcript.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    });
  }

  function appendMessage(role, message, options = {}) {
    const article = document.createElement("article");
    article.className = `chat-message ${role}`;
    const meta = document.createElement("div");
    meta.className = "chat-message-meta";
    const label = document.createElement("p");
    label.className = "chat-speaker";
    label.textContent = role === "user" ? "You" : "Guide";
    const body = document.createElement("p");
    body.className = "chat-copy";
    body.textContent = redactSixDigitValues(cleanText(message));
    if (role === "assistant" && typeof options.modelCalled === "boolean") {
      article.dataset.modelCalled = String(options.modelCalled);
    }
    meta.append(label);

    if (role === "user" && options.editable) {
      transcript.querySelectorAll(".chat-message-actions").forEach(actions => actions.remove());
      const actions = document.createElement("div");
      actions.className = "chat-message-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "chat-edit-button";
      editButton.textContent = "Edit";
      editButton.setAttribute("aria-label", "Edit question");
      actions.append(editButton);
      meta.append(actions);
    }

    article.append(meta, body);

    if (Array.isArray(options.choices) && options.choices.length) {
      const choiceSelect = document.createElement("select");
      choiceSelect.className = "answer-choice-select";
      choiceSelect.setAttribute("aria-label", "Choose");
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Choose";
      placeholder.disabled = true;
      placeholder.selected = true;
      choiceSelect.append(placeholder);
      options.choices.slice(0, 3).forEach(choice => {
        if (!choice?.label || !choice?.prompt) return;
        const option = document.createElement("option");
        option.value = choice.prompt;
        option.textContent = choice.label;
        choiceSelect.append(option);
      });
      if (choiceSelect.options.length > 1) article.append(choiceSelect);
    }

    if (options.destination?.url) {
      const action = document.createElement("a");
      action.className = "chat-destination";
      action.dataset.mockUrl = options.destination.url;
      const baseHref = window.FortuneMockSite.hrefFor(options.destination.url);
      const connector = String(baseHref).includes("?") ? "&" : "?";
      action.href = `${baseHref}${connector}open=1`;
      action.textContent = options.destination.title || "Go to the next page";
      article.append(action);
    }

    transcript.append(article);
    if (options.revealStart) revealResponse(article);
    else scrollConversation();
    return article;
  }

  function distinctDestination(data) {
    const currentUrl = window.FortuneMockSite.canonicalUrl(currentPage()?.url);
    const sourceRows = Array.isArray(data?.sources) ? data.sources : [];
    const relatedRows = Array.isArray(data?.related) ? data.related : [];
    const rows = ["site", "staff"].includes(data?.retrieval_scope)
      ? [...sourceRows, ...relatedRows]
      : [...relatedRows, ...sourceRows];
    const found = rows.find(row => row?.url && window.FortuneMockSite.canonicalUrl(row.url) !== currentUrl && window.FortuneMockSite.isKnown(row.url));
    if (found) {
      const title = Core.destinationLabel(found.title);
      return { url: found.url, title };
    }
    return null;
  }

  function appendTurn(turn, editable = false) {
    const userArticle = appendMessage("user", turn.question, { editable });
    const payload = storedPayload(turn.payload, turn.answer);
    const destination = payload.choices.length ? null : distinctDestination(payload);
    const assistantArticle = appendMessage("assistant", turn.answer, {
      choices: payload.choices,
      destination,
      scope: payload.retrieval_scope,
      modelCalled: payload.model_called,
    });
    return { question: turn.question, answer: turn.answer, userArticle, assistantArticle };
  }

  function renderConversation() {
    transcript.replaceChildren();
    latestTurn = null;
    turns.forEach((turn, index) => {
      const rendered = appendTurn(turn, index === turns.length - 1);
      if (index === turns.length - 1) latestTurn = rendered;
    });
    if (turns.length) panel.classList.add("is-expanded");
    else panel.classList.remove("is-expanded");
    if (turns.length) suggestions.replaceChildren();
    resetButton.hidden = !turns.length;
  }

  function restoreConversation() {
    const storage = conversationStorage();
    if (!storage) return false;
    try {
      const saved = JSON.parse(storage.getItem(CONVERSATION_STORAGE_KEY) || "null");
      if (saved?.version !== 1 || !Array.isArray(saved.turns)) return false;
      const restored = saved.turns.slice(-MAX_CONTEXT_EXCHANGES).map(storedTurn);
      if (!restored.length || restored.some(turn => !turn)) {
        clearPersistedConversation();
        return false;
      }
      turns = restored;
      history = turns.flatMap(turn => [
        { role: "user", content: turn.question },
        { role: "assistant", content: turn.answer },
      ]).slice(-MAX_CONTEXT_MESSAGES);
      conversationId = /^[0-9a-f-]{36}$/i.test(String(saved.conversationId || ""))
        ? String(saved.conversationId)
        : "";
      conversationToken = /^[A-Za-z0-9_-]{32,128}$/.test(String(saved.conversationToken || ""))
        ? String(saved.conversationToken)
        : "";
      renderConversation();
      updateContextWindow();
      return true;
    } catch {
      clearPersistedConversation();
      return false;
    }
  }

  function renderSuggestions(starter) {
    suggestions.replaceChildren();
    (starter?.suggestions || []).slice(0, 2).forEach(prompt => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.prompt = prompt;
      button.textContent = Core.suggestionLabel(prompt);
      suggestions.append(button);
    });
  }

  function resetForPage(page, starter) {
    if (!page) return;
    activePageId = page.id;
    endEditing({ clearInput: true });
    pendingClientEventId = "";
    pendingQuestion = "";
    updateContextWindow();
    panel.classList.toggle("is-expanded", turns.length > 0);
    title.textContent = "Website Guide";
    questionField.placeholder = "Ask about this page";
    if (turns.length) suggestions.replaceChildren();
    else renderSuggestions(starter);
  }

  function resetConversation() {
    if (answering) return;
    endEditing({ clearInput: true });
    history = [];
    turns = [];
    latestTurn = null;
    pendingClientEventId = "";
    pendingQuestion = "";
    conversationId = "";
    conversationToken = "";
    clearPersistedConversation();
    renderConversation();
    renderSuggestions(window.FortuneMockSite.getStarter(currentPage()));
    updateContextWindow();
    questionField.focus({ preventScroll: true });
  }

  function setEditStatus(message = "") {
    editStatus.textContent = message;
    editStatus.hidden = !message;
  }

  function setBusy(value) {
    answering = value;
    if (value) panel.classList.add("is-expanded");
    submitButton.disabled = value;
    questionField.readOnly = value;
    editCancel.disabled = value;
    resetButton.disabled = value;
    transcript.querySelectorAll(".chat-edit-button").forEach(button => { button.disabled = value; });
    transcript.querySelectorAll(".answer-choice-select").forEach(select => { select.disabled = value; });
    panel.setAttribute("aria-busy", String(value));
    submitButton.textContent = value ? "Sending…" : editTarget ? "Update" : "Send";
  }

  function endEditing(options = {}) {
    editTarget?.userArticle?.classList.remove("is-editing");
    editTarget = null;
    form.classList.remove("is-editing");
    editCancel.hidden = true;
    setEditStatus();
    questionLabel.textContent = "Question";
    if (options.clearInput) {
      questionField.value = "";
      resizeQuestionField();
    }
    if (!answering) submitButton.textContent = "Send";
  }

  function startEditing(userArticle) {
    if (!latestTurn || latestTurn.userArticle !== userArticle || answering) return;
    editTarget?.userArticle?.classList.remove("is-editing");
    editTarget = latestTurn;
    pendingClientEventId = "";
    pendingQuestion = "";
    userArticle.classList.add("is-editing");
    form.classList.add("is-editing");
    editCancel.hidden = false;
    setEditStatus();
    questionLabel.textContent = "Edit question";
    questionField.value = latestTurn.question;
    resizeQuestionField();
    submitButton.textContent = "Update";
    questionField.focus({ preventScroll: true });
    questionField.setSelectionRange(questionField.value.length, questionField.value.length);
  }

  function privacyHold(editing = false) {
    pendingClientEventId = "";
    pendingQuestion = "";
    questionField.value = "";
    resizeQuestionField();

    if (editing) {
      setEditStatus("Not sent. Remove personal information.");
      return;
    }

    suggestions.replaceChildren();
    appendMessage("user", "Not sent");
    appendMessage(
      "assistant",
      "Remove personal information and try again.",
      {
        destination: distinctDestination({ related: [{ title: "Contact", url: CONTACT_URL }] }),
        scope: "staff",
        revealStart: true,
      },
    );
    history = [];
    turns = [];
    latestTurn = null;
    conversationId = "";
    conversationToken = "";
    clearPersistedConversation();
    transcript.querySelectorAll(".chat-message-actions").forEach(actions => actions.remove());
    updateContextWindow();
  }

  async function remoteAnswer(question, clientEventId, options = {}) {
    const response = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: question,
        history: options.history || history,
        page_context: pageContext(),
        client_surface: "replica",
        client_event_id: clientEventId,
        conversation_id: options.startNew ? undefined : conversationId || undefined,
        conversation_token: options.startNew ? undefined : conversationToken || undefined,
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error) {
      const error = new Error(data.error || "The live model could not answer.");
      error.payload = data;
      error.status = response.status;
      throw error;
    }
    if (data.kind !== "privacy" && (
      !["answer", "clarify", "handoff"].includes(data.kind)
      || data.model_called !== true
      || !cleanText(data.message)
    )) {
      const error = new Error("The guide returned an invalid response.");
      error.payload = data;
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function warmModel() {
    if (warmupPromise) return warmupPromise;
    warmupPromise = (async () => {
      try {
        const response = await fetch(apiUrl("/api/warmup"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (!response.ok) throw new Error("Model warm-up failed");
        const data = await response.json();
        if (data.status !== "ready") throw new Error("Model warm-up unavailable");
        modelStatus.textContent = "Ready";
        modelStatus.classList.add("model-ready");
        return data;
      } catch {
        modelStatus.textContent = modelReady ? "Ready" : "Unavailable";
        return null;
      } finally {
        warmupPromise = null;
      }
    })();
    return warmupPromise;
  }

  function showAnswer(data) {
    suggestions.replaceChildren();
    const destination = Array.isArray(data?.choices) && data.choices.length
      ? null
      : distinctDestination(data);
    return appendMessage("assistant", data.message, {
      choices: data.choices,
      destination,
      scope: data.retrieval_scope || (data.sources?.some(source => source.url === currentPage()?.url) ? "page" : "site"),
      modelCalled: data.model_called === true,
      revealStart: true,
    });
  }

  function showUnavailable(message = "Guide unavailable. Try again.") {
    setEditStatus(message);
  }

  function requestFailureMessage(error, editing = false) {
    const status = Number(error?.status || 0);
    if (status === 409 && error?.payload?.idempotency_complete === false) {
      return editing ? "Still working. Try again or cancel." : "Still working. Try again.";
    }
    if (error?.payload?.idempotency_complete === true) {
      return editing ? "Try again or cancel." : "Try again.";
    }
    if (status === 429) {
      return editing ? "Guide busy. Try again shortly or cancel." : "Guide busy. Try again shortly.";
    }
    if (status === 502) {
      return editing ? "Try rephrasing or cancel." : "Try rephrasing.";
    }
    if (editing && status && status !== 503) return "Couldn’t update. Try again or cancel.";
    return editing ? "Guide unavailable. Try again or cancel." : "Guide unavailable. Try again.";
  }

  async function ask(question, options = {}) {
    const value = cleanText(question);
    if (!value || answering) return;
    const restoreComposerFocus = options.restoreFocus || form.contains(document.activeElement);
    questionField.value = "";
    resizeQuestionField();

    if (personalInformationDetected(value)) {
      privacyHold(Boolean(editTarget));
      return;
    }

    const safeQuestion = redactSixDigitValues(value);
    const editing = editTarget;
    const requestHistory = editing ? Core.historyBeforeLatestExchange(history) : history;
    if (pendingQuestion !== safeQuestion || !pendingClientEventId) {
      pendingQuestion = safeQuestion;
      pendingClientEventId = window.crypto.randomUUID();
    }
    suggestions.replaceChildren();
    setBusy(true);
    try {
      if (!apiReady) {
        await checkHealth();
        if (!apiReady) throw new Error("The guide backend is unavailable.");
      }
      const data = await remoteAnswer(safeQuestion, pendingClientEventId, {
        history: requestHistory,
        startNew: Boolean(editing),
      });
      if (data.kind === "privacy") {
        privacyHold(Boolean(editing));
        return;
      }
      if (editing) {
        let node = editing.userArticle;
        while (node) {
          const next = node.nextSibling;
          node.remove();
          node = next;
        }
        endEditing();
      }
      const userArticle = appendMessage("user", safeQuestion, { editable: true });
      const answer = redactSixDigitValues(data.message || "");
      const assistantArticle = showAnswer(data);
      history = [...requestHistory, { role: "user", content: safeQuestion }, { role: "assistant", content: answer }]
        .slice(-MAX_CONTEXT_MESSAGES);
      const turn = { question: safeQuestion, answer, payload: storedPayload(data, answer) };
      turns = (editing ? turns.slice(0, -1).concat(turn) : turns.concat(turn))
        .slice(-MAX_CONTEXT_EXCHANGES);
      resetButton.hidden = false;
      latestTurn = { question: safeQuestion, answer, userArticle, assistantArticle };
      conversationId = String(data.conversation_id || (editing ? "" : conversationId));
      conversationToken = String(data.conversation_token || (editing ? "" : conversationToken));
      updateContextWindow();
      pendingClientEventId = "";
      pendingQuestion = "";
      setEditStatus();
      persistConversation();
    } catch (error) {
      questionField.value = value;
      resizeQuestionField();
      const retryInProgress = Number(error?.status || 0) === 409
        && error?.payload?.idempotency_complete === false;
      if (error?.payload && !retryInProgress) {
        pendingClientEventId = "";
        pendingQuestion = "";
      }
      if (![409, 429, 502].includes(Number(error?.status || 0))) {
        apiReady = false;
        modelReady = false;
        modelStatus.textContent = "Unavailable";
        modelStatus.classList.remove("model-ready");
      }
      if (editing) {
        setEditStatus(requestFailureMessage(error, true));
      } else {
        showUnavailable(requestFailureMessage(error));
      }
    } finally {
      setBusy(false);
      if (restoreComposerFocus && !panel.hidden) questionField.focus({ preventScroll: true });
    }
  }

  async function checkHealth() {
    try {
      const response = await fetch(apiUrl("/health"), { cache: "no-store" });
      if (!response.ok || !String(response.headers.get("content-type") || "").includes("application/json")) throw new Error("No model backend");
      const data = await response.json();
      apiReady = true;
      modelReady = Boolean(data.model_enabled);
      captureMode = ["none", "metadata", "transcript"].includes(data.conversation_logging?.capture_mode)
        ? data.conversation_logging.capture_mode
        : "none";
      privacyCopy.textContent = captureMode === "transcript"
        ? "Recorded for team review. Don’t include personal information."
        : "Don’t include personal information.";
      contextWindowCopy.textContent = captureMode === "transcript"
        ? "Questions and answers are recorded for team review."
        : captureMode === "metadata"
          ? "This review build stores IDs and response data. Chat stays in this tab across pages."
          : "Up to 3 exchanges stay in this tab across pages.";
      modelStatus.textContent = modelReady ? "Starting…" : "Unavailable";
      modelStatus.classList.toggle("model-ready", modelReady);
      if (modelReady) warmModel();
    } catch {
      apiReady = false;
      modelReady = false;
      captureMode = "none";
      privacyCopy.textContent = "Don’t include personal information.";
      modelStatus.textContent = "Unavailable";
      modelStatus.classList.remove("model-ready");
    }
  }

  toggle.addEventListener("click", openGuide);
  closeButton.addEventListener("click", closeGuide);
  form.addEventListener("submit", event => {
    event.preventDefault();
    ask(questionField.value);
  });
  questionField.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    form.requestSubmit();
  });
  questionField.addEventListener("input", resizeQuestionField);
  suggestions.addEventListener("click", event => {
    const button = event.target.closest("[data-prompt]");
    if (!button) return;
    ask(button.dataset.prompt, { restoreFocus: event.detail === 0 });
  });
  transcript.addEventListener("click", event => {
    const editButton = event.target.closest(".chat-edit-button");
    if (editButton) {
      startEditing(editButton.closest(".chat-message.user"));
      return;
    }
    const button = event.target.closest("[data-prompt]");
    if (button) ask(button.dataset.prompt, { restoreFocus: event.detail === 0 });
  });
  transcript.addEventListener("change", event => {
    const select = event.target.closest(".answer-choice-select");
    if (!select?.value) return;
    const prompt = select.value;
    select.value = "";
    ask(prompt, { restoreFocus: true });
  });
  editCancel.addEventListener("click", () => {
    pendingClientEventId = "";
    pendingQuestion = "";
    endEditing({ clearInput: true });
  });
  resetButton.addEventListener("click", resetConversation);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) closeGuide();
  });
  window.addEventListener("fortune:pagechange", event => {
    if (event.detail?.page?.id === activePageId) return;
    resetForPage(event.detail.page, event.detail.starter);
  });

  window.FortuneMockSite.ready.then(page => {
    if (page.id !== activePageId) resetForPage(page, window.FortuneMockSite.getStarter(page));
    restoreConversation();
    checkHealth();
    const search = new URLSearchParams(window.location.search);
    if (search.get("open") === "1") openGuide({ moveFocus: false });
  });

  window.FortuneGuide = Object.freeze({
    ask,
    open: openGuide,
    close: closeGuide,
    reset: resetConversation,
    privacyDetected: personalInformationDetected,
    state: () => ({
      apiReady,
      modelReady,
      captureMode,
      conversationId,
      pendingClientEventId,
      activePageId,
      answering,
      historyLength: history.length,
      turnCount: turns.length,
      latestQuestion: latestTurn?.question || "",
      editing: Boolean(editTarget),
      contextExchanges: contextExchangeCount(),
    }),
  });
})();
