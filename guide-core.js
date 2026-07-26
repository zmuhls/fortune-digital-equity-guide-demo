(function attachFortuneGuideCore(root, factory) {
  "use strict";
  const core = Object.freeze(factory());
  if (typeof module === "object" && module.exports) module.exports = core;
  root.FortuneGuideCore = core;
})(typeof globalThis === "object" ? globalThis : window, function buildFortuneGuideCore() {
  "use strict";

  const SITE_ORIGIN = "https://www.fortunedigitalequity.org";
  const ROUTE_GROUPS = {
    directory: new Set([
      "/trainings", "/certifications", "/grow", "/practice", "/projects",
      "/opportunities", "/other", "/staff",
    ]),
    action: new Set(["/calendar", "/reserve", "/contact", "/assessments", "/deiqa", "/media"]),
    program: new Set(["/", "/about", "/about/impact", "/about/partners"]),
    support: new Set(["/devices", "/individual"]),
  };
  const STOPWORDS = new Set([
    "a", "about", "am", "an", "and", "are", "at", "be", "can", "do", "does",
    "for", "from", "get", "have", "help", "how", "i", "in", "is", "it", "me",
    "my", "of", "on", "or", "please", "the", "this", "to", "want", "what",
    "when", "where", "which", "with", "you", "your",
  ]);
  const DISPLAY_MESSAGE_WORD_LIMIT = 48;

  function canonicalUrl(value) {
    try {
      const url = new URL(String(value || ""), SITE_ORIGIN);
      if (!/^(?:www\.)?fortunedigitalequity\.org$/i.test(url.hostname)) return "";
      const path = url.pathname.replace(/\/+$/, "") || "/";
      return `${SITE_ORIGIN}${path}`;
    } catch {
      return "";
    }
  }

  function pathFor(value) {
    const canonical = canonicalUrl(value);
    return canonical ? new URL(canonical).pathname : "/";
  }

  function cleanText(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, " ")
      .replace(/\s+([,.;:!?])/g, "$1")
      .replace(/\s{2,}/g, " ")
      .replace(/(.{8,}?[.!?])(?:\s+\1)+/gi, "$1")
      .trim();
  }

  function cleanTitle(value) {
    const title = cleanText(value)
      .replace(/\s*[|·]\s*FS Digital Equity\s*$/i, "")
      .replace(/\s*[|·]\s*Digital Equity Program\s*$/i, "")
      .trim();
    return title || "Digital Equity";
  }

  function pageFamily(page) {
    const path = pathFor(page?.url);
    if (page?.authority === "excluded") return "excluded";
    if (page?.authority === "archive") return "archive";
    if (page?.authority === "navigation" || page?.sitemap_kind === "blog-categories") return "news";
    if (page?.sitemap_kind === "blog-posts") return "archive";
    if (page?.sitemap_kind === "booking-services" || path.startsWith("/service-page/")) return "service";
    for (const [family, paths] of Object.entries(ROUTE_GROUPS)) {
      if (paths.has(path)) return family;
    }
    if (path === "/events" || path.startsWith("/techfair")) return "event";
    return "program";
  }

  function starterFor(page) {
    const title = cleanTitle(page?.title);
    const path = pathFor(page?.url);
    const family = pageFamily(page);
    const common = {
      family,
      heading: `Ask about ${title}`,
      placeholder: "What do you need?",
      suggestions: ["What is the main information here?", "Where should I go next?"],
    };

    if (family === "service") return {
      ...common,
      placeholder: "What about this class?",
      suggestions: ["What does this class cover?", "What should I take before or after it?"],
    };
    if (family === "directory") return {
      ...common,
      placeholder: "What are you looking for?",
      suggestions: ["Help me choose an option", "What should I do next?"],
    };
    if (family === "support" && path === "/devices") return {
      ...common,
      placeholder: "Device or computer help?",
      suggestions: ["I need information about getting a device", "I need help using a device"],
    };
    if (family === "support") return {
      ...common,
      placeholder: "What help do you need?",
      suggestions: ["What individual support is available?", "Where can I check current hours?"],
    };
    if (family === "action" && path === "/calendar") return {
      ...common,
      placeholder: "Which class or date?",
      suggestions: ["Where and when are current classes?", "Which class should I look for?"],
    };
    if (family === "action" && path === "/reserve") return {
      ...common,
      placeholder: "How can we help you register?",
      suggestions: ["How does registration work?", "Where can I confirm current sessions?"],
    };
    if (family === "action" && path === "/contact") return {
      ...common,
      placeholder: "Who do you need to reach?",
      suggestions: ["How can I reach Digital Equity staff?", "Which page should I use first?"],
    };
    if (family === "action") return {
      ...common,
      placeholder: "What would you like to do?",
      suggestions: ["How do I use this page?", "Where can I confirm current information?"],
    };
    if (family === "event") return {
      ...common,
      placeholder: "What about this event?",
      suggestions: ["What does this event page describe?", "Where can I confirm current details?"],
    };
    if (family === "archive") return {
      ...common,
      heading: "Ask where to find current information",
      placeholder: "What current information do you need?",
      suggestions: ["Where is the current version?", "Take me to current Digital Equity information"],
    };
    if (family === "excluded") return {
      ...common,
      heading: "Find a current public page",
      placeholder: "What public information do you need?",
      suggestions: ["Take me to current services", "How do I contact Digital Equity staff?"],
    };
    if (family === "news") return {
      ...common,
      placeholder: "What current information do you need?",
      suggestions: ["Where are current programs listed?", "Take me to the current calendar"],
    };
    return {
      ...common,
      placeholder: "What would you like to do?",
      suggestions: ["What does the program offer?", "How can I get started?"],
    };
  }

  function normalizeTokens(value) {
    return (String(value || "").normalize("NFKD").toLowerCase().match(/[a-z0-9]+/g) || [])
      .filter(token => token.length > 1 && !STOPWORDS.has(token));
  }

  function searchableText(page) {
    return [page?.title, page?.description, ...(page?.headings || []), ...(page?.blocks || [])].join(" ");
  }

  function deicticPageQuestion(question) {
    const value = cleanText(question).toLowerCase();
    return /\b(?:this|the current)\s+(?:page|class|service|program|event|workshop)\b/.test(value)
      || /\b(?:on|from)\s+this\s+page\b/.test(value)
      || /\bwhat (?:does it|is here|should i take before or after it)\b/.test(value)
      || /\bwhere should i go next\b/.test(value)
      || /\bmain information here\b/.test(value);
  }

  function evidenceFor(question, page) {
    if (!page || page.authority !== "answer" || Number(page.status) !== 200) return { genuine: false, score: 0 };
    const queryTokens = [...new Set(normalizeTokens(question))];
    const haystack = searchableText(page).normalize("NFKD").toLowerCase();
    const title = cleanTitle(page.title).normalize("NFKD").toLowerCase();
    const matched = queryTokens.filter(token => haystack.includes(token));
    const titleMatch = matched.some(token => title.includes(token));
    let score = matched.length * 2 + (titleMatch ? 8 : 0);
    for (const token of matched) score += Math.min(haystack.split(token).length - 1, 7);
    const query = cleanText(question).toLowerCase();
    if (query.length > 5 && title.includes(query)) score += 20;
    return { genuine: matched.length >= 2 || titleMatch, score };
  }

  function currentPageCanAnswer(question, page) {
    if (!page || page.authority !== "answer" || Number(page.status) !== 200) return false;
    return deicticPageQuestion(question) || evidenceFor(question, page).genuine;
  }

  function normalizeDigits(value) {
    const ranges = [[0x0660, 0x0669], [0x06f0, 0x06f9], [0xff10, 0xff19]];
    return String(value || "").normalize("NFKC").replace(/\p{Nd}/gu, character => {
      const code = character.codePointAt(0);
      for (const [start, end] of ranges) {
        if (code >= start && code <= end) return String(code - start);
      }
      return character;
    });
  }

  function personalInformationDetected(value) {
    const normalized = normalizeDigits(value);
    const patterns = [
      /(?<!\d)\d{6}(?!\d)/,
      /(?<!\d)\d{3}[-. ]\d{3}(?!\d)/,
      /\b\d{3}[-. ]?\d{2}[-. ]?\d{4}\b/,
      /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i,
      /\b(?:my|their|participant'?s?)\s+(?:fortune\s+)?(?:id|case number|name|address|phone|email)\b/i,
      /\b(?:social security|ssn|date of birth|dob|password|passcode|my health|my diagnosis)\b/i,
      /\b(?:call me at|my number is|i live at)\b/i,
    ];
    return patterns.some(pattern => pattern.test(normalized));
  }

  function redactSixDigitValues(value) {
    return normalizeDigits(value)
      .replace(/(?<!\d)\d{3}[-. ]\d{3}(?!\d)/g, "[six-digit ID removed]")
      .replace(/(?<!\d)\d{6}(?!\d)/g, "[six-digit ID removed]");
  }

  function hrefFor(value, options = {}) {
    const canonical = canonicalUrl(value);
    const knownUrls = options.knownUrls;
    const isKnown = knownUrls && typeof knownUrls.has === "function" ? knownUrls.has(canonical) : true;
    if (!canonical || !isKnown) return value;
    const path = pathFor(canonical);
    if (!options.staticRoutes) return `?page=${encodeURIComponent(path)}`;
    const assetBase = String(options.assetBase || "");
    if (path === "/") return assetBase || "./";
    return `${assetBase}${path.replace(/^\//, "")}/`;
  }

  function clipWords(value, limit) {
    const words = cleanText(value).split(/\s+/).filter(Boolean);
    if (words.length <= limit) return words.join(" ");
    return `${words.slice(0, limit).join(" ").replace(/[,;:–—-]$/, "")}…`;
  }

  function clipAnswerPoint(value, limit) {
    const words = cleanText(value).split(/\s+/).filter(Boolean);
    for (let index = 0; index < Math.min(words.length - 1, limit - 1); index += 1) {
      if (/^coming$/i.test(words[index]) && /^soon[.!?]?$/i.test(words[index + 1])) {
        return `${words.slice(0, index + 2).join(" ").replace(/[.!?]+$/, "")}.`;
      }
    }
    return clipWords(value, limit);
  }

  function answerPresentation(value, limit = DISPLAY_MESSAGE_WORD_LIMIT) {
    let text = cleanText(value);
    const leadMatch = text.match(/^((?:On this page|This page says|The .{1,64}? page says)):\s*/i);
    const lead = leadMatch ? cleanText(leadMatch[1]) : "";
    if (leadMatch) text = text.slice(leadMatch[0].length);

    const sentences = (text.match(/[^.!?]+(?:[.!?]+|$)/g) || [text])
      .map(sentence => cleanText(sentence))
      .filter(Boolean);
    const noticeIndex = sentences.findIndex(sentence =>
      /\b(?:confirm|check|use)\b/i.test(sentence)
      && /\b(?:current details|live page|staff)\b/i.test(sentence));
    const notice = noticeIndex >= 0
      ? "Confirm current details on the live page or with Digital Equity staff."
      : "";
    const content = sentences.filter((_, index) => index !== noticeIndex).slice(0, 2);
    let budget = Math.max(1, limit - lead.split(/\s+/).filter(Boolean).length - notice.split(/\s+/).filter(Boolean).length);
    const points = [];

    content.forEach((sentence, index) => {
      if (budget <= 0) return;
      const remaining = content.length - index;
      const allocation = remaining === 1
        ? budget
        : Math.min(24, Math.max(12, budget - 14));
      const rawLabelMatch = sentence.match(/^([^:]{2,42}):\s+(.+)$/);
      const rawLabelWords = rawLabelMatch ? rawLabelMatch[1].split(/\s+/).filter(Boolean).length : 0;
      const pointLimit = rawLabelMatch && rawLabelWords <= 6
        ? Math.min(allocation, budget, 14)
        : Math.min(allocation, budget);
      const pointText = clipAnswerPoint(sentence, pointLimit);
      budget -= pointText.split(/\s+/).filter(Boolean).length;
      const labelMatch = pointText.match(/^([^:]{2,42}):\s+(.+)$/);
      const labelWords = labelMatch ? labelMatch[1].split(/\s+/).filter(Boolean).length : 0;
      points.push(labelMatch && labelWords <= 6
        ? { label: cleanText(labelMatch[1]), text: cleanText(labelMatch[2]) }
        : { label: "", text: pointText });
    });

    const flattened = cleanText([
      lead,
      ...points.map(point => [point.label, point.text].filter(Boolean).join(": ")),
      notice,
    ].filter(Boolean).join(" "));
    return { lead, points, notice, text: flattened };
  }

  function viewerMode(hostname, requested = "") {
    const override = String(requested || "").trim().toLowerCase();
    if (override === "admin" || override === "public") return override;
    const host = String(hostname || "").trim().toLowerCase().replace(/^\[|\]$/g, "");
    const local = host === "localhost"
      || host === "127.0.0.1"
      || host === "::1"
      || host === "0.0.0.0"
      || host.endsWith(".localhost");
    return local ? "admin" : "public";
  }

  function destinationLabel(value) {
    const label = cleanTitle(value);
    return /^(?:go to|contact|confirm|ask|view|review|register|browse|open|find|see|check)\b/i.test(label)
      ? label
      : `Go to ${label}`;
  }

  return {
    DISPLAY_MESSAGE_WORD_LIMIT,
    SITE_ORIGIN,
    answerPresentation,
    canonicalUrl,
    cleanText,
    cleanTitle,
    currentPageCanAnswer,
    deicticPageQuestion,
    destinationLabel,
    evidenceFor,
    hrefFor,
    normalizeDigits,
    normalizeTokens,
    pageFamily,
    pathFor,
    personalInformationDetected,
    redactSixDigitValues,
    starterFor,
    viewerMode,
  };
});
