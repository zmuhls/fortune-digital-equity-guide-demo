import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";

const require = createRequire(import.meta.url);
const TESTS = dirname(fileURLToPath(import.meta.url));
const DEMO = dirname(TESTS);
const Core = require(join(DEMO, "guide-core.js"));
const index = JSON.parse(readFileSync(join(DEMO, "site-index.json"), "utf8"));
const pages = index.pages;
const byPath = new Map(pages.map(page => [new URL(page.url).pathname, page]));
const evaluationSource = readFileSync(join(DEMO, "evaluation.js"), "utf8");
const appSource = readFileSync(join(DEMO, "app.js"), "utf8");
const wixSource = readFileSync(join(DEMO, "wix-app", "site", "fortune-guide-element.js"), "utf8");

class FakeClassList {
  constructor(owner) {
    this.owner = owner;
  }

  values() {
    return new Set(String(this.owner.className || "").split(/\s+/).filter(Boolean));
  }

  write(values) {
    this.owner.className = [...values].join(" ");
  }

  add(...names) {
    const values = this.values();
    names.forEach(name => values.add(name));
    this.write(values);
  }

  remove(...names) {
    const values = this.values();
    names.forEach(name => values.delete(name));
    this.write(values);
  }

  contains(name) {
    return this.values().has(name);
  }

  toggle(name, force) {
    const values = this.values();
    const enabled = force === undefined ? !values.has(name) : Boolean(force);
    if (enabled) values.add(name);
    else values.delete(name);
    this.write(values);
    return enabled;
  }
}

class FakeElement {
  constructor(tagName = "div", ownerDocument = null) {
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentElement = null;
    this.className = "";
    this.classList = new FakeClassList(this);
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.readOnly = false;
    this.scrollHeight = 24;
    this.scrollTop = 0;
    this.offsetHeight = 24;
    this.clientHeight = 22;
  }

  append(...children) {
    children.forEach(child => {
      if (!(child instanceof FakeElement)) return;
      child.parentElement = this;
      child.shadowRootHost = this.shadowRootHost;
      this.children.push(child);
    });
  }

  replaceChildren(...children) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentElement) return;
    this.parentElement.children = this.parentElement.children.filter(child => child !== this);
    this.parentElement = null;
  }

  get nextSibling() {
    if (!this.parentElement) return null;
    const index = this.parentElement.children.indexOf(this);
    return this.parentElement.children[index + 1] || null;
  }

  get options() {
    return this.children.filter(child => child.tagName === "OPTION");
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  toggleAttribute(name, force) {
    if (force) this.setAttribute(name, "");
    else this.attributes.delete(name);
  }

  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }

  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.defaultPrevented ||= false;
    const originalPreventDefault = event.preventDefault;
    event.preventDefault = () => {
      event.defaultPrevented = true;
      originalPreventDefault?.call(event);
    };
    (this.listeners.get(event.type) || []).forEach(handler => handler(event));
    return !event.defaultPrevented;
  }

  requestSubmit() {
    this.dispatchEvent({ type: "submit", preventDefault() {} });
  }

  contains(element) {
    return this === element || this.children.some(child => child.contains(element));
  }

  matches(selector) {
    if (selector === 'button[type="submit"]') return this.tagName === "BUTTON" && this.type === "submit";
    if (selector.startsWith(".")) {
      return selector.slice(1).split(".").every(name => this.classList.contains(name));
    }
    return this.tagName === selector.toUpperCase();
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = element => {
      element.children.forEach(child => {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      });
    };
    visit(this);
    return matches;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
    if (this.shadowRootHost) this.shadowRootHost.activeElement = this;
  }

  setSelectionRange() {}
  scrollTo(options = {}) { this.scrollTop = Number(options.top || 0); }
  getBoundingClientRect() { return { top: 0, left: 0, right: 100, bottom: 24, width: 100, height: 24 }; }
}

class FakeDocument extends FakeElement {
  constructor() {
    super("document");
    this.ownerDocument = this;
    this.activeElement = null;
    this.title = "Digital Equity";
    this.nodes = new Map();
  }

  register(selector, element) {
    element.ownerDocument = this;
    this.nodes.set(selector, element);
    return element;
  }

