(() => {
  "use strict";

  const ZONE = "America/New_York";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // Observed by clicking both REGISTER buttons on the official service page.
  // Other services keep their captured destination; their booking URLs are not inferred.
  const VERIFIED_BOOKING = new Map([
    ["/service-page/tech-time-foundations-1", "https://www.fortunedigitalequity.org/booking-calendar/tech-time-foundations-1?timezone=America%2FNew_York&location=&referral=service_details_widget"],
  ]);

  function localParts(date) {
    return Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: ZONE, year: "numeric", month: "numeric", day: "numeric",
      hour: "numeric", minute: "numeric", hourCycle: "h23",
    }).formatToParts(date).filter(part => part.type !== "literal").map(part => [part.type, Number(part.value)]));
  }

  function liveLink(url, label) {
    const link = document.createElement("a");
    link.href = url;
    link.textContent = label;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.replicaLiveAction = "true";
    return link;
  }

  window.initializeReplicaServices = ({ sourceUrl, capturedAt } = {}) => {
    let source;
    try {
      source = new URL(sourceUrl || document.querySelector('meta[name="fortune-replica-source"]')?.content || "");
    } catch { return false; }
    if (!/^www\./.test(source.hostname)) source.hostname = `www.${source.hostname}`;
    if (source.hostname !== "www.fortunedigitalequity.org" || !source.pathname.startsWith("/service-page/")) return false;
    const main = document.querySelector("main");
    if (!main || main.dataset.replicaServicesReady) return false;
    main.dataset.replicaServicesReady = "true";

    if (!document.querySelector("#fortune-service-controls-style")) {
      const style = document.createElement("style");
      style.id = "fortune-service-controls-style";
      style.textContent = `
        .fortune-service-session[hidden], .fortune-service-day[hidden], .fortune-service-agenda[hidden] { display:none !important; }
        .fortune-service-status { font:inherit; font-size:13px; line-height:1.5; margin:12px 0; }
        .fortune-service-status a, .fortune-service-more-dates { color:inherit; text-decoration:underline; text-underline-offset:3px; }
        .fortune-service-location { font:inherit; font-size:14px; color:inherit; background:transparent; border:0; max-width:100%; min-height:44px; padding:8px 24px 8px 0; cursor:pointer; }
        .fortune-service-location:focus-visible { outline:2px solid currentColor; outline-offset:2px; }
        .fortune-service-empty { font:inherit; font-size:14px; line-height:1.5; margin:16px 0; }
        [data-replica-service-closed] { opacity:.7; cursor:default; }
      `;
      document.head.append(style);
    }

    const captureValue = capturedAt || document.querySelector('meta[name="fortune-replica-captured-at"]')?.content || document.querySelector('script[data-captured-at]')?.dataset.capturedAt;
    const captureDate = new Date(captureValue || "");
    const hasCaptureDate = Number.isFinite(captureDate.getTime());
    const captured = hasCaptureDate ? localParts(captureDate) : null;
    const now = localParts(new Date());
    const today = Date.UTC(now.year, now.month - 1, now.day);
    const currentMinute = now.hour * 60 + now.minute;
    const schedule = main.querySelector('[data-hook="scheduling-section"]');
    const days = [...(schedule?.querySelectorAll('[data-hook="daily-sessions"]') || [])];
    const sessions = [];
    let year = captured?.year;
    let previousMonth = captured ? captured.month - 1 : null;
    let unknownDates = 0;

    days.forEach((day, index) => {
      day.classList.add("fortune-service-day");
      const dateText = day.querySelector('[data-hook="date"]')?.textContent || "";
      const match = dateText.match(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?:,?\s+(\d{4}))?\b/);
      const month = match ? MONTHS.indexOf(match[1]) : -1;
      if (match?.[3]) year = Number(match[3]);
      else if (index === 0 && month >= 0 && previousMonth !== null && month - previousMonth > 6) year -= 1;
      else if (month >= 0 && previousMonth !== null && month < previousMonth && (index > 0 || previousMonth - month > 6)) year += 1;
      if (month >= 0) previousMonth = month;
      const date = Number.isFinite(year) && match ? Date.UTC(year, month, Number(match[2])) : NaN;
      if (!Number.isFinite(date)) unknownDates += 1;
      for (const row of day.querySelectorAll('[data-hook="session-details"]')) {
        row.classList.add("fortune-service-session");
        const time = row.querySelector('[data-hook="start-time"]')?.textContent.match(/(\d{1,2}):(\d{2})\s*(am|pm)/i);
        const startMinute = time ? (Number(time[1]) % 12 + (/pm/i.test(time[3]) ? 12 : 0)) * 60 + Number(time[2]) : null;
        const expired = Number.isFinite(date) && (date < today || (date === today && startMinute !== null && startMinute <= currentMinute));
        sessions.push({ row, day, expired, location: row.querySelector('[data-hook="location"]')?.textContent.trim() || "" });
      }
    });

    const ended = days.length > 0 && unknownDates === 0 && sessions.length > 0 && sessions.every(session => session.expired);
    const liveDestination = VERIFIED_BOOKING.get(source.pathname.replace(/\/$/, "")) || source.href;
    const registerLinks = [...main.querySelectorAll('[data-hook="book-button-wrapper"] a[data-replica-live-action]')];
    for (const link of registerLinks) {
      if (ended) {
        const closed = document.createElement("span");
        closed.className = link.className;
        closed.dataset.replicaServiceClosed = "true";
        closed.textContent = "CLOSED";
        link.replaceWith(closed);
      } else {
        if (VERIFIED_BOOKING.has(source.pathname.replace(/\/$/, ""))) link.href = liveDestination;
        link.textContent = unknownDates ? "CHECK LIVE AVAILABILITY" : "REGISTER";
        link.setAttribute("aria-label", "Check availability and register on the official Digital Equity site");
      }
    }

    if (!schedule) return true;
    const title = schedule.querySelector('[data-hook="scheduling-title"]');
    if (unknownDates && title) title.textContent = "Captured Sessions";
    const notice = document.createElement("p");
    notice.className = "fortune-service-status";
    notice.setAttribute("role", "note");
    const stamp = hasCaptureDate ? new Intl.DateTimeFormat("en-US", { timeZone: ZONE, month: "short", day: "numeric", year: "numeric" }).format(captureDate) : null;
    notice.append(ended ? "Captured sessions have ended. " : stamp ? `Schedule captured ${stamp}. ` : "This is a captured schedule. ");
    notice.append(liveLink(source.href, "Check live availability."));
    title?.after(notice);

    // The capture cannot substantiate an ever-moving “next 31 days” window.
    for (const element of schedule.querySelectorAll("p,span,div")) {
      if (!element.children.length && /^No sessions in the next \d+ days\.?$/.test(element.textContent.trim())) {
        element.textContent = "No upcoming sessions were listed when this page was captured.";
      }
    }

    const empty = document.createElement("p");
    empty.className = "fortune-service-empty";
    empty.setAttribute("role", "status");
    empty.hidden = true;
    empty.textContent = "No upcoming sessions in this snapshot for this location. Check live availability for current dates.";
    const agenda = schedule.querySelector('[data-hook="scheduling-agenda"]');
    agenda?.classList.add("fortune-service-agenda");
    agenda?.after(empty);
    const filterHost = schedule.querySelector('[data-hook="floating-dropdown-base"]') || schedule.querySelector('[data-hook="location-selection-dropdown"]');
    const locationText = main.querySelector('[data-hook="details-locations"]')?.textContent || "";
    const locations = [...new Set([...locationText.split("|").map(value => value.trim()), ...sessions.map(session => session.location)].filter(Boolean))];
    let select = null;
    if (filterHost && locations.length) {
      select = document.createElement("select");
      select.className = "fortune-service-location";
      select.setAttribute("aria-label", "Location");
      for (const location of ["All Locations", ...locations]) {
        const option = document.createElement("option");
        option.value = location === "All Locations" ? "" : location;
        option.textContent = location;
        select.append(option);
      }
      filterHost.replaceChildren(select);
    }
    const render = () => {
      let visible = 0;
      for (const session of sessions) {
        session.row.hidden = session.expired || Boolean(select?.value && session.location !== select.value);
        if (!session.row.hidden) visible += 1;
      }
      for (const day of days) day.hidden = !sessions.some(session => session.day === day && !session.row.hidden);
      if (agenda) agenda.hidden = days.length > 0 && visible === 0;
      empty.hidden = days.length === 0 || visible > 0;
    };
    select?.addEventListener("change", render);
    render();

    // The captured first page does not contain the provider's later pages.
    // A plainly labeled live handoff is preferable to an inert or fictitious Next.
    const pagination = schedule.querySelector('[data-hook="sessions-pagination"]');
    if (pagination) {
      const more = liveLink(source.href, "More dates on live site");
      more.className = "fortune-service-more-dates";
      pagination.replaceChildren(more);
    }
    return true;
  };
})();
