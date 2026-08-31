(() => {
  "use strict";

  const script = document.currentScript;
  if (!script) return;

  const sourceUrl = script.dataset.sourceUrl || "";
  const assetRoot = new URL("./", script.src);
  const allowedHosts = new Set([
    "fortunedigitalequity.org",
    "www.fortunedigitalequity.org",
  ]);
  const legacyPathAliases = new Map([
    ["/about/partners", "/about"],
    ["/individual", "/support"],
    ["/reserve", "/calendar"],
    ["/trainings", "/workshops"],
  ]);
  const liveOnlyPaths = new Set(["/file-share", "/groups", "/members", "/pdf2-upload"]);
  let knownRoutes = null;

  function canonicalUrl(value) {
    try {
      const url = new URL(value, sourceUrl || "https://www.fortunedigitalequity.org/");
      if (url.protocol !== "https:" || !allowedHosts.has(url.hostname.toLowerCase())) return "";
      const originalPath = url.pathname.replace(/\/+$/, "") || "/";
      const path = legacyPathAliases.get(originalPath) || originalPath;
      return `https://www.fortunedigitalequity.org${path}`;
    } catch {
      return "";
    }
  }

  function replicaUrl(value) {
    if (!knownRoutes) return null;
    const canonical = canonicalUrl(value);
    if (!canonical || !knownRoutes.has(canonical)) return null;
    const path = new URL(canonical).pathname;
    if (liveOnlyPaths.has(path)) return null;
    return new URL(path.replace(/^\//, ""), assetRoot);
  }

  function liveUrl(value) {
    const canonical = canonicalUrl(value);
    return canonical ? new URL(canonical) : null;
  }

  fetch(new URL("site-index.json", assetRoot), { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error(`route index returned ${response.status}`);
      return response.json();
    })
    .then(index => {
      knownRoutes = new Set((index.pages || []).map(page => canonicalUrl(page.url)).filter(Boolean));
    })
    .catch(() => {
      knownRoutes = new Set();
    });

  if (new URLSearchParams(window.location.search).get("guide") === "0") return;

  const host = document.createElement("div");
  host.id = "fortune-sidecar-host";
  host.dataset.expanded = "false";

  const frame = document.createElement("iframe");
  frame.id = "fortune-sidecar-frame";
  frame.title = "Website Guide";
  frame.loading = "eager";
  frame.setAttribute(
    "sandbox",
    "allow-forms allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
  );
  const frameUrl = new URL("sidecar.html", assetRoot);
  frameUrl.searchParams.set("v", "20260831-v33-1");
  frameUrl.searchParams.set("embed", "1");
  frameUrl.searchParams.set("page", canonicalUrl(sourceUrl) || sourceUrl);
  if (new URLSearchParams(window.location.search).get("open") === "1") {
    frameUrl.searchParams.set("open", "1");
  }
  frame.src = frameUrl.href;
  host.append(frame);
  document.body.append(host);

  window.addEventListener("message", event => {
    if (event.source !== frame.contentWindow || event.origin !== window.location.origin) return;
    const message = event.data || {};
    if (message.type === "fortune-sidecar-state") {
      host.dataset.expanded = message.expanded ? "true" : "false";
      return;
    }
    if (message.type !== "fortune-sidecar-navigate") return;
    const destination = replicaUrl(message.url);
    if (destination) {
      window.location.assign(destination.href);
      return;
    }
    const liveDestination = liveUrl(message.url);
    if (liveDestination) window.location.assign(liveDestination.href);
  });
})();