  querySelector(selector) {
    return this.nodes.get(selector) || super.querySelector(selector);
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
}

class FakeStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

function fakeResponse(payload, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    headers: { get: name => String(name).toLowerCase() === "content-type" ? "application/json" : null },
    async json() { return payload; },
  };
}

function keyEvent(key, options = {}) {
  return {
    type: "keydown",
    key,
    shiftKey: Boolean(options.shiftKey),
    isComposing: Boolean(options.isComposing),
    preventDefault() {},
  };
}

function descendants(element) {
  return element.children.flatMap(child => [child, ...descendants(child)]);
}

async function waitFor(predicate, message = "frontend state did not settle") {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.fail(message);
}

async function pagesHarness({ chatPayload, chatError, chatResponses = [], modelEnabled = false, captureMode = "none" } = {}) {
  const document = new FakeDocument();
  const panel = document.register("#guide-panel", new FakeElement("section", document));
  const toggle = document.register("#guide-toggle", new FakeElement("button", document));
  const close = document.register("#guide-close", new FakeElement("button", document));
  const title = document.register("#guide-title", new FakeElement("h2", document));
  const transcript = document.register("#chat-transcript", new FakeElement("div", document));
  const suggestions = document.register("#chat-suggestions", new FakeElement("div", document));
  const form = document.register("#question-form", new FakeElement("form", document));
  const questionLabel = document.register("#question-label", new FakeElement("label", document));
  const input = document.register("#question", new FakeElement("textarea", document));
  const send = new FakeElement("button", document);
  send.type = "submit";
  const editStatus = document.register("#edit-status", new FakeElement("p", document));
  const editCancel = document.register("#edit-cancel", new FakeElement("button", document));
  const privacyCopy = document.register("#privacy-copy", new FakeElement("p", document));
  const modelStatus = document.register("#model-status", new FakeElement("p", document));
  const contextText = document.register("#context-window-text", new FakeElement("p", document));
  const contextCopy = document.register("#context-window-copy", new FakeElement("p", document));
  const reset = document.register("#guide-reset", new FakeElement("button", document));
  form.append(input, send, editCancel);
  panel.append(close, title, transcript, suggestions, form, editStatus, privacyCopy, modelStatus, contextText, contextCopy, reset);
  panel.hidden = false;

  const storage = new FakeStorage();
  const chatRequests = [];
  const pendingChatResponses = [...chatResponses];
  let healthRequests = 0;
  const fetch = async (url, options = {}) => {
    const value = String(url);
    if (value.endsWith("/health")) {
      healthRequests += 1;
      return fakeResponse({ model_enabled: modelEnabled, conversation_logging: { capture_mode: captureMode } });
    }
    if (value.endsWith("/api/chat")) {
      chatRequests.push(JSON.parse(options.body));
      if (chatError) throw chatError;
      if (pendingChatResponses.length) {
        const next = pendingChatResponses.shift();
        return fakeResponse(next.payload, next.status < 400, next.status);
      }
      return fakeResponse(chatPayload);
    }
    if (value.endsWith("/api/warmup")) return fakeResponse({ status: modelEnabled ? "ready" : "disabled" });
    throw new Error(`Unexpected request: ${value}`);
  };
  const currentPage = {
    id: "home",
    title: "Digital Equity home",
    url: "https://www.fortunedigitalequity.org/",
    authority: "answer",
    status: 200,
  };
  const window = {
    FORTUNE_GUIDE_CONFIG: { apiBaseUrl: "https://guide.test" },
    FortuneGuideCore: Core,
    FortuneMockSite: {
      ready: Promise.resolve(currentPage),
      getCurrentPage: () => currentPage,
      cleanTitle: Core.cleanTitle,
      getStarter: () => ({ suggestions: [] }),
      canonicalUrl: Core.canonicalUrl,
      hrefFor: value => value,
      isKnown: value => Boolean(Core.canonicalUrl(value)),
    },
    location: { href: "https://pages.test/sidecar.html", search: "" },
    sessionStorage: storage,
    crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
    getComputedStyle: () => ({ maxHeight: "92" }),
    addEventListener() {},
  };
  window.parent = window;
  runInNewContext(appSource, {
    window,
    document,
    fetch,
    URL,
    URLSearchParams,
    requestAnimationFrame: callback => callback(),
    console,
  }, { filename: "app.js" });
  await waitFor(() => healthRequests === 1 && window.FortuneGuide.state().apiReady, "Pages health check did not settle");
  return { window, document, input, transcript, editStatus, privacyCopy, contextCopy, storage, chatRequests };
}

