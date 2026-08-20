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
  const liveOnlyPaths = new Set([
    "/file-share",
    "/groups",
    "/members",
    "/pdf2-upload",
  ]);
  let knownRoutes = null;

  function canonicalUrl(value) {
    try {
      const url = new URL(value, sourceUrl || "https://www.fortunedigitalequity.org/");
      if (url.protocol !== "https:" || !allowedHosts.has(url.hostname.toLowerCase())) {
        return "";
      }
      const originalPath = url.pathname.replace(/\/+$/, "") || "/";
      const path = legacyPathAliases.get(originalPath) || originalPath;
      return `https://www.fortunedigitalequity.org${path}`;
    } catch (_error) {
      return "";
    }
  }

  function validatedLiveUrl(value) {
    try {
      const url = new URL(value, sourceUrl || "https://www.fortunedigitalequity.org/");
      if (url.protocol !== "https:" || !allowedHosts.has(url.hostname.toLowerCase())) return null;
      return url;
    } catch (_error) {
      return null;
    }
  }

  function replicaUrl(value) {
    if (!knownRoutes) return null;
    let original;
    try {
      original = new URL(value, sourceUrl);
    } catch (_error) {
      return null;
    }
    const canonical = canonicalUrl(original.href);
    if (!canonical || !knownRoutes.has(canonical)) return null;
    const canonicalPath = new URL(canonical).pathname;
    if (liveOnlyPaths.has(canonicalPath)) return null;
    const path = canonicalPath.replace(/^\//, "");
    const destination = new URL(path, assetRoot);
    destination.search = original.search;
    destination.hash = original.hash;
    return destination;
  }

  function rewriteInternalLinks() {
    if (!knownRoutes) return;
    document.querySelectorAll("a[href]").forEach((anchor) => {
      if (anchor.dataset.liveAction === "true") return;
      const destination = replicaUrl(anchor.href);
      if (destination) anchor.href = destination.href;
    });
  }

  function visibleText(element) {
    return (element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function makeReplicaLink(source, className) {
    const label = visibleText(source);
    const href = source?.href || source?.getAttribute("href") || "";
    if (!label || !href) return null;
    const link = document.createElement("a");
    link.className = className;
    link.href = href;
    link.textContent = label;
    if (source.getAttribute("aria-current")) {
      link.setAttribute("aria-current", source.getAttribute("aria-current"));
    }
    return link;
  }

  /**
   * Wix's captured header is a fixed-width, JavaScript-dependent grid. The
   * public page links themselves are complete in the snapshot, so rebuild only
   * that navigation in neutral native markup. This lets the static replica
   * remain usable at narrow widths without inventing a new navigation tree.
   */
  function installReplicaHeader() {
    const sourceHeader = document.querySelector("#SITE_HEADER");
    const sourceNav = sourceHeader?.querySelector("nav > ul");
    if (!sourceHeader || !sourceNav || document.querySelector("#fortune-replica-header")) return;

    const header = document.createElement("header");
    header.id = "fortune-replica-header";
    header.setAttribute("aria-label", "Fortune Digital Equity site header");
    header.dataset.menuOpen = "false";

    const inner = document.createElement("div");
    inner.className = "fortune-replica-header__inner";

    const sourceBrand = [...sourceHeader.querySelectorAll("a[href]")]
      .find((anchor) => anchor.querySelector("img"));
    const sourceLogo = sourceBrand?.querySelector("img");
    if (sourceBrand && sourceLogo) {
      const brand = document.createElement("a");
      brand.className = "fortune-replica-header__brand";
      brand.href = sourceBrand.href;
      brand.setAttribute("aria-label", sourceBrand.getAttribute("aria-label") || "Fortune Digital Equity home");
      const logo = document.createElement("img");
      logo.src = sourceLogo.currentSrc || sourceLogo.src;
      logo.alt = sourceLogo.alt || "The Fortune Society";
      brand.append(logo);
      inner.append(brand);
    }

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "fortune-replica-header__toggle";
    toggle.textContent = "Menu";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", "fortune-replica-site-navigation");

    const nav = document.createElement("nav");
    nav.className = "fortune-replica-header__nav";
    nav.id = "fortune-replica-site-navigation";
    nav.setAttribute("aria-label", "Site navigation");
    const list = document.createElement("ul");

    for (const sourceItem of sourceNav.children) {
      if (sourceItem.getAttribute("aria-hidden") === "true") continue;
      const item = document.createElement("li");
      const sourceLink = sourceItem.querySelector(":scope > a[href]");
      if (sourceLink) {
        const link = makeReplicaLink(sourceLink, "fortune-replica-header__link");
        if (link) item.append(link);
      } else {
        const sourceMenu = sourceItem.querySelector(":scope > details[data-replica-static-menu]");
        const sourceSummary = sourceMenu?.querySelector(":scope > summary");
        const label = visibleText(sourceSummary);
        if (!sourceMenu || !label) continue;
        const menu = document.createElement("details");
        menu.className = "fortune-replica-header__menu";
        const summary = document.createElement("summary");
        summary.textContent = label;
        menu.append(summary);
        const submenu = document.createElement("ul");
        for (const sourceSubLink of sourceMenu.querySelectorAll(":scope > ul > li > a[href]")) {
          const link = makeReplicaLink(sourceSubLink, "fortune-replica-header__sublink");
          if (!link) continue;
          const subitem = document.createElement("li");
          subitem.append(link);
          submenu.append(subitem);
        }
        if (!submenu.childElementCount) continue;
        menu.append(submenu);
        item.append(menu);
      }
      if (item.childElementCount) list.append(item);
    }
    if (!list.childElementCount) return;

    nav.append(list);
    inner.append(toggle, nav);
    header.append(inner);
    sourceHeader.before(header);
    document.documentElement.dataset.fortuneReplicaHeaderReady = "true";

    toggle.addEventListener("click", () => {
      const isOpen = header.dataset.menuOpen === "true";
      header.dataset.menuOpen = String(!isOpen);
      toggle.setAttribute("aria-expanded", String(!isOpen));
    });

    nav.addEventListener("toggle", (event) => {
      const opened = event.target;
      if (!(opened instanceof HTMLDetailsElement) || !opened.open) return;
      nav.querySelectorAll("details[open]").forEach((menu) => {
        if (menu !== opened) menu.open = false;
      });
    }, true);
  }

  fetch(new URL("site-index.json", assetRoot), { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`route index returned ${response.status}`);
      return response.json();
    })
    .then((index) => {
      knownRoutes = new Set((index.pages || []).map((page) => canonicalUrl(page.url)).filter(Boolean));
      rewriteInternalLinks();
    })
    .catch(() => {
      knownRoutes = new Set();
    });

  document.addEventListener("click", (event) => {
    const anchor = event.target.closest("a[href]");
    if (!anchor) return;
    if (anchor.dataset.liveAction === "true") return;
    const destination = replicaUrl(anchor.href);
    if (destination) anchor.href = destination.href;
  }, true);

  const notice = document.createElement("div");
  notice.id = "fortune-replica-notice";
  notice.setAttribute("role", "note");
  notice.append("Static snapshot of the live Fortune site · Interactive services open on ");
  const liveLink = document.createElement("a");
  liveLink.href = canonicalUrl(sourceUrl) || "https://www.fortunedigitalequity.org/";
  liveLink.target = "_blank";
  liveLink.rel = "noreferrer";
  liveLink.dataset.liveAction = "true";
  liveLink.textContent = "Fortune's current website";
  notice.append(liveLink, ".");
  document.body.prepend(notice);

  installReplicaHeader();

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
  frameUrl.searchParams.set("v", "20260817-route-refresh-1");
  frameUrl.searchParams.set("embed", "1");
  frameUrl.searchParams.set("page", canonicalUrl(sourceUrl) || sourceUrl);
  const pageSearch = new URLSearchParams(window.location.search);
  if (pageSearch.get("open") === "1") frameUrl.searchParams.set("open", "1");
  frame.src = frameUrl.href;
  host.append(frame);
  document.body.append(host);

  window.addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || event.origin !== window.location.origin) return;
    const message = event.data || {};
    if (message.type === "fortune-sidecar-state") {
      host.dataset.expanded = message.expanded ? "true" : "false";
      return;
    }
    if (message.type === "fortune-sidecar-navigate") {
      const destination = replicaUrl(message.url);
      if (destination) {
        window.location.assign(destination.href);
        return;
      }
      const liveDestination = validatedLiveUrl(message.url);
      if (liveDestination) window.location.assign(liveDestination.href);
    }
  });
})();
