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

  const responsiveNavigationGroups = [
    {
      label: "SERVICES",
      links: [
        ["Regular Workshops", "workshops/"],
        ["Individual Support", "support/"],
        ["Special Events & Sessions", "events/"],
        ["Professional Digital Foundations", "pdf/"],
        ["Microsoft Certifications", "certifications/"],
        ["Tech Fair", "techfair/"],
      ],
    },
    {
      label: "RESOURCES",
      links: [
        ["Practice Your Skills", "practice/"],
        ["Device Distribution", "devices/"],
        ["Find Opportunities", "opportunities/"],
        ["Other Digital Resources", "other/"],
      ],
    },
  ];

  function localUrl(path) {
    return new URL(path, assetRoot).href;
  }

  function buildResponsiveNavigation() {
    if (document.querySelector("#fortune-responsive-header")) return;
    const sourceHeader = document.querySelector("#SITE_HEADER");
    const sourceBrand = sourceHeader?.querySelector('a[aria-label="Homepage"]');
    if (!sourceHeader || !sourceBrand) return;

    const header = document.createElement("header");
    header.id = "fortune-responsive-header";
    header.innerHTML = `
      <div class="fortune-responsive-header__bar">
        <a class="fortune-responsive-header__brand" href="${localUrl("")}" aria-label="Digital Equity home"></a>
        <button class="fortune-responsive-header__toggle" type="button" aria-expanded="false" aria-controls="fortune-responsive-navigation">
          <span>MENU</span><span class="fortune-responsive-header__toggle-icon" aria-hidden="true"></span>
        </button>
      </div>
      <nav id="fortune-responsive-navigation" class="fortune-responsive-navigation" aria-label="Site menu">
        <a href="${localUrl("")}">HOME</a>
        <a href="${localUrl("about/")}">ABOUT</a>
        ${responsiveNavigationGroups.map(group => `
          <details>
            <summary>${group.label}</summary>
            <div>${group.links.map(([label, path]) => `<a href="${localUrl(path)}">${label}</a>`).join("")}</div>
          </details>
        `).join("")}
        <a href="${localUrl("calendar/")}">CALENDAR</a>
        <a href="${localUrl("contact/")}">CONTACT</a>
      </nav>
    `;

    const brandClone = sourceBrand.cloneNode(true);
    brandClone.removeAttribute("class");
    brandClone.href = localUrl("");
    brandClone.setAttribute("aria-label", "Digital Equity home");
    brandClone.querySelectorAll("[id]").forEach(node => node.removeAttribute("id"));
    header.querySelector(".fortune-responsive-header__brand").replaceWith(brandClone);
    brandClone.className = "fortune-responsive-header__brand";

    if ((new URL(sourceUrl || "https://www.fortunedigitalequity.org/")).pathname === "/") {
      const actions = document.createElement("div");
      actions.className = "fortune-responsive-actions";
      actions.setAttribute("aria-label", "Page actions");
      actions.innerHTML = `
        <a href="#comp-mbzt50my">CHOOSE A SERVICE</a>
        <a href="#comp-mscrj860">EXPLORE LEARNING PATHS</a>
      `;
      header.append(actions);
    }

    document.body.insertBefore(header, document.querySelector("#SITE_CONTAINER"));
    const toggle = header.querySelector(".fortune-responsive-header__toggle");
    const navigation = header.querySelector(".fortune-responsive-navigation");
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      navigation.dataset.open = String(open);
    });
    navigation.addEventListener("click", event => {
      if (!event.target.closest("a")) return;
      toggle.setAttribute("aria-expanded", "false");
      navigation.dataset.open = "false";
    });
    header.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      toggle.setAttribute("aria-expanded", "false");
      navigation.dataset.open = "false";
      toggle.focus();
    });
  }

  function fitVisualMirrorToViewport() {
    const canvas = document.querySelector("#SITE_CONTAINER");
    if (!canvas || document.documentElement.dataset.fortuneVisualMirror !== "true") return;

    const configuredWidth = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--site-width")
    );
    const designWidth = Number.isFinite(configuredWidth) && configuredWidth > 0
      ? configuredWidth
      : 980;
    const scale = Math.min(1, window.innerWidth / designWidth);
    const fitted = scale < 0.999;

    document.documentElement.dataset.replicaViewportFit = fitted ? "true" : "false";
    document.body.dataset.replicaViewportFit = fitted ? "true" : "false";
    canvas.style.zoom = fitted ? String(scale) : "";
  }

  let pendingViewportFit = 0;
  function scheduleViewportFit() {
    window.cancelAnimationFrame(pendingViewportFit);
    pendingViewportFit = window.requestAnimationFrame(fitVisualMirrorToViewport);
  }

  buildResponsiveNavigation();
  fitVisualMirrorToViewport();
  window.addEventListener("resize", scheduleViewportFit, { passive: true });
  window.addEventListener("orientationchange", scheduleViewportFit, { passive: true });
  window.addEventListener("load", scheduleViewportFit, { once: true });
  document.fonts?.ready?.then(scheduleViewportFit).catch(() => {});

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

  function normalizeAnchorLabel(value) {
    return String(value || "")
      .replace(/\(coming soon\)/gi, "")
      .replace(/[^a-z0-9]+/gi, " ")
      .trim()
      .toLowerCase();
  }

  function restoreSamePageAnchors() {
    const headings = [...document.querySelectorAll("h1, h2, h3, h4, h5, h6")];
    document.querySelectorAll("a[data-anchor]").forEach(link => {
      const label = normalizeAnchorLabel(link.getAttribute("aria-label") || link.textContent);
      if (!label) return;
      const heading = headings.find(candidate => {
        const headingLabel = normalizeAnchorLabel(candidate.textContent);
        return headingLabel === label || headingLabel.startsWith(`${label} `);
      });
      const target = heading?.closest("section[id]");
      if (!target?.id) return;
      link.href = `#${target.id}`;
    });
  }

  restoreSamePageAnchors();

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
  frameUrl.searchParams.set("v", "20260923-responsive-menu-v1");
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