class FakeShadowRoot extends FakeElement {
  constructor(ownerDocument) {
    super("shadow-root", ownerDocument);
    this.nodes = new Map();
    this.activeElement = null;
  }

  set innerHTML(_value) {
    this.nodes = new Map();
    this.children = [];
    const add = (selector, tagName = "div") => {
      const element = new FakeElement(tagName, this.ownerDocument);
      element.shadowRootHost = this;
      this.nodes.set(selector, element);
      return element;
    };
    const toggle = add(".toggle", "button");
    const panel = add(".panel", "section");
    const close = add(".close", "button");
    const transcript = add(".transcript", "div");
    const suggestions = add(".suggestions", "div");
    const form = add("form", "form");
    const questionLabel = add("#fortune-guide-question-label", "label");
    const input = add("textarea", "textarea");
    const send = add(".send", "button");
    const cancel = add(".cancel-edit", "button");
    const editStatus = add(".edit-status", "p");
    const privacy = add("#fortune-guide-privacy", "p");
    const capture = add(".capture-notice", "p");
    const context = add(".context-count", "p");
    const model = add(".model-status", "p");
    const status = add(".status", "p");
    const reset = add(".reset", "button");
    const contact = add(".contact", "a");
    send.type = "submit";
    panel.hidden = true;
    reset.hidden = true;
    cancel.hidden = true;
    form.append(input, send, cancel, editStatus, status);
    panel.append(close, transcript, suggestions, form, privacy, capture, context, model, reset, contact);
    this.append(toggle, panel, questionLabel);
  }

  get innerHTML() { return ""; }

  querySelector(selector) {
    return this.nodes.get(selector) || super.querySelector(selector);
  }
}

async function wixHarness({ chatPayload, chatError, chatResponses = [], captureMode = "none" } = {}) {
  const document = new FakeDocument();
  const storage = new FakeStorage();
  const chatRequests = [];
  const pendingChatResponses = [...chatResponses];
  let healthRequests = 0;
  let GuideElement = null;
  const fetch = async (url, options = {}) => {
    const value = String(url);
    if (value.endsWith("/health")) {
      healthRequests += 1;
      return fakeResponse({ model_enabled: true, conversation_logging: { capture_mode: captureMode } });
    }
    if (value.endsWith("/api/warmup")) return fakeResponse({ status: "ready" });
    if (value.endsWith("/api/chat")) {
      chatRequests.push(JSON.parse(options.body));
      if (chatError) throw chatError;
      if (pendingChatResponses.length) {
        const next = pendingChatResponses.shift();
        return fakeResponse(next.payload, next.status < 400, next.status);
      }
      return fakeResponse(chatPayload);
    }
    throw new Error(`Unexpected request: ${value}`);
  };
  const window = {
    location: {
      href: "https://www.fortunedigitalequity.org/",
      pathname: "/",
    },
    sessionStorage: storage,
    crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000002" },
    getComputedStyle: () => ({ maxHeight: "92" }),
  };
  class FakeHTMLElement extends FakeElement {
    constructor() {
      super("fortune-digital-equity-guide", document);
    }

    attachShadow() {
      this.shadowRoot = new FakeShadowRoot(document);
      return this.shadowRoot;
    }
  }
  const customElements = {
    get() { return null; },
    define(_name, constructor) { GuideElement = constructor; },
  };
  runInNewContext(wixSource, {
    window,
    document,
    fetch,
    customElements,
    HTMLElement: FakeHTMLElement,
    URL,
    requestAnimationFrame: callback => callback(),
    console,
  }, { filename: "fortune-guide-element.js" });
  const guide = new GuideElement();
  guide.setAttribute("api-base-url", "https://guide.test");
  guide.connectedCallback();
  guide.panel.hidden = false;
  await waitFor(() => healthRequests === 1 && guide.capturePolicyReady, "Wix health check did not settle");
  return {
    guide,
    input: guide.input,
    transcript: guide.transcript,
    status: guide.status,
    privacyNotice: guide.privacyNotice,
    captureNotice: guide.captureNotice,
    storage,
    chatRequests,
  };
}

