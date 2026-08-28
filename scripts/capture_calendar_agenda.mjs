#!/usr/bin/env node

/**
 * Capture the public, rendered Daily Agenda from Fortune's Calendar page.
 *
 * This writes a small, source-bound JSON record for the static mirror. It
 * deliberately does not reproduce Wix's booking form: each event returns a
 * visitor to the official Calendar, where availability and registration are
 * live.
 */

import { randomBytes } from "node:crypto";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

import { firefox } from "playwright";


const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, "..");
export const CALENDAR_URL = "https://www.fortunedigitalequity.org/calendar";
export const CALENDAR_HOSTS = new Set([
  "fortunedigitalequity.org",
  "www.fortunedigitalequity.org",
]);
export const DEFAULT_TIMEOUT_MS = 60_000;
export const VIEWPORT = Object.freeze({ width: 1440, height: 1200 });

const MONTHS = new Map([
  ["january", 0], ["february", 1], ["march", 2], ["april", 3],
  ["may", 4], ["june", 5], ["july", 6], ["august", 7],
  ["september", 8], ["october", 9], ["november", 10], ["december", 11],
]);


export class CalendarAgendaError extends Error {
  constructor(message) {
    super(message);
    this.name = "CalendarAgendaError";
  }
}


function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}


function requiredText(value, label) {
  const text = cleanText(value);
  if (!text) throw new CalendarAgendaError(`${label} is missing`);
  return text;
}


function requiredOption(argv, index, option) {
  const value = argv[index + 1];
  if (!value || value.startsWith("--")) {
    throw new CalendarAgendaError(`${option} requires a value`);
  }
  return value;
}


function positiveInteger(value, option) {
  if (!/^\d+$/.test(value) || Number(value) < 1 || !Number.isSafeInteger(Number(value))) {
    throw new CalendarAgendaError(`${option} must be a positive integer`);
  }
  return Number(value);
}


export function canonicalCalendarUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch (_error) {
    throw new CalendarAgendaError("calendar URL is invalid");
  }
  if (
    parsed.protocol !== "https:" ||
    !CALENDAR_HOSTS.has(parsed.hostname) ||
    parsed.port ||
    parsed.username ||
    parsed.password ||
    parsed.pathname.replace(/\/+$/, "") !== "/calendar"
  ) {
    throw new CalendarAgendaError("calendar URL must be the official Digital Equity calendar");
  }
  return CALENDAR_URL;
}


export function canonicalCalendarPdfUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch (_error) {
    throw new CalendarAgendaError("calendar PDF URL is invalid");
  }
  if (
    parsed.protocol !== "https:" ||
    !CALENDAR_HOSTS.has(parsed.hostname) ||
    parsed.port ||
    parsed.username ||
    parsed.password ||
    !/^\/_files\/ugd\/[^/?#]+\.pdf$/i.test(parsed.pathname)
  ) {
    throw new CalendarAgendaError("calendar PDF must be an official Fortune download");
  }
  return parsed.href;
}


export function normalizeCaptureTimestamp(value) {
  const parsed = new Date(String(value || ""));
  if (Number.isNaN(parsed.getTime())) {
    throw new CalendarAgendaError("capture timestamp is invalid");
  }
  return parsed.toISOString().replace(/\.\d{3}Z$/, "Z");
}


function normalizeCalendarWeek(rawWeek) {
  if (!rawWeek || typeof rawWeek !== "object") {
    throw new CalendarAgendaError("calendar week strip is missing");
  }
  const label = requiredText(rawWeek.label, "calendar week label");
  const days = Array.isArray(rawWeek.days)
    ? rawWeek.days.map((day) => requiredText(day, "calendar week day"))
    : [];
  if (days.length !== 7) {
    throw new CalendarAgendaError("calendar week strip must contain seven visible days");
  }
  for (const day of days) parseAnchorDate(day);
  const selected = requiredText(rawWeek.selected, "calendar week selected day");
  parseAnchorDate(selected);
  if (!days.includes(selected)) {
    throw new CalendarAgendaError("calendar week selected day is not in the visible strip");
  }
  return { label, days, selected };
}


