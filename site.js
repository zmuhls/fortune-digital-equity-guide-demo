(() => {
  "use strict";

  const Core = window.FortuneGuideCore;
  if (!Core) throw new Error("FortuneGuideCore must load before site.js");
  const SITE_ORIGIN = "https://www.fortunedigitalequity.org";
  const CONTACT_URL = `${SITE_ORIGIN}/contact`;
  const TRAININGS_URL = `${SITE_ORIGIN}/trainings`;
  const CALENDAR_URL = `${SITE_ORIGIN}/calendar`;
  const DEVICES_URL = `${SITE_ORIGIN}/devices`;
  const INDIVIDUAL_URL = `${SITE_ORIGIN}/individual`;
  const PRACTICE_URL = `${SITE_ORIGIN}/practice`;
  const RESERVE_URL = `${SITE_ORIGIN}/reserve`;
  const ASSET_BASE = String(window.FORTUNE_ASSET_BASE || "");
  const STATIC_ROUTES = Boolean(window.FORTUNE_STATIC_ROUTES);
  const BOT_MESSAGE_WORD_LIMIT = 48;
  const REQUESTED_VIEWER_MODE = String(new URLSearchParams(window.location.search).get("view") || "").toLowerCase();
  const VIEWER_OVERRIDE = ["admin", "public"].includes(REQUESTED_VIEWER_MODE) ? REQUESTED_VIEWER_MODE : "";

  const state = {
    index: null,
    pages: [],
    byUrl: new Map(),
    current: null,
  };
  const memberSignedOut = document.querySelector("#member-signed-out");
  const memberProfile = document.querySelector("#member-profile");

  const BOILERPLATE = [
    /double click on the text box/i,
    /this space is a great opportunity/i,
    /every website has a story/i,
    /Description UNDER DEVELOPMENT/i,
    /use tab to navigate/i,
  ];

  function canonicalUrl(value) {
    return Core.canonicalUrl(value);
  }

  function safeMemberUrl(value, fallback = `${SITE_ORIGIN}/members`) {
    try {
      const url = new URL(String(value || fallback), SITE_ORIGIN);
      if (url.protocol !== "https:" || !/^(?:www\.)?fortunedigitalequity\.org$/i.test(url.hostname)) return fallback;
      return url.href;
    } catch {
      return fallback;
    }
  }

  function setMemberState(context = {}) {
    const signedIn = Boolean(context?.signedIn);
    memberSignedOut.hidden = signedIn;
    memberProfile.hidden = !signedIn;
    if (signedIn) memberProfile.href = safeMemberUrl(context.profileUrl);
  }

  function pathFor(value) {
    return Core.pathFor(value);
  }

  function cleanText(value) {
    return Core.cleanText(value);
  }

  function cleanTitle(value) {
    return Core.cleanTitle(value);
  }

  function clipWords(value, limit = 48) {
    const words = cleanText(value).split(/\s+/).filter(Boolean);
    if (words.length <= limit) return words.join(" ");
    return `${words.slice(0, limit).join(" ").replace(/[,;:]$/, "")}…`;
  }

  function usefulBlocks(page) {
    if (!page || page.authority === "excluded" || Number(page.status) !== 200) return [];
    const title = cleanTitle(page.title).toLowerCase();
    const seen = new Set();
    return (Array.isArray(page.blocks) ? page.blocks : [])
      .map(block => cleanText(block)
        .replace(/^Home Service list\s+/i, "")
        .replace(/\bUpcoming Sessions All Locations\b/gi, "")
        .replace(/\bLoading (?:days|times|availability)\b/gi, "")
        .replace(/\bBook Now\b/gi, "")
        .trim())
      .filter(block => block.length >= 34)
      .filter(block => !BOILERPLATE.some(pattern => pattern.test(block)))
      .map(block => block.replace(new RegExp(`^${escapeRegExp(title)}\\s+`, "i"), ""))
      .map(block => block.replace(/^.*?\bDescription\s+/i, ""))
      .filter(block => {
        const key = block.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function pageFamily(page) {
    return Core.pageFamily(page);
  }

  function normalizeTokens(value) {
    return Core.normalizeTokens(value);
  }

  function blockForQuestion(page, question) {
    const blocks = usefulBlocks(page);
    if (!blocks.length) return "";
    const queryTokens = [...new Set(normalizeTokens(question))];
    const ranked = blocks.map((block, index) => {
      const value = block.normalize("NFKD").toLowerCase();
      let score = queryTokens.reduce((total, token) => total + (value.includes(token) ? 2 : 0), 0);
      if (/\bcover|learn|class|workshop\b/i.test(question) && /\bclass|learn|cover|skill|workshop\b/i.test(block)) score += 5;
      return { block, index, score };
    }).sort((a, b) => b.score - a.score || a.index - b.index);
    return ranked[0].score > 0 ? ranked[0].block : blocks[0];
  }

  function evidenceFor(question, page) {
    return Core.evidenceFor(question, page);
  }

  function currentPageCanAnswer(question, page) {
    return Core.currentPageCanAnswer(question, page);
  }

  function rankPages(question, current) {
    return state.pages
      .filter(page => page.authority === "answer" && Number(page.status) === 200)
      .map(page => {
        const evidence = evidenceFor(question, page);
        return { page, score: evidence.score, genuine: evidence.genuine };
      })
      .filter(row => row.genuine && row.score > 0)
      .sort((a, b) => b.score - a.score || cleanTitle(a.page.title).localeCompare(cleanTitle(b.page.title)));
  }

  function fallbackDestination(question, current) {
    const value = cleanText(question).toLowerCase();
    const candidates = [];
    if (/device|laptop|cell ?phone|phone|lifeline/.test(value)) candidates.push(DEVICES_URL, INDIVIDUAL_URL, CONTACT_URL);
    else if (/date|time|schedule|when|where|calendar/.test(value)) candidates.push(CALENDAR_URL, TRAININGS_URL, CONTACT_URL);
    else if (/register|reserve|sign up|enroll/.test(value)) candidates.push(RESERVE_URL, CALENDAR_URL, CONTACT_URL);
    else if (/practice|exercise|assessment|quiz/.test(value)) candidates.push(PRACTICE_URL, TRAININGS_URL, CONTACT_URL);
    else if (/support|tutor|problem|fix|troubleshoot/.test(value)) candidates.push(INDIVIDUAL_URL, CONTACT_URL, TRAININGS_URL);
    else candidates.push(TRAININGS_URL, PRACTICE_URL, CONTACT_URL);
    return candidates.find(url => canonicalUrl(url) !== canonicalUrl(current?.url)) || CONTACT_URL;
  }

  function linkRecord(url, label) {
    const canonical = canonicalUrl(url);
    const page = state.byUrl.get(canonical);
    return {
      id: page?.id || "",
      title: label || cleanTitle(page?.title) || "Digital Equity information",
      url: canonical || CONTACT_URL,
    };
  }

  function ambiguityAnswer(question, current) {
    const compact = cleanText(question).toLowerCase().replace(/[?.!]/g, "");
    let message = "";
    let choices = [];
    if (["help", "i need help", "support", "i need support"].includes(compact)) {
      message = "What would you like help with: learning a skill, using a device, or reaching staff?";
      choices = [
        { label: "Learn a skill", prompt: "I want to learn a digital skill." },
        { label: "Use a device", prompt: "I need help using a device." },
        { label: "Reach staff", prompt: "I want to contact Digital Equity staff." },
      ];
    } else if (/^(?:a |the )?(?:device|computer|phone|laptop)$/.test(compact)) {
      message = "Do you need a device, help learning to use one, or help with a problem?";
      choices = [
        { label: "I need a device", prompt: "I want information about getting a device." },
        { label: "Learn to use it", prompt: "I want to learn how to use a device." },
        { label: "Solve a problem", prompt: "I need help with a device problem." },
      ];
    } else if (/^(?:a |the )?(?:class|classes|training|workshop|workshops)$/.test(compact)) {
      message = "Are you looking for beginner skills, job-related skills, or a particular topic?";
      choices = [
        { label: "Beginner skills", prompt: "I am looking for a beginner digital skills class." },
        { label: "Job-related skills", prompt: "I want a class related to work or job searching." },
        { label: "A topic", prompt: "I want to ask about a particular class topic." },
      ];
    }
    if (!message) return null;
    const destination = fallbackDestination(question, current);
    return {
      kind: "clarify",
      message,
      reason: "One detail will help the guide choose a useful page.",
      choices,
      sources: current?.authority === "answer" ? [linkRecord(current.url)] : [],
      related: [linkRecord(destination, "Continue to the most relevant section")],
      handoff_url: CONTACT_URL,
      model_called: false,
    };
  }

  function staticAnswer(question, current = state.current) {
    const ambiguous = ambiguityAnswer(question, current);
    if (ambiguous) return ambiguous;

    const localEvidence = currentPageCanAnswer(question, current);
    const ranked = localEvidence ? [] : rankPages(question, current);
    if (!localEvidence && !ranked.length) {
      const fallback = canonicalUrl(current?.url) === CONTACT_URL ? TRAININGS_URL : CONTACT_URL;
      return {
        kind: "handoff",
        message: "I could not find that information on this page or elsewhere in the approved Digital Equity website. Please ask Digital Equity staff.",
        reason: "The guide does not use unrelated pages or supply information that is absent from the public website.",
        choices: [],
        sources: [linkRecord(CONTACT_URL)],
        related: [linkRecord(fallback, fallback === CONTACT_URL ? "Contact Digital Equity staff" : "Go to current trainings")],
        handoff_url: CONTACT_URL,
        model_called: false,
        retrieval_scope: "staff",
      };
    }

    const scope = localEvidence ? "page" : "site";
    const best = localEvidence ? current : ranked[0].page;
    const blocks = usefulBlocks(best);
    const selectedBlock = blockForQuestion(best, question);
    const excerpt = clipWords(selectedBlock || best?.description || "The public Digital Equity pages list program information, classes, resources, and contact routes.", 28);
    const bestTitle = cleanTitle(best?.title);
    const onCurrentPage = scope === "page";
    let message = onCurrentPage ? `This page says: ${excerpt}` : `${bestTitle} is the closest public page. ${excerpt}`;

    const statusBlock = blocks.find(block => /\b(?:on hold|not available|ended|coming soon)\b/i.test(block));
    if (statusBlock && statusBlock !== selectedBlock) message += ` ${clipWords(statusBlock, 12)}`;

    const volatile = Boolean(best?.volatile) || /date|time|schedule|available|availability|eligible|eligibility|inventory|location|register|session/i.test(question);
    if (volatile) message += " Confirm current dates and availability on the live page or with staff.";

    let destination = ranked.map(row => row.page.url).find(url => canonicalUrl(url) !== canonicalUrl(current?.url));
    if (!destination) destination = fallbackDestination(question, current);
    if (canonicalUrl(destination) === canonicalUrl(current?.url)) destination = CONTACT_URL;

    const sourcePages = (scope === "page" ? [best] : [best, ...ranked.slice(1, 3).map(row => row.page)])
      .filter(Boolean)
      .filter((page, index, pages) => pages.findIndex(candidate => candidate.url === page.url) === index)
      .slice(0, 3);
    return {
      kind: "answer",
      message: clipWords(message, BOT_MESSAGE_WORD_LIMIT),
      reason: "The answer uses the current public site index for this page.",
      choices: [],
      sources: sourcePages.map(page => linkRecord(page.url)),
      related: [linkRecord(destination, `Go to ${cleanTitle(state.byUrl.get(canonicalUrl(destination))?.title)}`)],
      handoff_url: CONTACT_URL,
      model_called: false,
      retrieval_scope: scope,
    };
  }

  function selectedUrl() {
    if (window.FORTUNE_ROUTE_URL) return canonicalUrl(window.FORTUNE_ROUTE_URL);
    const fromQuery = new URLSearchParams(window.location.search).get("page");
    return canonicalUrl(fromQuery || SITE_ORIGIN);
  }

  function hrefFor(value) {
    const href = Core.hrefFor(value, {
      staticRoutes: STATIC_ROUTES,
      assetBase: ASSET_BASE,
      knownUrls: state.byUrl,
    });
    if (!VIEWER_OVERRIDE) return href;
    const url = new URL(href, window.location.href);
    url.searchParams.set("view", VIEWER_OVERRIDE);
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function renderPage(page) {
    state.current = page;
    const family = pageFamily(page);
    const title = cleanTitle(page?.title);
    const heading = document.querySelector("#page-heading");
    const loading = document.querySelector("#page-loading");
    const documentPanel = document.querySelector("#page-document");
    const summary = document.querySelector("#page-summary");
    const blocks = document.querySelector("#page-blocks");
    const liveLink = document.querySelector("#live-page-link");

    document.title = `${title} · Digital Equity guide demonstration`;
    document.body.dataset.page = pathFor(page.url);
    document.body.dataset.sourceUrl = page.url;
    heading.textContent = title;
    loading.hidden = true;
    documentPanel.hidden = false;
    blocks.replaceChildren();
    liveLink.href = page.url;

    if (family === "excluded") {
      summary.textContent = "This route is retained in the site inventory but is not reproduced in the public demonstration.";
      appendStatus(blocks, "Use the current public service directory or contact Digital Equity staff for help.");
    } else if (family === "archive") {
      summary.textContent = "This page contains historical information. Current services, dates, and registration may have changed.";
      appendStatus(blocks, "The guide can take you to the current calendar, training directory, or staff contact.");
    } else if (Number(page.status) !== 200) {
      summary.textContent = "The public index found this route but did not receive a complete page record.";
      appendStatus(blocks, "Use the live page, current training directory, or staff contact before relying on details.");
    } else {
      const pageBlocks = usefulBlocks(page);
      summary.textContent = clipWords(page.description || pageBlocks[0] || "Use the page guide to find the relevant Digital Equity information and next page.", 58);
      pageBlocks.slice(page.description ? 0 : 1, 5).forEach((text, index) => {
        const section = document.createElement("section");
        const paragraph = document.createElement("p");
        paragraph.textContent = clipWords(text, index === 0 ? 90 : 70);
        section.append(paragraph);
        blocks.append(section);
      });
    }

    document.querySelectorAll("[data-site-url]").forEach(link => {
      const url = canonicalUrl(link.dataset.siteUrl);
      link.href = hrefFor(url);
      link.toggleAttribute("aria-current", pathFor(url) === pathFor(page.url));
    });

    window.dispatchEvent(new CustomEvent("fortune:pagechange", { detail: { page, starter: Core.starterFor(page) } }));
  }

  function appendStatus(container, text) {
    const section = document.createElement("section");
    section.className = "page-status";
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    section.append(paragraph);
    container.append(section);
  }

  function navigate(value) {
    const canonical = canonicalUrl(value);
    const page = state.byUrl.get(canonical);
    if (!page) {
      window.location.assign(value);
      return;
    }
    if (STATIC_ROUTES) {
      window.location.assign(hrefFor(canonical));
      return;
    }
    window.history.pushState({ page: pathFor(canonical) }, "", hrefFor(canonical));
    renderPage(page);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function initialize() {
    const response = await fetch(`${ASSET_BASE}site-index.json`, { cache: "no-store" });
    if (!response.ok) throw new Error("The public page index could not be loaded.");
    state.index = await response.json();
    state.pages = Array.isArray(state.index.pages) ? state.index.pages : [];
    state.pages.forEach(page => state.byUrl.set(canonicalUrl(page.url), page));
    const page = state.byUrl.get(selectedUrl()) || state.byUrl.get(`${SITE_ORIGIN}/`) || state.pages[0];
    if (!page) throw new Error("No public page records are available.");
    renderPage(page);
    return page;
  }

  document.addEventListener("click", event => {
    const link = event.target.closest("a[data-site-url], a[data-mock-url]");
    if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (STATIC_ROUTES) return;
    const url = link.dataset.siteUrl || link.dataset.mockUrl;
    const canonical = canonicalUrl(url);
    if (!canonical || !state.byUrl.has(canonical)) return;
    event.preventDefault();
    navigate(canonical);
  });

  window.addEventListener("popstate", () => {
    if (STATIC_ROUTES) return;
    const page = state.byUrl.get(selectedUrl()) || state.byUrl.get(`${SITE_ORIGIN}/`);
    if (page) renderPage(page);
  });
  window.addEventListener("fortune:memberstate", event => setMemberState(event.detail));
  setMemberState(window.FORTUNE_MEMBER_CONTEXT);

  const ready = initialize().catch(error => {
    const loading = document.querySelector("#page-loading");
    if (loading) loading.textContent = error.message;
    throw error;
  });

  window.FortuneMockSite = Object.freeze({
    ready,
    canonicalUrl,
    cleanTitle,
    getCurrentPage: () => state.current,
    getIndex: () => state.index,
    getStarter: page => Core.starterFor(page || state.current),
    hrefFor,
    isKnown: value => state.byUrl.has(canonicalUrl(value)),
    navigate,
    setMemberState,
    staticAnswer,
  });
})();