function runEmbedBridge({ panelHidden = true, anchor = null } = {}) {
  const messages = [];
  let clickHandler = null;
  const panel = { hidden: panelHidden };
  const parent = {
    postMessage(message, origin) { messages.push({ message, origin }); },
  };
  const location = {
    search: "?embed=1",
    origin: "https://zmuhls.github.io",
    href: "https://zmuhls.github.io/fortune-digital-equity-guide-demo/sidecar.html?embed=1",
  };
  const window = {
    parent,
    location,
    addEventListener() {},
  };
  const document = {
    querySelector(selector) {
      if (selector === "#guide-panel") return panel;
      return null;
    },
    addEventListener(type, handler) {
      if (type === "click") clickHandler = handler;
    },
  };
  class MutationObserver {
    constructor() {}
    observe() {}
  }
  runInNewContext(
    readFileSync(join(DEMO, "embed-frame.js"), "utf8"),
    { window, document, MutationObserver, URL, URLSearchParams },
  );
  if (anchor && clickHandler) {
    clickHandler({
      target: { closest: () => anchor },
      preventDefault() {},
      stopImmediatePropagation() {},
    });
  }
  return messages;
}

function evaluationTimestampHelpers() {
  const start = evaluationSource.indexOf("function timestampValue(value)");
  const end = evaluationSource.indexOf("function setStatus(message", start);
  assert.ok(start >= 0 && end > start, "evaluation timestamp helpers are present");
  return evaluationSource.slice(start, end);
}

test("evaluation conversations are ordered newest first before pagination", () => {
  const helpers = evaluationTimestampHelpers();
  const conversations = [
    { id: "older", last_turn_at: "2026-08-15T12:00:00Z" },
    { id: "same-b", last_turn_at: "2026-08-17T12:00:00Z" },
    { id: "invalid", last_turn_at: "" },
    { id: "newest", last_turn_at: "2026-08-18T12:00:00Z" },
    { id: "same-a", last_turn_at: "2026-08-17T12:00:00Z" },
  ];
  const orderedJson = runInNewContext(
    `${helpers}; JSON.stringify(newestFirst(${JSON.stringify(conversations)}).map(item => item.id))`,
    { Date, Intl, JSON, Number, String },
  );
  assert.deepEqual(JSON.parse(orderedJson), ["newest", "same-a", "same-b", "older", "invalid"]);
});

test("Pages and Wix disclose human transcript review when capture is active", async () => {
  const pages = await pagesHarness({ captureMode: "transcript" });
  assert.equal(
    pages.privacyCopy.textContent,
    "Recorded for team review. Don’t include personal information.",
  );
  assert.equal(
    pages.contextCopy.textContent,
    "Questions and answers are recorded for team review.",
  );

  const wix = await wixHarness({ captureMode: "transcript" });
  assert.equal(
    wix.privacyNotice.textContent,
    "Recorded for team review. Don’t include personal information.",
  );
  assert.equal(
    wix.captureNotice.textContent,
    "Questions and answers are recorded for team review.",
  );
});

test("canonical URLs stay on the approved public host", () => {
  assert.equal(Core.canonicalUrl("https://fortunedigitalequity.org/devices/?x=1#top"), "https://www.fortunedigitalequity.org/devices");
  assert.equal(Core.canonicalUrl("/about/"), "https://www.fortunedigitalequity.org/about");
  assert.equal(Core.canonicalUrl("https://example.com/devices"), "");
  assert.equal(Core.pathFor("https://www.fortunedigitalequity.org/"), "/");
  assert.equal(Core.canonicalUrl("/trainings"), "https://www.fortunedigitalequity.org/workshops");
  assert.equal(Core.canonicalUrl("/individual"), "https://www.fortunedigitalequity.org/support");
  assert.equal(Core.canonicalUrl("/reserve"), "https://www.fortunedigitalequity.org/calendar");
  assert.equal(Core.canonicalUrl("/about/partners"), "https://www.fortunedigitalequity.org/about");
});

