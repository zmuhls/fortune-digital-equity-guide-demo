(() => {
  "use strict";

  const Core = window.FortuneGuideCore;
  if (!Core) throw new Error("FortuneGuideCore must load before app.js");
  const panel = document.querySelector("#guide-panel");
  const toggle = document.querySelector("#guide-toggle");
  const closeButton = document.querySelector("#guide-close");
  const title = document.querySelector("#guide-title");
  const guideBody = document.querySelector("#guide-body");
  const transcript = document.querySelector("#chat-transcript");
  const suggestions = document.querySelector("#chat-suggestions");
  const form = document.querySelector("#question-form");
  const questionField = document.querySelector("#question");
  const submitButton = form.querySelector('button[type="submit"]');
  const modelStatus = document.querySelector("#model-status");
  const viewerFilter = document.querySelector("#viewer-filter");
  const viewerModeField = document.querySelector("#viewer-mode");
  const API_BASE = String(window.FORTUNE_GUIDE_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
  const CONTACT_URL = "https://www.fortunedigitalequity.org/contact";
  const TRAININGS_URL = "https://www.fortunedigitalequity.org/trainings";
  const requestedViewerMode = new URLSearchParams(window.location.search).get("view");
  const viewerMode = Core.viewerMode(window.location.hostname, requestedViewerMode);
  const isAdminView = viewerMode === "admin";

  let history = [];
  let modelReady = false;
  let answering = false;
  let activePageId = "";
  let warmupPromise = null;
  let activeModelName = "glm-5.2";
  let indexedPages = 184;

  document.documentElement.dataset.viewerMode = viewerMode;
  viewerModeField.value = viewerMode;
  viewerFilter.hidden = !isAdminView;

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

  function openGuide() {
    panel.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    toggle.setAttribute("aria-expanded", "true");
    toggle.hidden = true;
  }

  function closeGuide() {
    panel.hidden = true;
    panel.setAttribute("aria-hidden", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.hidden = false;
    toggle.focus();
  }

  function scrollConversation() {
    requestAnimationFrame(() => guideBody.scrollTo({ top: guideBody.scrollHeight, behavior: "smooth" }));
  }

  function revealResponse(article) {
    panel.classList.add("is-expanded");
    requestAnimationFrame(() => {
      const articleRect = article.getBoundingClientRect();
      const bodyRect = guideBody.getBoundingClientRect();
      const top = guideBody.scrollTop + articleRect.top - bodyRect.top;
      guideBody.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    });
  }

  function appendMessage(role, message, options = {}) {
    const article = document.createElement("article");
    article.className = `chat-message ${role}`;
    const label = document.createElement("p");
    label.className = "chat-speaker";
    label.textContent = role === "user" ? "You" : "Digital Equity guide";
    const safeMessage = redactSixDigitValues(cleanText(message));
    let body;
    if (role === "assistant") {
      const presentation = Core.answerPresentation(safeMessage);
      body = document.createElement("div");
      body.className = "chat-copy chat-answer";
      const presentationWords = presentation.text.split(/\s+/).filter(Boolean).length;
      const structured = Boolean(
        presentation.lead
        || presentation.notice
        || presentation.points[0]?.label
        || (presentation.points.length > 1 && presentationWords > 32)
      );
      if (presentation.lead) {
        const lead = document.createElement("p");
        lead.className = "answer-lead";
        lead.textContent = presentation.lead;
        body.append(lead);
      }
      if (structured) {
        const list = document.createElement("ul");
        list.className = "answer-list";
        presentation.points.forEach(point => {
          const item = document.createElement("li");
          if (point.label) {
            const strong = document.createElement("strong");
            strong.textContent = `${point.label}:`;
            item.append(strong, document.createTextNode(` ${point.text}`));
          } else {
            item.textContent = point.text;
          }
          list.append(item);
        });
        if (presentation.points.length) body.append(list);
      } else {
        const paragraph = document.createElement("p");
        paragraph.textContent = presentation.text;
        body.append(paragraph);
      }
      if (presentation.notice) {
        const note = document.createElement("p");
        note.className = "answer-note";
        note.textContent = presentation.notice;
        body.append(note);
      }
    } else {
      body = document.createElement("p");
      body.className = "chat-copy";
      body.textContent = safeMessage;
    }
    article.append(label, body);

    if (Array.isArray(options.choices) && options.choices.length) {
      const choiceList = document.createElement("div");
      choiceList.className = "answer-choices";
      options.choices.slice(0, 3).forEach(choice => {
        if (!choice?.label || !choice?.prompt) return;
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.prompt = choice.prompt;
        button.textContent = choice.label;
        choiceList.append(button);
      });
      article.append(choiceList);
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

    const sourceRows = Array.isArray(options.sources) ? options.sources.filter(source => source?.url && source?.title) : [];
    if (sourceRows.length) {
      const details = document.createElement("details");
      details.className = "chat-sources";
      const summary = document.createElement("summary");
      const scope = options.scope === "page"
        ? "Source on this page"
        : options.scope === "staff"
          ? "Staff route"
          : "Website sources";
      summary.textContent = sourceRows.length === 1 ? scope : `${scope} (${sourceRows.length})`;
      const list = document.createElement("ul");
      sourceRows.slice(0, 3).forEach(source => {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.dataset.mockUrl = source.url;
        link.href = window.FortuneMockSite.hrefFor(source.url);
        link.textContent = source.title;
        item.append(link);
        list.append(item);
      });
      details.append(summary, list);
      article.append(details);
    }

    transcript.append(article);
    if (options.revealStart) revealResponse(article);
    else if (options.preserveTop) {
      requestAnimationFrame(() => guideBody.scrollTo({ top: 0, behavior: "auto" }));
    }
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
    const fallback = currentUrl === CONTACT_URL ? TRAININGS_URL : CONTACT_URL;
    return { url: fallback, title: currentUrl === CONTACT_URL ? "Go to current trainings" : "Contact Digital Equity staff" };
  }

  function renderSuggestions(starter) {
    suggestions.replaceChildren();
    (starter?.suggestions || []).slice(0, 2).forEach(prompt => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.prompt = prompt;
      button.textContent = prompt;
      suggestions.append(button);
    });
  }

  function resetForPage(page, starter) {
    if (!page) return;
    activePageId = page.id;
    history = [];
    panel.classList.remove("is-expanded");
    transcript.replaceChildren();
    title.textContent = starter.heading;
    questionField.placeholder = starter.placeholder;
    renderSuggestions(starter);
    const pageTitle = window.FortuneMockSite.cleanTitle(page.title);
    let greeting = `You’re on ${pageTitle}. Ask about this page, or tell me what you’re trying to do and I’ll take you to the right section.`;
    if (starter.family === "archive") greeting = `This is a historical page. Tell me what current information you need and I’ll take you to the right section.`;
    if (starter.family === "excluded") greeting = `This route is not reproduced in the public demo. Tell me what current information you need and I’ll take you to a public section.`;
    appendMessage("assistant", greeting, { preserveTop: true });
  }

  function setBusy(value) {
    answering = value;
    if (value) panel.classList.add("is-expanded");
    submitButton.disabled = value;
    questionField.disabled = value;
    panel.setAttribute("aria-busy", String(value));
    submitButton.textContent = value ? "Checking…" : "Ask";
  }

  function renderModelStatus(phase, options = {}) {
    activeModelName = cleanText(options.model || activeModelName) || "glm-5.2";
    indexedPages = Number(options.pages) || indexedPages;
    modelStatus.classList.toggle("model-ready", isAdminView && phase === "ready");
    if (!isAdminView) {
      modelStatus.textContent = `Source guide · ${indexedPages} public pages`;
      return;
    }
    if (phase === "ready") modelStatus.textContent = `${activeModelName} · ready`;
    else if (phase === "preparing") modelStatus.textContent = `Preparing ${activeModelName}…`;
    else if (phase === "page-first") modelStatus.textContent = `${activeModelName} · page-first`;
    else if (phase === "unavailable") modelStatus.textContent = "Source guide · model unavailable";
    else modelStatus.textContent = `Source guide · ${indexedPages} public pages`;
  }

  function privacyHold() {
    suggestions.replaceChildren();
    appendMessage("user", "[Personal information removed]");
    appendMessage(
      "assistant",
      "We removed that entry before it left this browser. Please ask again without your six-digit Fortune ID, name, contact details, case information, health information, or other personally identifiable information.",
      {
        destination: distinctDestination({ related: [{ title: "Contact Digital Equity staff", url: CONTACT_URL }] }),
        scope: "staff",
        revealStart: true,
      },
    );
    history = [];
  }

  async function remoteAnswer(question) {
    if (warmupPromise) {
      try {
        await warmupPromise;
      } catch {
        // The chat request can still succeed when a preload attempt fails.
      }
    }
    const response = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, history, page_context: pageContext() }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "The live model could not answer.");
    return data;
  }

  async function warmModel(modelName, pages) {
    try {
      const response = await fetch(apiUrl("/api/warmup"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) throw new Error("Model warm-up failed");
      const data = await response.json();
      if (data.status !== "ready") throw new Error("Model warm-up unavailable");
      renderModelStatus("ready", { model: data.model || modelName, pages });
      return data;
    } catch {
      renderModelStatus(modelReady ? "page-first" : "source", { model: modelName, pages });
      return null;
    }
  }

  function showAnswer(data) {
    suggestions.replaceChildren();
    const destination = distinctDestination(data);
    appendMessage("assistant", data.message || "The website does not contain an approved answer. Please contact Digital Equity staff.", {
      choices: data.choices,
      destination,
      sources: data.sources,
      scope: data.retrieval_scope || (data.sources?.some(source => source.url === currentPage()?.url) ? "page" : "site"),
      revealStart: true,
    });
  }

  async function ask(question) {
    const value = cleanText(question);
    if (!value || answering) return;
    questionField.value = "";

    if (personalInformationDetected(value)) {
      privacyHold();
      return;
    }

    const safeQuestion = redactSixDigitValues(value);
    appendMessage("user", safeQuestion);
    suggestions.replaceChildren();
    setBusy(true);
    try {
      let data;
      if (modelReady) {
        try {
          data = await remoteAnswer(safeQuestion);
        } catch {
          data = window.FortuneMockSite.staticAnswer(safeQuestion, currentPage());
          renderModelStatus("unavailable");
          modelReady = false;
        }
      } else {
        data = window.FortuneMockSite.staticAnswer(safeQuestion, currentPage());
      }
      const displayedAnswer = Core.answerPresentation(redactSixDigitValues(data.message || "")).text;
      history.push({ role: "user", content: safeQuestion }, { role: "assistant", content: displayedAnswer });
      history = history.slice(-6);
      showAnswer(data);
    } finally {
      setBusy(false);
    }
  }

  async function checkHealth() {
    try {
      const response = await fetch(apiUrl("/health"), { cache: "no-store" });
      if (!response.ok || !String(response.headers.get("content-type") || "").includes("application/json")) throw new Error("No model backend");
      const data = await response.json();
      modelReady = Boolean(data.model_enabled);
      const pages = Number(data.indexed_pages) || Number(window.FortuneMockSite.getIndex()?.unique_urls) || 184;
      renderModelStatus(modelReady ? "preparing" : "source", { model: data.model, pages });
      if (modelReady) warmupPromise = warmModel(data.model, pages);
    } catch {
      const pages = Number(window.FortuneMockSite.getIndex()?.unique_urls) || 184;
      modelReady = false;
      renderModelStatus("source", { pages });
    }
  }

  viewerModeField.addEventListener("change", () => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", viewerModeField.value);
    window.location.assign(url.href);
  });
  toggle.addEventListener("click", openGuide);
  closeButton.addEventListener("click", closeGuide);
  form.addEventListener("submit", event => {
    event.preventDefault();
    ask(questionField.value);
  });
  suggestions.addEventListener("click", event => {
    const button = event.target.closest("[data-prompt]");
    if (button) ask(button.dataset.prompt);
  });
  transcript.addEventListener("click", event => {
    const button = event.target.closest("[data-prompt]");
    if (button) ask(button.dataset.prompt);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) closeGuide();
  });
  window.addEventListener("fortune:pagechange", event => {
    if (event.detail?.page?.id === activePageId) return;
    resetForPage(event.detail.page, event.detail.starter);
  });

  window.FortuneMockSite.ready.then(page => {
    if (page.id !== activePageId) resetForPage(page, window.FortuneMockSite.getStarter(page));
    checkHealth();
    if (new URLSearchParams(window.location.search).get("open") === "1") openGuide();
  });

  window.FortuneGuide = Object.freeze({
    ask,
    open: openGuide,
    close: closeGuide,
    privacyDetected: personalInformationDetected,
    state: () => ({ modelReady, viewerMode, activePageId, answering, historyLength: history.length }),
  });
})();
