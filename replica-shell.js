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
      // Native fragments restored at build time include cross-page targets.
      // A heading on this page must never replace one of those destinations.
      const destination = new URL(link.href, window.location.href);
      if (destination.hash) return;
      const current = new URL(window.location.href);
      const routePath = url => url.pathname.replace(/(?:\/index\.html|\/+)$/, "") || "/";
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

  function restoreCapturedCalendar() {
    const picker = document.querySelector('[data-hook="weekly-date-picker"]');
    const agenda = document.querySelector('[data-hook="daily-agenda-content"]');
    if (!picker || !agenda) return;

    const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    const capturedCaption = picker.querySelector('[data-hook="weekly-date-picker-caption"]')?.textContent || "";
    const capturedYear = Number(capturedCaption.match(/\b\d{4}\b/)?.[0]);
    const capturedLastMonth = months.findIndex(month => capturedCaption.includes(month));
    if (!capturedYear || capturedLastMonth < 0) return;

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
    let year = capturedYear;
    let previousMonth = -1;
    const entries = [...agenda.querySelectorAll('[data-hook="daily-agenda-day"]')].map((element, index) => {
      const label = element.querySelector('[data-hook="daily-agenda-day-date"]')?.textContent.trim() || "";
      const month = months.findIndex(name => label.startsWith(`${name} `));
      const day = Number(label.match(/\b(\d{1,2})\b/)?.[1]);
      if (month < 0 || !day) return null;
      // The picker retains the last captured week; rows run forward from the
      // capture's first date, which may be in the previous calendar year.
      if (index === 0 && month > capturedLastMonth) year -= 1;
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
          const link = document.createElement("a");
          link.className = action.className;
          link.href = "https://www.fortunedigitalequity.org/calendar";
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.dataset.replicaLiveAction = "true";
          link.textContent = past ? "Past session · view live calendar" : "Check live availability";
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
        ? `Captured schedule · week of ${formatDate(weekStart, { month: "long", day: "numeric", year: "numeric" })}`
        : `Captured schedule · ${formatDate(selectedDay, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}`;
    }
    render();
  }

  restoreCapturedCalendar();

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