test("all 138 routes receive one of the reviewed page families", () => {
  const counts = {};
  for (const page of pages) {
    const family = Core.pageFamily(page);
    counts[family] = (counts[family] || 0) + 1;
  }
  assert.deepEqual(counts, {
    program: 3,
    excluded: 18,
    action: 3,
    directory: 6,
    support: 2,
    event: 4,
    archive: 21,
    news: 9,
    service: 72,
  });
  assert.equal(Object.values(counts).reduce((sum, value) => sum + value, 0), 138);
});

test("every page has a tailored heading, placeholder, and exactly two prompts", () => {
  for (const page of pages) {
    const starter = Core.starterFor(page);
    assert.ok(starter.heading.length > 8, page.url);
    assert.ok(starter.placeholder.endsWith("?"), page.url);
    assert.equal(starter.suggestions.length, 2, page.url);
    assert.equal(new Set(starter.suggestions).size, 2, page.url);
  }
  assert.equal(Core.starterFor(byPath.get("/devices")).placeholder, "Do you need a device or help using one?");
  assert.equal(Core.starterFor(byPath.get("/calendar")).suggestions[0], "Where and when are current classes?");
  assert.equal(Core.starterFor(byPath.get("/service-page/understanding-computers")).suggestions[0], "What does this class cover?");
});

test("starter prompts keep their full question while exposing compact button labels", () => {
  assert.equal(Core.suggestionLabel("What is the main information here?"), "Page summary");
  assert.equal(Core.suggestionLabel("What can I do from this page?"), "Page options");
  assert.equal(Core.suggestionLabel("What does this class cover?"), "Class details");
  assert.equal(Core.suggestionLabel("I need information about getting a device"), "Get a device");
  assert.equal(Core.suggestionLabel("Where and when are current classes?"), "Class times");

  for (const page of pages) {
    for (const prompt of Core.starterFor(page).suggestions) {
      const label = Core.suggestionLabel(prompt);
      assert.ok(label.length > 0 && label.length <= 32, `${page.url}: ${label}`);
      assert.equal(/[?!.]$/.test(label), false, `${page.url}: ${label}`);
      assert.equal(/\bnext\b/i.test(`${label} ${prompt}`), false, `${page.url}: ${label}`);
    }
  }
});

test("current-page evidence is recognized before a wider search", () => {
  const devices = byPath.get("/devices");
  const calendar = byPath.get("/calendar");
  assert.equal(Core.currentPageCanAnswer("Can I get a free laptop?", devices), true);
  assert.equal(Core.currentPageCanAnswer("Can I get a free laptop?", calendar), false);
  assert.equal(Core.currentPageCanAnswer("What does this page say?", calendar), true);
  assert.equal(Core.currentPageCanAnswer("What is the zzyzx quasar permit policy?", calendar), false);
});

test("excluded, archived, and partial records can never become current-page evidence", () => {
  for (const page of pages.filter(page => page.authority !== "answer" || Number(page.status) !== 200)) {
    assert.equal(Core.currentPageCanAnswer("What does this page say?", page), false, page.url);
  }
});

test("six-digit Fortune ID patterns are detected after Unicode normalization", () => {
  for (const value of [
    "123456",
    "123-456",
    "123–456",
    "123—456",
    "123 456",
    "１２３４５６",
    "١٢٣٤٥٦",
    "My Fortune ID is 654321",
  ]) {
    assert.equal(Core.personalInformationDetected(value), true, value);
  }
  assert.equal(Core.personalInformationDetected("Workshop 12345"), false);
  assert.equal(Core.personalInformationDetected("Workshop 1234567"), false);
});

test("other obvious personal-information forms are held", () => {
  for (const value of [
    "Email me at person@example.com",
    "My SSN is 123-45-6789",
    "My case number is ABC-12",
    "My name is Rosa",
    "Their phone is in my contacts",
    "My email is not working",
    "My address is 123 Example Street",
    "I need help with my health",
    "My diagnosis is private",
  ]) {
    assert.equal(Core.personalInformationDetected(value), true, value);
  }
});

test("redaction removes every six-digit representation from display text", () => {
  for (const value of ["123456", "123-456", "123–456", "123—456", "123 456", "１２３４５６", "١٢٣٤٥٦"]) {
    const redacted = Core.redactSixDigitValues(`ID ${value}`);
    assert.equal(redacted.includes("123456"), false, value);
    assert.equal(redacted.includes("123-456"), false, value);
    assert.match(redacted, /\[six-digit ID removed\]/);
  }
});