function parseAnchorDate(value) {
  const match = cleanText(value).match(
    /(?:^|,\s*)([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$/,
  );
  const month = match && MONTHS.get(match[1].toLowerCase());
  if (!match || month === undefined) {
    throw new CalendarAgendaError("agenda anchor date is invalid");
  }
  const day = Number(match[2]);
  const year = Number(match[3]);
  const date = new Date(Date.UTC(year, month, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month ||
    date.getUTCDate() !== day
  ) {
    throw new CalendarAgendaError("agenda anchor date is invalid");
  }
  return date;
}


function isoDateForAgendaDay(monthDay, anchorDate) {
  const match = cleanText(monthDay).match(/^([A-Za-z]+)\s+(\d{1,2})$/);
  const month = match && MONTHS.get(match[1].toLowerCase());
  if (!match || month === undefined) {
    throw new CalendarAgendaError("agenda event date is invalid");
  }
  const day = Number(match[2]);
  const candidates = [-1, 0, 1].map((offset) => {
    const candidate = new Date(Date.UTC(anchorDate.getUTCFullYear() + offset, month, day));
    if (candidate.getUTCMonth() !== month || candidate.getUTCDate() !== day) return null;
    return candidate;
  }).filter(Boolean);
  if (candidates.length === 0) throw new CalendarAgendaError("agenda event date is invalid");
  candidates.sort((left, right) => (
    Math.abs(left.getTime() - anchorDate.getTime()) -
    Math.abs(right.getTime() - anchorDate.getTime())
  ));
  return candidates[0].toISOString().slice(0, 10);
}


function dateLabel(isoDate, weekday) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  const month = Array.from(MONTHS.keys())[date.getUTCMonth()];
  const monthName = month.slice(0, 1).toUpperCase() + month.slice(1);
  return `${requiredText(weekday, "agenda weekday")}, ${monthName} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
}


function normalizeTime(value) {
  const match = cleanText(value).match(/\b(\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?))\b/i);
  if (!match) throw new CalendarAgendaError("agenda event start time is missing");
  return match[1].replace(/\./g, "").replace(/\s+/g, " ").toLowerCase();
}


function normalizeDuration(value) {
  const match = cleanText(value).match(/\(([^)]*\b(?:hr|min)\b[^)]*)\)/i);
  if (!match) throw new CalendarAgendaError("agenda event duration is missing");
  return cleanText(match[1]);
}


function isRegisterLabel(value) {
  return /^(?:register|book(?:\s+now)?|join(?:\s+now)?)$/i.test(cleanText(value));
}


function isAvailabilityLabel(value) {
  return /(?:\bspots?\s+left\b|\bwaitlist\b|\bsold\s+out\b|\bunavailable\b|\bfull\b)/i.test(cleanText(value));
}


export function normalizeAgendaEvent(rawEvent, day, anchorDate, calendarUrl) {
  if (!rawEvent || typeof rawEvent !== "object") {
    throw new CalendarAgendaError("agenda event is invalid");
  }
  const values = Array.isArray(rawEvent.values)
    ? rawEvent.values.map((value) => cleanText(value)).filter(Boolean)
    : [];
  const title = requiredText(rawEvent.title || values[0], "agenda event title");
  const rawText = requiredText(rawEvent.text, "agenda event text");
  const registerIndex = values.findIndex(isRegisterLabel);
  if (!rawEvent.has_register && registerIndex < 0) {
    throw new CalendarAgendaError(`agenda event ${title} does not expose registration`);
  }
  const availabilityIndex = values.findIndex(isAvailabilityLabel);
  const locationIndex = availabilityIndex > 0
    ? availabilityIndex - 1
    : registerIndex > 0
      ? registerIndex - 1
      : -1;
  // Index 0 is the title and index 1 is the printed duration. A location
  // cannot be inferred unless it follows both of those source fields.
  if (locationIndex < 2) {
    throw new CalendarAgendaError(`agenda event ${title} has no readable location`);
  }

  const date = isoDateForAgendaDay(day.date, anchorDate);
  return {
    date,
    date_label: dateLabel(date, day.weekday),
    title,
    start_time: normalizeTime(rawText),
    duration: normalizeDuration(rawText),
    location: requiredText(values[locationIndex], "agenda event location"),
    registration: {
      url: calendarUrl,
      label: "Check availability and register",
    },
  };
}


export function normalizeCalendarAgendaCapture(rawCapture) {
  if (!rawCapture || typeof rawCapture !== "object") {
    throw new CalendarAgendaError("calendar capture is invalid");
  }
  const calendarUrl = canonicalCalendarUrl(rawCapture.calendar_url || CALENDAR_URL);
  const capturedAt = normalizeCaptureTimestamp(rawCapture.captured_at);
  const week = normalizeCalendarWeek(rawCapture.week);
  const anchorDate = parseAnchorDate(week.selected);
  const days = Array.isArray(rawCapture.agenda_days) ? rawCapture.agenda_days : [];
  const events = days.flatMap((rawDay) => {
    if (!rawDay || typeof rawDay !== "object") {
      throw new CalendarAgendaError("agenda day is invalid");
    }
    const day = {
      date: requiredText(rawDay.date, "agenda day date"),
      weekday: requiredText(rawDay.weekday, "agenda day weekday"),
    };
    if (!Array.isArray(rawDay.events)) {
      throw new CalendarAgendaError("agenda day events are invalid");
    }
    return rawDay.events.map((event) => normalizeAgendaEvent(event, day, anchorDate, calendarUrl));
  });
  if (events.length === 0) {
    throw new CalendarAgendaError("official calendar has no readable agenda events");
  }

  const pdfCandidates = Array.isArray(rawCapture.pdf_candidates) ? rawCapture.pdf_candidates : [];
  const pdfs = pdfCandidates.map((candidate) => ({
    url: canonicalCalendarPdfUrl(candidate?.url),
    label: requiredText(candidate?.label, "calendar PDF label"),
  }));
  const distinctPdfs = Array.from(
    new Map(pdfs.map((pdf) => [`${pdf.url}\u0000${pdf.label}`, pdf])).values(),
  );
  if (distinctPdfs.length !== 1) {
    throw new CalendarAgendaError("official calendar must expose exactly one readable PDF action");
  }

  return {
    schema_version: 2,
    captured_at: capturedAt,
    calendar: {
      url: calendarUrl,
      label: "View the official calendar",
    },
    week,
    agenda: {
      source_url: calendarUrl,
      continuation_url: calendarUrl,
      events,
    },
    pdf: distinctPdfs[0],
  };
}


export function validateCalendarAgendaArtifact(artifact) {
  if (!artifact || artifact.schema_version !== 2) {
    throw new CalendarAgendaError("calendar agenda artifact has an unsupported schema");
  }
  return normalizeCalendarAgendaCapture({
    calendar_url: artifact.calendar?.url,
    captured_at: artifact.captured_at,
    week: artifact.week,
    agenda_days: groupArtifactEventsByDay(artifact.agenda?.events),
    pdf_candidates: [artifact.pdf],
  });
}


function groupArtifactEventsByDay(events) {
  if (!Array.isArray(events)) return [];
  const days = new Map();
  for (const event of events) {
    const match = cleanText(event?.date_label).match(/^([^,]+),\s+([A-Za-z]+\s+\d{1,2}),\s+\d{4}$/);
    if (!match) throw new CalendarAgendaError("calendar agenda artifact has an invalid event date label");
    const key = `${match[2]}\u0000${match[1]}`;
    if (!days.has(key)) days.set(key, { date: match[2], weekday: match[1], events: [] });
    days.get(key).events.push({
      title: event.title,
      text: `${event.title} ${event.start_time} (${event.duration}) ${event.location} REGISTER`,
      values: [event.title, event.duration, event.location, "REGISTER"],
      has_register: event.registration?.url === CALENDAR_URL,
    });
  }
  return Array.from(days.values());
}


/**
 * Runs inside Playwright's page context. Keep this browser-only layer shallow;
 * normalization and validation happen in pure functions above.
 */
export function readRenderedCalendarModel() {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (element) => {
    const style = getComputedStyle(element);
    return element.getClientRects().length > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0";
  };
  // Wix exposes the full date as visible grid-cell text, but not consistently
  // as a DOM aria-label. The selected cell keeps the year anchor stable when
  // a captured agenda crosses a month or year boundary.
  const grid = document.querySelector('[role="grid"]');
  const gridCells = Array.from(grid?.querySelectorAll('[role="gridcell"]') || []);
  const fullDateLabel = (element) => clean(element?.innerText).match(
    /([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s*\d{4})$/,
  )?.[1] || "";
  const selectedCell = gridCells.find(
    (element) => element.getAttribute("aria-selected") === "true",
  );
  const gridText = clean(grid?.closest('[role="region"]')?.innerText);
  const weekLabel = gridText.match(
    /\b([A-Za-z]{3,9}(?:\s*-\s*[A-Za-z]{3,9})?\s+\d{4})\b/,
  )?.[1] || "";
  const week = {
    label: weekLabel,
    days: gridCells.map(fullDateLabel),
    selected: fullDateLabel(selectedCell),
  };
  const agendaDays = Array.from(
    document.querySelectorAll('[data-hook="daily-agenda-content"] [data-hook="daily-agenda-day"]'),
  ).filter(visible).map((day) => {
    const dateNode = day.querySelector('[data-hook="daily-agenda-day-date"]');
    const date = clean(dateNode?.textContent);
    const weekday = Array.from(dateNode?.parentElement?.querySelectorAll("span") || [])
      .map((element) => clean(element.textContent))
      .find((value) => value && value !== date) || "";
    const events = Array.from(day.querySelectorAll('[data-hook="daily-agenda-slot"]'))
      .filter(visible)
      .map((slot) => {
        const values = Array.from(slot.querySelectorAll("span"))
          .filter((element) => !element.querySelector("span"))
          .map((element) => clean(element.textContent))
          .filter(Boolean);
        const registerLabel = Array.from(slot.querySelectorAll("button"))
          .map((element) => clean(element.textContent))
          .find((value) => /^(?:register|book(?:\s+now)?|join(?:\s+now)?)$/i.test(value));
        return {
          title: values[0] || "",
          text: clean(slot.innerText),
          values,
          has_register: Boolean(registerLabel),
        };
      });
    return { date, weekday, events };
  });
  const pdfCandidates = Array.from(document.querySelectorAll("a[href]"))
    .filter(visible)
    .map((link) => {
      let url = "";
      try {
        url = new URL(link.getAttribute("href") || "", window.location.href).href;
      } catch (_error) {
        return null;
      }
      const label = clean(link.innerText) ||
        clean(link.getAttribute("aria-label")) ||
        clean(link.querySelector("img")?.getAttribute("alt"));
      return { url, label };
    })
    .filter((candidate) => candidate && /\/_files\/ugd\/[^/?#]+\.pdf(?:[?#]|$)/i.test(candidate.url));
  return {
    week,
    agenda_days: agendaDays,
    pdf_candidates: pdfCandidates,
  };
}


export function parseArgs(argv) {
  const options = {
    calendarUrl: CALENDAR_URL,
    outputPath: null,
    timeoutMs: DEFAULT_TIMEOUT_MS,
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (argument === "--output") {
      options.outputPath = path.resolve(requiredOption(argv, index, argument));
      index += 1;
    } else if (argument === "--url") {
      options.calendarUrl = canonicalCalendarUrl(requiredOption(argv, index, argument));
      index += 1;
    } else if (argument === "--timeout-ms") {
      options.timeoutMs = positiveInteger(requiredOption(argv, index, argument), argument);
      index += 1;
    } else {
      throw new CalendarAgendaError(`unknown option: ${argument}`);
    }
  }
  if (!options.help && !options.outputPath) {
    throw new CalendarAgendaError("--output is required");
  }
  return options;
}


export function helpText() {
  return `Usage: node scripts/capture_calendar_agenda.mjs --output PATH [options]

Capture the currently rendered public Daily Agenda and its visible calendar PDF action.

Options:
  --output PATH       JSON artifact destination (required)
  --url URL           Official Calendar URL (default: ${CALENDAR_URL})
  --timeout-ms N      Navigation and selector timeout in milliseconds
  -h, --help          Show this help
`;
}


export async function writeCalendarAgendaArtifact(outputPath, artifact) {
  const resolved = path.resolve(outputPath);
  const directory = path.dirname(resolved);
  const temporary = path.join(
    directory,
    `.${path.basename(resolved)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`,
  );
  await mkdir(directory, { recursive: true });
  try {
    await writeFile(temporary, `${JSON.stringify(artifact, null, 2)}\n`, {
      encoding: "utf8",
      flag: "wx",
    });
    await rename(temporary, resolved);
  } catch (error) {
    await rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}


export async function captureCalendarAgenda(options) {
  const browser = await firefox.launch({ headless: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    locale: "en-US",
    timezoneId: "America/New_York",
    colorScheme: "light",
    reducedMotion: "reduce",
    serviceWorkers: "block",
    storageState: { cookies: [], origins: [] },
    extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
  });
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(options.timeoutMs);
    const response = await page.goto(options.calendarUrl, {
      waitUntil: "domcontentloaded",
      timeout: options.timeoutMs,
    });
    if (!response || response.status() !== 200) {
      throw new CalendarAgendaError("official calendar did not return HTTP 200");
    }
    if (canonicalCalendarUrl(page.url()) !== options.calendarUrl) {
      throw new CalendarAgendaError("official calendar redirected outside the approved route");
    }
    await page.locator('[data-hook="daily-agenda-content"]').waitFor({ state: "visible" });
    await page.locator('[data-hook="daily-agenda-slot"]').first().waitFor({ state: "visible" });
    await page.locator('a[href*="/_files/ugd/"]').first().waitFor({ state: "visible" });
    const rawCapture = await page.evaluate(readRenderedCalendarModel);
    return normalizeCalendarAgendaCapture({
      ...rawCapture,
      calendar_url: options.calendarUrl,
      captured_at: new Date().toISOString(),
    });
  } finally {
    await context.close();
    await browser.close();
  }
}


export async function run(options) {
  const artifact = await captureCalendarAgenda(options);
  await writeCalendarAgendaArtifact(options.outputPath, artifact);
  return artifact;
}


async function main() {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      process.stdout.write(helpText());
      return;
    }
    const artifact = await run(options);
    process.stdout.write(
      `captured ${artifact.agenda.events.length} official calendar events to ${options.outputPath}\n`,
    );
  } catch (error) {
    process.stderr.write(`calendar agenda capture failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}


const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) await main();
