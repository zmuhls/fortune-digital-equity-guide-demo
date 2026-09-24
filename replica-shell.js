(async () => {
  "use strict";

  const script = document.currentScript;
  if (!script) return;

  const sourceUrl = script.dataset.sourceUrl || "";
  const assetRoot = new URL("./", script.src);
  const mobileSource = script.dataset.mobileSrc;
  const smallViewport = window.matchMedia("(max-width: 767px)");
  const optionalStartupTimeout = 1500;
  if (mobileSource && smallViewport.matches) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), optionalStartupTimeout);
    try {
      const response = await fetch(new URL(mobileSource, document.baseURI), { signal: controller.signal });
      if (!response.ok) throw new Error(`Mobile source returned ${response.status}`);
      const mobile = new DOMParser().parseFromString(await response.text(), "text/html");
      if (mobile.documentElement.dataset.replicaLayout !== "mobile"
          || mobile.querySelector('meta[name="fortune-replica-source"]')?.content !== sourceUrl) {
        throw new Error("Mobile capture identity does not match this page");
      }
      mobile.querySelectorAll("script").forEach(node => node.remove());
      for (const attribute of [...document.documentElement.attributes]) {
        document.documentElement.removeAttribute(attribute.name);
      }
      for (const attribute of mobile.documentElement.attributes) {
        document.documentElement.setAttribute(attribute.name, attribute.value);
      }
      document.head.replaceChildren(...mobile.head.childNodes);
      document.body.replaceWith(mobile.body);
    } catch (error) {
      // A failed optional layout fetch must never leave a blank page.
      console.error("Native mobile layout unavailable", error);
    } finally {
      window.clearTimeout(timeout);
    }
  }
  const nativeMobile = document.documentElement.dataset.replicaLayout === "mobile";
  if (mobileSource || nativeMobile) {
    smallViewport.addEventListener("change", () => window.location.reload());
  }
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
    if (nativeMobile) return;
    if (document.querySelector("#fortune-responsive-header")) return;
    const sourceHeader = document.querySelector("#SITE_HEADER");
    const sourceBrand = sourceHeader?.querySelector('a[aria-label="Homepage"]');
    if (!sourceHeader || !sourceBrand) return;

    const header = document.createElement("header");
    header.id = "fortune-responsive-header";
    header.innerHTML = `
      <div class="fortune-responsive-header__bar">
        <a class="fortune-responsive-header__brand" href="${localUrl("")}" aria-label="Digital Equity home"></a>
        <button class="fortune-responsive-header__toggle" type="button" aria-label="Open navigation menu" aria-expanded="false" aria-controls="fortune-responsive-navigation">
          <span class="fortune-responsive-header__toggle-icon" aria-hidden="true"></span>
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

    document.body.insertBefore(header, document.querySelector("#SITE_CONTAINER"));
    const toggle = header.querySelector(".fortune-responsive-header__toggle");
    const navigation = header.querySelector(".fortune-responsive-navigation");
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
      navigation.dataset.open = String(open);
    });
    navigation.addEventListener("click", event => {
      if (!event.target.closest("a")) return;
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation menu");
      navigation.dataset.open = "false";
    });
    header.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation menu");
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
    const scale = nativeMobile ? window.innerWidth / designWidth : Math.min(1, window.innerWidth / designWidth);
    const fitted = scale < 0.999;

    document.documentElement.dataset.replicaViewportFit = fitted ? "true" : "false";
    document.body.dataset.replicaViewportFit = fitted ? "true" : "false";
    canvas.style.zoom = nativeMobile || fitted ? String(scale) : "";
    sizeMobileNavigation();
  }

  function sizeMobileNavigation() {
    const menu = document.querySelector('[data-replica-mobile-menu][data-replica-open="true"]');
    if (!menu) return;
    const bounds = menu.getBoundingClientRect();
    const scale = menu.offsetWidth > 0 ? bounds.width / menu.offsetWidth : 1;
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    const height = Math.max(44, (viewportHeight - Math.max(0, bounds.top)) / Math.max(scale, .1));
    menu.style.setProperty("--replica-mobile-menu-height", `${height}px`);
  }

  function restoreMobileNavigation() {
    if (!nativeMobile) return;
    const menu = document.querySelector("[data-replica-mobile-menu]");
    const toggle = document.querySelector("[data-replica-mobile-toggle]");
    if (!menu || !toggle) return;
    toggle.setAttribute("role", "button");
    toggle.tabIndex = 0;
    toggle.setAttribute("aria-controls", menu.id);
    const originalToggleDisplay = toggle.style.getPropertyValue("display");
    const originalToggleDisplayPriority = toggle.style.getPropertyPriority("display");
    // Wix's live overlay promotes the original header toggle above the panel.
    // Its inert capture has a lower ancestor stacking context, so keep an
    // actual touch/keyboard close control inside the restored dialog.
    const close = document.createElement("button");
    close.type = "button";
    close.dataset.replicaMobileClose = "true";
    close.setAttribute("aria-label", "Close navigation menu");
    close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5L19 19M19 5L5 19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    menu.append(close);
    const setOpen = open => {
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
      if (open) toggle.style.setProperty("display", "none", "important");
      else if (originalToggleDisplay) toggle.style.setProperty("display", originalToggleDisplay, originalToggleDisplayPriority);
      else toggle.style.removeProperty("display");
      menu.dataset.replicaOpen = String(open);
      menu.toggleAttribute("data-undisplayed", !open);
      menu.setAttribute("aria-hidden", String(!open));
      if (!open) menu.querySelectorAll("details[open]").forEach(detail => { detail.open = false; });
      else { sizeMobileNavigation(); close.focus({ preventScroll: true }); }
    };
    setOpen(false);
    toggle.addEventListener("click", () => setOpen(toggle.getAttribute("aria-expanded") !== "true"));
    close.addEventListener("click", () => { setOpen(false); toggle.focus({ preventScroll: true }); });
    toggle.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle.click(); }
    });
    menu.addEventListener("click", event => { if (event.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
        setOpen(false);
        toggle.focus();
      }
    });
  }

  function restoreCapturedBackgrounds() {
    const repair = nativeMobile || document.documentElement.dataset.replicaViewportFit === "true";
    document.querySelectorAll('[id^="bgMedia_"]').forEach(media => {
      const image = [...media.querySelectorAll("wow-image")].find(node => node.hasAttribute("data-replica-static-background-image") || getComputedStyle(node).position === "sticky");
      if (!image) return;
      // Captured parallax offsets need Wix's live scroll runtime. Keep the
      // original image and crop, but anchor its paint to its own section.
      media.toggleAttribute("data-replica-static-background", repair);
      image.toggleAttribute("data-replica-static-background-image", repair);
    });
  }

  function restoreCapturedImages() {
    document.querySelectorAll("wow-image[data-image-info] img").forEach(image => {
      if (!image.getAttribute("src")?.trim()) {
        try {
          const metadata = JSON.parse(image.closest("wow-image[data-image-info]").getAttribute("data-image-info"));
          const uri = String(metadata.imageData?.uri || "");
          const mime = metadata.imageData?.mimeType || metadata.mimeType;
          const filename = /^[a-z0-9][a-z0-9_.~-]*\.(?:png|jpe?g|gif|webp|avif|svg)$/i;
          const candidate = filename.test(uri) ? new URL(uri, "https://static.wixstatic.com/media/") : new URL(uri);
          if ((!mime || /^image\/(?:png|jpe?g|gif|webp|avif|svg\+xml)$/i.test(mime))
              && candidate.protocol === "https:" && candidate.hostname === "static.wixstatic.com"
              && !candidate.port && !candidate.username && !candidate.password && !candidate.search && !candidate.hash
              && candidate.pathname.startsWith("/media/") && filename.test(candidate.pathname.slice(7))) {
            image.src = candidate.href;
            image.dataset.replicaRestoredImage = "true";
          }
        } catch { /* Keep unknown or unsafe source assets unresolved. */ }
      }
      if (image.dataset.replicaRestoredImage !== "true") return;
      const reveal = () => {
        if (image.naturalWidth <= 0) return;
        if (image.style.visibility === "hidden") image.style.removeProperty("visibility");
        const holder = image.closest('[data-img-loaded="false"]');
        if (holder) holder.dataset.imgLoaded = "true";
      };
      image.addEventListener("load", reveal, { once: true });
      if (image.complete) reveal();
    });
  }

  // A newly opened navigation branch replaces its sibling, as on the source
  // menus. FAQ disclosures are deliberately outside this selector.
  document.addEventListener("toggle", event => {
    const detail = event.target;
    if (!detail.matches?.('details[data-replica-static-menu],details[data-replica-mobile-submenu],.fortune-responsive-navigation > details') || !detail.open) return;
    const navigation = detail.closest('nav,[data-replica-mobile-menu],#SITE_HEADER,.fortune-responsive-navigation');
    navigation?.querySelectorAll('details[data-replica-static-menu],details[data-replica-mobile-submenu],.fortune-responsive-navigation > details').forEach(sibling => {
      if (sibling !== detail && !sibling.contains(detail) && !detail.contains(sibling)) sibling.open = false;
    });
    sizeMobileNavigation();
  }, true);

  let pendingViewportFit = 0;
  function scheduleViewportFit() {
    window.cancelAnimationFrame(pendingViewportFit);
    pendingViewportFit = window.requestAnimationFrame(() => {
      fitVisualMirrorToViewport();
      restoreCapturedBackgrounds();
    });
  }

  buildResponsiveNavigation();
  restoreMobileNavigation();
  fitVisualMirrorToViewport();
  restoreCapturedBackgrounds();
  restoreCapturedImages();
  window.addEventListener("resize", scheduleViewportFit, { passive: true });
  window.addEventListener("orientationchange", scheduleViewportFit, { passive: true });
  window.visualViewport?.addEventListener("resize", scheduleViewportFit, { passive: true });
  window.addEventListener("load", scheduleViewportFit, { once: true });
  document.fonts?.ready?.then(scheduleViewportFit).catch(() => {});

  function restoreNewsSearch() {
    if (!/^\/news(?:\/|$)/.test(new URL(sourceUrl || location.href).pathname)) return;
    document.querySelectorAll('[data-hook="search-input"] .search-input').forEach((holder, index) => {
      const toggle = holder.querySelector('[aria-label="Search"]');
      if (!toggle || holder.dataset.replicaSearchReady === "true") return;
      holder.dataset.replicaSearchReady = "true";
      holder.setAttribute("role", "search");
      holder.setAttribute("aria-label", "Search Fortune’s news");
      const input = document.createElement("input");
      input.id = `fortune-news-search-${index}`;
      input.type = "search";
      input.placeholder = "Search";
      input.setAttribute("aria-label", "Search news on Fortune’s site");
      input.className = "m2yAmK blog-desktop-header-search-text-color blog-desktop-header-search-font search-input__input";
      input.hidden = true;
      holder.append(input);
      toggle.setAttribute("role", "button");
      toggle.tabIndex = 0;
      toggle.setAttribute("aria-controls", input.id);
      const setOpen = open => {
        holder.classList.toggle("ON6A2O", open);
        input.hidden = !open;
        input.style.display = open ? "inline-block" : "none";
        toggle.setAttribute("aria-expanded", String(open));
        if (open) input.focus();
      };
      setOpen(false);
      toggle.addEventListener("click", () => setOpen(input.hidden));
      toggle.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle.click(); }
      });
      input.addEventListener("keydown", event => {
        if (event.key === "Escape") { event.preventDefault(); setOpen(false); toggle.focus(); }
        if (event.key !== "Enter") return;
        event.preventDefault();
        const query = input.value.trim();
        if (query) window.location.assign(`https://www.fortunedigitalequity.org/news/search/${encodeURIComponent(query)}`);
      });
    });
  }
  restoreNewsSearch();

  function restoreNewsCategories() {
    if (!nativeMobile || !/^\/(?:news|post)(?:\/|$)/.test(new URL(sourceUrl || location.href).pathname)) return;
    // These labels and routes are the original blog's native category sheet,
    // independently clicked on September 24, 2026 (also in its desktop nav).
    const categories = [
      ["All Posts", "news/"], ["General", "news/categories/general/"],
      ["Press", "news/categories/press/"], ["Updates", "news/categories/updates/"],
      ["Classes", "news/categories/classes/"], ["Events", "news/categories/events/"],
      ["Student", "news/categories/student/"], ["Fortune Bloggers", "news/categories/fortune-bloggers/"],
    ];
    document.querySelectorAll('[data-replica-static-control-label="true"]').forEach(label => {
      if (label.textContent.trim() !== "Select blog category") return;
      const select = document.createElement("select");
      select.setAttribute("aria-label", "Select blog category");
      select.dataset.replicaNewsCategories = "true";
      select.style.cssText = "display:block;width:100%;min-height:50px;box-sizing:border-box;padding:10px 18px;border:0;border-block:1px solid rgba(var(--navigationTextColor,40,26,57),.3);border-radius:0;background:var(--navigationBackgroundColor,transparent);color:inherit;font-family:var(--navigationFont-family,Arial,sans-serif);font-size:var(--navigationFont-size,14px);font-weight:var(--navigationFont-weight,400);line-height:var(--navigationFont-line-height,1.4)";
      categories.forEach(([text, path]) => {
        const option = new Option(text, localUrl(path));
        option.selected = new URL(sourceUrl).pathname.replace(/\/+$/, "") === `/${path.replace(/\/+$/, "")}`;
        select.append(option);
      });
      select.addEventListener("change", () => window.location.assign(select.value));
      label.replaceWith(select);
    });
  }
  restoreNewsCategories();

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
    const current = new URL(window.location.href);
    const routePath = url => url.pathname.replace(/(?:\/index\.html|\/+)$/, "") || "/";
    document.querySelectorAll('a[href*="#"]').forEach(link => {
      const destination = new URL(link.href, current);
      if (destination.hash && destination.origin === current.origin && routePath(destination) === routePath(current)) link.setAttribute("href", destination.hash);
    });
    document.querySelectorAll("a[data-anchor]").forEach(link => {
      // Native fragments restored at build time include cross-page targets.
      // A heading on this page must never replace one of those destinations.
      const destination = new URL(link.href, window.location.href);
      if (destination.hash) return;
      if (destination.origin !== current.origin || routePath(destination) !== routePath(current)) return;
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

  function alignHashTarget() {
    if (!window.location.hash) return;
    let id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (!target) return;
    // Wix can make body (rather than window) the scroller after viewport fit.
    // Let the browser traverse the actual scroll ancestors and CSS margins.
    target.scrollIntoView({ block: "start", behavior: "instant" });
  }
  const scheduleHashAlignment = () => window.requestAnimationFrame(() => window.requestAnimationFrame(alignHashTarget));
  document.addEventListener("click", event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || !link.hash || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.pushState(null, "", link.hash);
    scheduleHashAlignment();
  });
  window.addEventListener("hashchange", scheduleHashAlignment);
  window.addEventListener("load", scheduleHashAlignment, { once: true });
  document.fonts?.ready?.then(scheduleHashAlignment).catch(() => {});
  scheduleHashAlignment();

  function restoreCapturedCalendar() {
    const picker = document.querySelector('[data-hook="weekly-date-picker"]');
    const agenda = document.querySelector('[data-hook="daily-agenda-content"]');
    if (!picker || !agenda) return;

    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const capturedCaption = picker.querySelector('[data-hook="weekly-date-picker-caption"]')?.textContent || "";
    const capturedYear = Number(capturedCaption.match(/\b\d{4}\b/)?.[0]);
    const capturedLastMonth = months.findIndex(month => capturedCaption.includes(month));
    const capturedAt = new Date(document.querySelector('meta[name="fortune-replica-captured-at"]')?.content || script.dataset.capturedAt || "");
    const captureParts = Number.isFinite(capturedAt.getTime()) ? Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", year: "numeric", month: "numeric",
    }).formatToParts(capturedAt).map(part => [part.type, part.value])) : null;
    if ((!capturedYear || capturedLastMonth < 0) && !captureParts) return;

    const dayMilliseconds = 86400000;
    const currentParts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", year: "numeric", month: "numeric", day: "numeric",
      hour: "numeric", minute: "numeric", hourCycle: "h23",
    }).formatToParts(new Date()).map(part => [part.type, part.value]));
    const today = Date.UTC(Number(currentParts.year), Number(currentParts.month) - 1, Number(currentParts.day));
    const currentMinutes = Number(currentParts.hour) * 60 + Number(currentParts.minute);
    const formatDate = (date, options) => new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC", ...options,
    }).format(date);
    let year = captureParts ? Number(captureParts.year) : capturedYear;
    let previousMonth = -1;
    const entries = [...agenda.querySelectorAll('[data-hook="daily-agenda-day"]')].map((element, index) => {
      const label = element.querySelector('[data-hook="daily-agenda-day-date"]')?.textContent.trim()
        || element.querySelector('section > [role="presentation"]')?.textContent.trim()
        || element.querySelector("section[id]")?.id.replace(/-/g, " ") || "";
      const parsed = label.match(new RegExp(`\\b(${months.join("|")})\\s*(\\d{1,2})(?!\\d)`, "i"));
      const month = parsed ? months.findIndex(name => name.toLowerCase() === parsed[1].toLowerCase()) : -1;
      const day = parsed ? Number(parsed[2]) : 0;
      if (month < 0 || !day) return null;
      // The picker retains the last captured week; rows run forward from the
      // capture's first date, which may be in the previous calendar year.
      if (index === 0 && captureParts && Number(captureParts.month) - 1 - month > 6) year += 1;
      else if (index === 0 && captureParts && month - (Number(captureParts.month) - 1) > 6) year -= 1;
      else if (index === 0 && !captureParts && month > capturedLastMonth) year -= 1;
      if (previousMonth >= 0 && month < previousMonth) year += 1;
      previousMonth = month;
      const date = Date.UTC(year, month, day);
      const slots = [...element.querySelectorAll('[data-hook="daily-agenda-slot"]')].map(slot => {
        const time = slot.querySelector(".ssaqcAw")?.textContent.match(/(\d{1,2}):(\d{2})\s*(am|pm)/i);
        const startMinutes = time ? (Number(time[1]) % 12 + (time[3].toLowerCase() === "pm" ? 12 : 0)) * 60 + Number(time[2]) : null;
        const past = date < today || (date === today && startMinutes !== null && startMinutes <= currentMinutes);
        slot.querySelectorAll("span").forEach(span => {
          if (!span.children.length && /^\d+\s+spots?\s+left$/i.test(span.textContent.trim())) span.remove();
        });
        const action = slot.querySelector('[data-replica-live-action="true"], [data-replica-static-control-label="true"]');
        if (action) {
          const closed = past || /^closed$/i.test(action.textContent.trim());
          const link = document.createElement(closed ? "span" : "a");
          link.className = action.className;
          if (closed) {
            link.setAttribute("aria-disabled", "true");
            link.dataset.replicaStaticControlLabel = "true";
            link.textContent = "CLOSED";
          } else {
            link.href = "https://www.fortunedigitalequity.org/calendar";
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.title = "Open registration on Fortune’s live calendar";
            link.dataset.replicaLiveAction = "true";
            link.textContent = /^register\b/i.test(action.textContent.trim()) ? "REGISTER" : action.textContent.trim();
          }
          link.style.cssText = "white-space:normal;box-sizing:border-box;max-width:100%;height:auto;min-height:44px;padding:8px;text-align:center;line-height:1.35";
          action.replaceWith(link);
        }
        return {
          element: slot,
          service: slot.querySelector(".s__1sSNBn")?.textContent.trim() || "",
          location: slot.querySelector(".sf9j229")?.textContent.trim() || "",
          staff: slot.querySelector(".sHwjQAH")?.textContent.trim() || "",
        };
      });
      return { element, date, slots };
    }).filter(Boolean);
    if (!entries.length) return;

    const calendarRoot = agenda.closest('[data-hook="DailyAgenda-wrapper"]');
    if (calendarRoot) {
      calendarRoot.classList.add("fortune-calendar-restored");
      const fitCalendar = () => {
        const scale = Number.parseFloat(document.querySelector("#SITE_CONTAINER")?.style.zoom) || 1;
        calendarRoot.style.zoom = scale < 1 ? String(1 / scale) : "";
        calendarRoot.style.width = scale < 1 ? `${window.innerWidth}px` : "";
      };
      fitCalendar();
      window.addEventListener("resize", () => window.requestAnimationFrame(fitCalendar), { passive: true });
    }

    const controls = document.createElement("div");
    controls.dataset.replicaCalendarControls = "true";
    controls.style.cssText = "color:#281a39;font:15px/1.4 Arial,Helvetica,sans-serif;display:grid;gap:12px;padding:12px 0";
    const toolbar = document.createElement("div");
    toolbar.style.cssText = "display:flex;flex-wrap:wrap;align-items:center;gap:8px";
    const caption = document.createElement("strong");
    caption.style.cssText = "flex:1;min-width:160px;font-size:20px";
    toolbar.append(caption);
    const days = document.createElement("div");
    days.style.cssText = "display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px";
    const status = document.createElement("p");
    status.setAttribute("aria-live", "polite");
    status.style.margin = "0";
    const captureLabel = captureParts ? `Captured ${new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" }).format(capturedAt)}` : "Captured schedule";
    controls.append(toolbar, days, status);
    picker.replaceChildren(controls);
    const oldWeekAnnouncement = [...document.querySelectorAll('main [aria-live="polite"]')]
      .find(element => element.textContent.startsWith("Week starting"));
    oldWeekAnnouncement?.remove();
    const empty = document.createElement("p");
    empty.textContent = "No captured sessions for these dates or filters. ";
    const liveCalendar = document.createElement("a");
    liveCalendar.href = "https://www.fortunedigitalequity.org/calendar";
    liveCalendar.textContent = "Check the live calendar for the latest schedule.";
    liveCalendar.target = "_blank";
    liveCalendar.rel = "noopener noreferrer";
    liveCalendar.dataset.replicaLiveAction = "true";
    empty.append(liveCalendar);
    empty.style.cssText = "padding:24px 0;font:16px/1.5 Arial,Helvetica,sans-serif;color:#281a39";
    agenda.after(empty);

    const selectedFilters = { service: "", location: "", staff: "" };
    const filterRoot = document.querySelector('[data-hook="filters-root"]');
    if (filterRoot) {
      filterRoot.replaceChildren();
      filterRoot.style.cssText = "display:flex;flex-wrap:wrap;gap:12px;padding:12px 0";
      for (const [field, label] of [["service", "Class Name"], ["location", "Location"], ["staff", "Staff member"]]) {
        const wrapper = document.createElement("label");
        wrapper.style.cssText = "display:grid;gap:4px;flex:1;min-width:160px;font:14px Arial,Helvetica,sans-serif";
        wrapper.append(document.createTextNode(label));
        const select = document.createElement("select");
        select.style.cssText = "min-height:44px;width:100%;min-width:0;max-width:100%;box-sizing:border-box;padding:8px;color:#281a39;background:#fff;border:1px solid #281a39;font:15px Arial,Helvetica,sans-serif";
        select.append(new Option({ service: "All classes", location: "All locations", staff: "All staff members" }[field], ""));
        const values = [...new Set(entries.flatMap(entry => entry.slots.map(slot => slot[field])).filter(Boolean))].sort();
        values.forEach(value => select.append(new Option(value, value)));
        select.addEventListener("change", () => { selectedFilters[field] = select.value; render(); });
        wrapper.append(select);
        filterRoot.append(wrapper);
      }
    }

    let weekStart = today - new Date(today).getUTCDay() * dayMilliseconds;
    let selectedDay = null;
    function button(label, action) {
      const element = document.createElement("button");
      element.type = "button";
      element.textContent = label;
      element.style.cssText = "min-height:44px;padding:8px;border:1px solid #281a39;background:#fff;color:#281a39;font:inherit;cursor:pointer;white-space:pre-line";
      element.addEventListener("click", action);
      return element;
    }
    toolbar.append(
      button("Previous week", () => { weekStart -= 7 * dayMilliseconds; selectedDay = null; render(); }),
      button("This week", () => { weekStart = today - new Date(today).getUTCDay() * dayMilliseconds; selectedDay = null; render(); }),
      button("Next week", () => { weekStart += 7 * dayMilliseconds; selectedDay = null; render(); }),
    );
    function render() {
      const focusedDay = days.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
      caption.textContent = formatDate(weekStart + 3 * dayMilliseconds, { month: "long", year: "numeric" });
      days.replaceChildren();
      for (let offset = 0; offset < 7; offset += 1) {
        const date = weekStart + offset * dayMilliseconds;
        const dayButton = button(formatDate(date, { weekday: "short" }) + "\n" + formatDate(date, { day: "numeric" }), () => {
          selectedDay = selectedDay === date ? null : date;
          render();
        });
        dayButton.setAttribute("aria-label", formatDate(date, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
        dayButton.setAttribute("aria-pressed", String(selectedDay === date));
        if (date === today) dayButton.setAttribute("aria-current", "date");
        if (selectedDay === date) { dayButton.style.background = "#281a39"; dayButton.style.color = "#fff"; }
        days.append(dayButton);
        if (focusedDay === dayButton.getAttribute("aria-label")) dayButton.focus();
      }
      const start = selectedDay ?? (today >= weekStart && today < weekStart + 7 * dayMilliseconds ? today : weekStart);
      const end = selectedDay === null ? weekStart + 7 * dayMilliseconds : selectedDay + dayMilliseconds;
      let visibleCount = 0;
      entries.forEach(entry => {
        let visibleSlots = 0;
        entry.slots.forEach(slot => {
          const matches = Object.entries(selectedFilters).every(([field, value]) => !value || slot[field] === value);
          slot.element.style.setProperty("display", matches ? "" : "none", "important");
          if (matches) visibleSlots += 1;
        });
        const visible = entry.date >= start && entry.date < end && visibleSlots > 0;
        entry.element.style.setProperty("display", visible ? "" : "none", "important");
        if (visible) visibleCount += visibleSlots;
      });
      empty.hidden = visibleCount > 0;
      status.textContent = selectedDay === null
        ? `${captureLabel} · week of ${formatDate(weekStart, { month: "long", day: "numeric", year: "numeric" })}`
        : `${captureLabel} · ${formatDate(selectedDay, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}`;
    }
    render();
  }

  restoreCapturedCalendar();

  let serviceEnhancement = Promise.resolve();
  if (new URL(sourceUrl || "https://www.fortunedigitalequity.org/").pathname.startsWith("/service-page/")) {
    // Optional schedule controls must not prevent the guide from being created.
    // Import cannot be aborted, so ignore a late result after the bounded wait.
    serviceEnhancement = (async () => {
      let timeout;
      try {
        const serviceRuntime = new URL("replica-services.js", assetRoot);
        serviceRuntime.search = new URL(script.src).search;
        await Promise.race([
          import(serviceRuntime.href),
          new Promise((_, reject) => {
            timeout = window.setTimeout(() => reject(new Error("Service controls timed out")), optionalStartupTimeout);
          }),
        ]);
        window.initializeReplicaServices({
          sourceUrl,
          // A native mobile body can have a newer capture than its desktop shell.
          capturedAt: document.querySelector('meta[name="fortune-replica-captured-at"]')?.content || script.dataset.capturedAt,
        });
      } catch (error) {
        // Keep the captured page and guide usable if an optional enhancement fails.
        console.error("Service schedule controls unavailable", error);
      } finally {
        window.clearTimeout(timeout);
      }
    })();
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

  const markReplicaReady = () => {
    fitVisualMirrorToViewport();
    document.documentElement.dataset.replicaReady = "true";
  };
  if (new URLSearchParams(window.location.search).get("guide") === "0") {
    await serviceEnhancement;
    markReplicaReady();
    return;
  }

  const host = document.createElement("div");
  host.id = "fortune-sidecar-host";
  host.dataset.expanded = "false";

  const frame = document.createElement("iframe");
  frame.id = "fortune-sidecar-frame";
  frame.tabIndex = -1;
  frame.title = "Website Guide";
  frame.loading = "eager";
  frame.setAttribute(
    "sandbox",
    "allow-forms allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
  );
  const frameUrl = new URL("sidecar.html", assetRoot);
  frameUrl.searchParams.set("v", "20260924-launcher-hit-area-v2");
  frameUrl.searchParams.set("embed", "1");
  frameUrl.searchParams.set("page", canonicalUrl(sourceUrl) || sourceUrl);
  if (new URLSearchParams(window.location.search).get("open") === "1") {
    frameUrl.searchParams.set("open", "1");
  }
  frame.src = frameUrl.href;
  // The closed iframe may paint decorative rays outside its launcher, but its
  // transparent canvas must not intercept the page's links. Only this exactly
  // measured button accepts input while the guide is closed.
  const launcher = document.createElement("button");
  launcher.type = "button";
  launcher.id = "fortune-sidecar-launcher";
  launcher.hidden = true;
  launcher.setAttribute("aria-label", "Open Website Guide");
  launcher.textContent = "Website Guide";
  launcher.addEventListener("click", () => frame.contentWindow.postMessage({ type: "fortune-sidecar-open" }, window.location.origin));
  for (const type of ["mouseenter", "mouseleave", "focus", "blur"]) {
    launcher.addEventListener(type, () => frame.contentWindow.postMessage({ type: "fortune-sidecar-launcher-hover", active: type === "mouseenter" || type === "focus" }, window.location.origin));
  }
  host.append(frame);
  host.append(launcher);
  document.body.append(host);
  const footerClearance = document.createElement("div");
  footerClearance.id = "fortune-guide-footer-clearance";
  footerClearance.setAttribute("aria-hidden", "true");
  document.body.append(footerClearance);

  window.addEventListener("message", event => {
    if (event.source !== frame.contentWindow || event.origin !== window.location.origin) return;
    const message = event.data || {};
    if (message.type === "fortune-sidecar-state") {
      const wasExpanded = host.dataset.expanded === "true";
      host.dataset.expanded = message.expanded ? "true" : "false";
      frame.tabIndex = message.expanded ? 0 : -1;
      const box = message.launcher;
      const validBox = box && [box.x, box.y, box.width, box.height].every(Number.isFinite) && box.width > 0 && box.height > 0;
      launcher.hidden = Boolean(message.expanded) || !validBox;
      if (validBox) {
        launcher.style.left = `${box.x}px`;
        launcher.style.top = `${box.y}px`;
        launcher.style.width = `${box.width}px`;
        launcher.style.height = `${box.height}px`;
      }
      if (wasExpanded && !message.expanded && !launcher.hidden) launcher.focus({ preventScroll: true });
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
  await serviceEnhancement;
  markReplicaReady();
})();