test("editing the latest exchange branches from the preceding bounded history", () => {
  const history = [
    { role: "user", content: "First question" },
    { role: "assistant", content: "First answer" },
    { role: "user", content: "Latest question" },
    { role: "assistant", content: "Latest answer" },
  ];
  assert.deepEqual(Core.historyBeforeLatestExchange(history), history.slice(0, 2));
  assert.deepEqual(Core.historyBeforeLatestExchange([]), []);
  assert.deepEqual(Core.historyBeforeLatestExchange(null), []);
});

test("mock hrefs preserve the repository base for root and nested routes", () => {
  const about = Core.canonicalUrl("/about");
  const root = Core.canonicalUrl("/");
  const known = new Set([about, root]);
  assert.equal(Core.hrefFor(about, { staticRoutes: false, knownUrls: known }), "?page=%2Fabout");
  assert.equal(Core.hrefFor(about, { staticRoutes: true, assetBase: "../../", knownUrls: known }), "../../about/");
  assert.equal(Core.hrefFor(root, { staticRoutes: true, assetBase: "../../", knownUrls: known }), "../../");
  assert.equal(Core.hrefFor("https://example.com/nope", { staticRoutes: true, assetBase: "../../", knownUrls: known }), "https://example.com/nope");
});

test("destination labels stay grammatical", () => {
  assert.equal(Core.destinationLabel("Regular Workshops | FS Digital Equity"), "Go to Regular Workshops");
  assert.equal(Core.destinationLabel("Confirm eligibility with staff"), "Confirm eligibility with staff");
  assert.equal(Core.destinationLabel("Contact Digital Equity"), "Contact Digital Equity");
});

test("public text cleanup removes duplicated sentences and source-title suffixes", () => {
  assert.equal(Core.cleanText("Great , start here.  Great , start here."), "Great, start here.");
  assert.equal(Core.cleanTitle("Devices | FS Digital Equity"), "Devices");
});

test("embedded guide reports an open panel before an answer expands it", () => {
  const messages = runEmbedBridge({ panelHidden: false });
  assert.equal(messages[0].message.type, "fortune-sidecar-state");
  assert.equal(messages[0].message.expanded, true);
  assert.equal(messages[0].origin, "https://zmuhls.github.io");
});

test("embedded guide sends source and query-based destinations to its parent", () => {
  const declared = runEmbedBridge({
    anchor: {
      href: "https://zmuhls.github.io/fortune-digital-equity-guide-demo/sidecar.html?page=%2Fabout&open=1",
      dataset: { mockUrl: "https://www.fortunedigitalequity.org/about" },
    },
  });
  assert.equal(declared.at(-1).message.url, "https://www.fortunedigitalequity.org/about?open=1");

  const queryFallback = runEmbedBridge({
    anchor: {
      href: "https://zmuhls.github.io/fortune-digital-equity-guide-demo/sidecar.html?page=%2Fcalendar",
      dataset: {},
    },
  });
  assert.equal(queryFallback.at(-1).message.url, "https://www.fortunedigitalequity.org/calendar");
});

const validModelAnswer = Object.freeze({
  kind: "answer",
  message: "Approved source-backed answer.",
  model_called: true,
  retrieval_scope: "site",
  sources: [],
  related: [],
  choices: [],
});

test("Pages and Wix accept one Return submission and preserve model provenance", async () => {
  const pages = await pagesHarness({ chatPayload: validModelAnswer });
  pages.input.value = "What is available?";
  pages.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => pages.chatRequests.length === 1 && !pages.window.FortuneGuide.state().answering,
    "Pages Return submission did not settle",
  );
  const pageAssistants = descendants(pages.transcript).filter(element => element.classList.contains("assistant"));
  assert.equal(pages.chatRequests.length, 1);
  assert.equal(pageAssistants.length, 1);
  assert.equal(pageAssistants[0].dataset.modelCalled, "true");
  const pageSession = JSON.parse([...pages.storage.values.values()][0]);
  assert.equal(pageSession.turns[0].payload.model_called, true);

  const wix = await wixHarness({ chatPayload: validModelAnswer });
  wix.input.value = "What is available?";
  wix.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => wix.chatRequests.length === 1 && !wix.guide.answering,
    "Wix Return submission did not settle",
  );
  const wixAssistants = descendants(wix.transcript).filter(element => element.classList.contains("assistant"));
  assert.equal(wix.chatRequests.length, 1);
  assert.equal(wixAssistants.length, 1);
  assert.equal(wixAssistants[0].dataset.modelCalled, "true");
  const wixSession = JSON.parse([...wix.storage.values.values()][0]);
  assert.equal(wixSession.turns[0].payload.model_called, true);
});

test("Pages and Wix reject successful nonprivacy payloads outside the model contract", async t => {
  const invalidPayloads = [
    {
      name: "model was not called",
      payload: { ...validModelAnswer, model_called: false },
    },
    {
      name: "message is empty",
      payload: { ...validModelAnswer, message: "   " },
    },
    {
      name: "kind is not allowed",
      payload: { ...validModelAnswer, kind: "unknown" },
    },
  ];

  for (const { name, payload } of invalidPayloads) {
    await t.test(name, async () => {
      const pages = await pagesHarness({ chatPayload: payload });
      pages.input.value = "Help me";
      pages.input.dispatchEvent(keyEvent("Enter"));
      await waitFor(
        () => pages.chatRequests.length === 1 && !pages.window.FortuneGuide.state().answering,
        `Pages invalid payload did not settle: ${name}`,
      );
      assert.equal(pages.window.FortuneGuide.state().turnCount, 0);
      assert.equal(descendants(pages.transcript).filter(element => element.classList.contains("assistant")).length, 0);
      assert.equal(pages.editStatus.textContent, "Guide unavailable. Try again.");

      const wix = await wixHarness({ chatPayload: payload });
      wix.input.value = "Help me";
      wix.input.dispatchEvent(keyEvent("Enter"));
      await waitFor(
        () => wix.chatRequests.length === 1 && !wix.guide.answering,
        `Wix invalid payload did not settle: ${name}`,
      );
      assert.equal(wix.guide.turns.length, 0);
      assert.equal(descendants(wix.transcript).filter(element => element.classList.contains("assistant")).length, 0);
      assert.equal(wix.status.textContent, "Guide unavailable. Try again.");
    });
  }
});

test("Pages and Wix retry an in-progress turn with the same client event ID", async () => {
  const inProgress = {
    status: 409,
    payload: {
      error: "This question is still being processed.",
      idempotency_complete: false,
    },
  };
  const completed = { status: 200, payload: validModelAnswer };

  const pages = await pagesHarness({
    modelEnabled: true,
    chatResponses: [inProgress, completed],
  });
  pages.input.value = "Help me";
  pages.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => pages.chatRequests.length === 1 && !pages.window.FortuneGuide.state().answering,
    "Pages in-progress response did not settle",
  );
  const pagesEventId = pages.chatRequests[0].client_event_id;
  assert.equal(pages.editStatus.textContent, "Still working. Try again.");
  assert.equal(pages.input.value, "Help me");
  assert.equal(pages.window.FortuneGuide.state().pendingClientEventId, pagesEventId);
  assert.equal(pages.window.FortuneGuide.state().apiReady, true);
  assert.equal(pages.window.FortuneGuide.state().modelReady, true);
  assert.equal(descendants(pages.transcript).filter(element => element.classList.contains("assistant")).length, 0);

  pages.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => pages.chatRequests.length === 2 && !pages.window.FortuneGuide.state().answering,
    "Pages in-progress retry did not settle",
  );
  assert.equal(pages.chatRequests[1].client_event_id, pagesEventId);
  assert.equal(pages.window.FortuneGuide.state().pendingClientEventId, "");
  assert.equal(pages.window.FortuneGuide.state().turnCount, 1);

  const wix = await wixHarness({ chatResponses: [inProgress, completed] });
  wix.input.value = "Help me";
  wix.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => wix.chatRequests.length === 1 && !wix.guide.answering,
    "Wix in-progress response did not settle",
  );
  const wixEventId = wix.chatRequests[0].client_event_id;
  assert.equal(wix.status.textContent, "Still working. Try again.");
  assert.equal(wix.input.value, "Help me");
  assert.equal(wix.guide.pendingClientEventId, wixEventId);
  assert.notEqual(wix.guide.modelStatus.textContent, "Unavailable");
  assert.equal(descendants(wix.transcript).filter(element => element.classList.contains("assistant")).length, 0);

  wix.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => wix.chatRequests.length === 2 && !wix.guide.answering,
    "Wix in-progress retry did not settle",
  );
  assert.equal(wix.chatRequests[1].client_event_id, wixEventId);
  assert.equal(wix.guide.pendingClientEventId, "");
  assert.equal(wix.guide.turns.length, 1);
});

test("Pages and Wix distinguish bounded HTTP failures without adding Guide turns", async t => {
  const cases = [
    { status: 429, message: "Guide busy. Try again shortly.", backendReady: true },
    { status: 502, message: "Try rephrasing.", backendReady: true },
    { status: 503, message: "Guide unavailable. Try again.", backendReady: false },
  ];

  for (const failure of cases) {
    await t.test(String(failure.status), async () => {
      const chatResponses = [{
        status: failure.status,
        payload: {
          error: "Bounded server failure.",
          model_called: failure.status !== 429,
        },
      }];
      const pages = await pagesHarness({ modelEnabled: true, chatResponses });
      pages.input.value = "Help me";
      pages.input.dispatchEvent(keyEvent("Enter"));
      await waitFor(
        () => pages.chatRequests.length === 1 && !pages.window.FortuneGuide.state().answering,
        `Pages ${failure.status} response did not settle`,
      );
      assert.equal(pages.editStatus.textContent, failure.message);
      assert.equal(pages.input.value, "Help me");
      assert.equal(pages.window.FortuneGuide.state().turnCount, 0);
      assert.equal(pages.window.FortuneGuide.state().apiReady, failure.backendReady);
      assert.equal(pages.window.FortuneGuide.state().modelReady, failure.backendReady);
      assert.equal(descendants(pages.transcript).filter(element => element.classList.contains("assistant")).length, 0);

      const wix = await wixHarness({ chatResponses });
      wix.input.value = "Help me";
      wix.input.dispatchEvent(keyEvent("Enter"));
      await waitFor(
        () => wix.chatRequests.length === 1 && !wix.guide.answering,
        `Wix ${failure.status} response did not settle`,
      );
      assert.equal(wix.status.textContent, failure.message);
      assert.equal(wix.input.value, "Help me");
      assert.equal(wix.guide.turns.length, 0);
      assert.equal(wix.guide.modelStatus.textContent === "Unavailable", !failure.backendReady);
      assert.equal(descendants(wix.transcript).filter(element => element.classList.contains("assistant")).length, 0);
    });
  }
});

test("Pages and Wix keep transport failures out of the Guide transcript", async () => {
  const pages = await pagesHarness({ chatError: new Error("network down") });
  pages.input.value = "Help me";
  pages.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => pages.chatRequests.length === 1 && !pages.window.FortuneGuide.state().answering,
    "Pages transport failure did not settle",
  );
  assert.equal(descendants(pages.transcript).filter(element => element.classList.contains("assistant")).length, 0);
  assert.equal(pages.editStatus.textContent, "Guide unavailable. Try again.");
  assert.equal(pages.window.FortuneGuide.state().apiReady, false);
  assert.equal(pages.window.FortuneGuide.state().modelReady, false);

  const wix = await wixHarness({ chatError: new Error("network down") });
  wix.input.value = "Help me";
  wix.input.dispatchEvent(keyEvent("Enter"));
  await waitFor(
    () => wix.chatRequests.length === 1 && !wix.guide.answering,
    "Wix transport failure did not settle",
  );
  assert.equal(descendants(wix.transcript).filter(element => element.classList.contains("assistant")).length, 0);
  assert.equal(wix.status.textContent, "Guide unavailable. Try again.");
  assert.equal(wix.guide.modelStatus.textContent, "Unavailable");
});

test("Pages and Wix block personal information before any chat POST", async () => {
  const pages = await pagesHarness({ chatPayload: validModelAnswer });
  pages.input.value = "My Fortune ID is 123456";
  pages.input.dispatchEvent(keyEvent("Enter"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(pages.chatRequests.length, 0);

  const wix = await wixHarness({ chatPayload: validModelAnswer });
  wix.input.value = "My Fortune ID is 123456";
  wix.input.dispatchEvent(keyEvent("Enter"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(wix.chatRequests.length, 0);
});
