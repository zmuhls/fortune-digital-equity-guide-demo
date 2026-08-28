import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  CALENDAR_URL,
  CalendarAgendaError,
  canonicalCalendarPdfUrl,
  normalizeCalendarAgendaCapture,
  parseArgs,
  validateCalendarAgendaArtifact,
  writeCalendarAgendaArtifact,
} from "../scripts/capture_calendar_agenda.mjs";


const PDF_URL =
  "https://www.fortunedigitalequity.org/_files/ugd/e58568_9ba96e2dcd284a19afd0c0c0d71fe06c.pdf";


function sourceFixture() {
  return {
    calendar_url: CALENDAR_URL,
    captured_at: "2026-08-28T01:56:30.901Z",
    week: {
      label: "August 2026",
      days: [
        "Sunday, August 23, 2026",
        "Monday, August 24, 2026",
        "Tuesday, August 25, 2026",
        "Wednesday, August 26, 2026",
        "Thursday, August 27, 2026",
        "Friday, August 28, 2026",
        "Saturday, August 29, 2026",
      ],
      selected: "Friday, August 28, 2026",
    },
    agenda_days: [
      {
        date: "August 28",
        weekday: "Friday",
        events: [{
          title: "Tech Time: Foundations",
          text: "Tech Time: Foundations 1:00 pm (1 hr) Raysean Richardson Main Office (LIC) 15 spots left REGISTER",
          values: [
            "Tech Time: Foundations",
            "(1 hr)",
            "Raysean Richardson",
            "Main Office (LIC)",
            "15 spots left",
            "REGISTER",
          ],
          has_register: true,
        }],
      },
      {
        date: "September 1",
        weekday: "Tuesday",
        events: [{
          title: "What Is AI?",
          text: "What Is AI? 2:00 pm (1 hr 30 min) Jacob Schwartz Jacob Schwartz Main Office (LIC) 15 spots left REGISTER",
          values: [
            "What Is AI?",
            "(1 hr 30 min)",
            "Jacob Schwartz",
            "Jacob Schwartz",
            "Main Office (LIC)",
            "15 spots left",
            "REGISTER",
          ],
          has_register: true,
        }],
      },
    ],
    pdf_candidates: [{
      url: PDF_URL,
      label: "AI Month Class Schedule",
    }],
  };
}


test("normalizes rendered Daily Agenda rows into a source-bound artifact", () => {
  const artifact = normalizeCalendarAgendaCapture(sourceFixture());

  assert.deepEqual(artifact, {
    schema_version: 2,
    captured_at: "2026-08-28T01:56:30Z",
    calendar: {
      url: CALENDAR_URL,
      label: "View the official calendar",
    },
    week: {
      label: "August 2026",
      days: [
        "Sunday, August 23, 2026",
        "Monday, August 24, 2026",
        "Tuesday, August 25, 2026",
        "Wednesday, August 26, 2026",
        "Thursday, August 27, 2026",
        "Friday, August 28, 2026",
        "Saturday, August 29, 2026",
      ],
      selected: "Friday, August 28, 2026",
    },
    agenda: {
      source_url: CALENDAR_URL,
      continuation_url: CALENDAR_URL,
      events: [
        {
          date: "2026-08-28",
          date_label: "Friday, August 28, 2026",
          title: "Tech Time: Foundations",
          start_time: "1:00 pm",
          duration: "1 hr",
          location: "Main Office (LIC)",
          registration: {
            url: CALENDAR_URL,
            label: "Check availability and register",
          },
        },
        {
          date: "2026-09-01",
          date_label: "Tuesday, September 1, 2026",
          title: "What Is AI?",
          start_time: "2:00 pm",
          duration: "1 hr 30 min",
          location: "Main Office (LIC)",
          registration: {
            url: CALENDAR_URL,
            label: "Check availability and register",
          },
        },
      ],
    },
    pdf: {
      url: PDF_URL,
      label: "AI Month Class Schedule",
    },
  });
});


test("does not persist volatile capacity or a nonportable Wix booking URL", () => {
  const artifact = normalizeCalendarAgendaCapture(sourceFixture());
  const eventText = JSON.stringify(artifact.agenda.events);

  assert.doesNotMatch(eventText, /spots left/i);
  assert.doesNotMatch(eventText, /booking-form/i);
  assert.equal(artifact.agenda.events[0].registration.url, CALENDAR_URL);
});


test("rejects unsafe calendar downloads and incomplete agenda rows", () => {
  assert.throws(
    () => canonicalCalendarPdfUrl("https://example.test/current.pdf"),
    CalendarAgendaError,
  );
  const withoutLocation = sourceFixture();
  withoutLocation.agenda_days[0].events[0].values = [
    "Tech Time: Foundations",
    "(1 hr)",
    "REGISTER",
  ];
  assert.throws(() => normalizeCalendarAgendaCapture(withoutLocation), /location/);

  const extraPdf = sourceFixture();
  extraPdf.pdf_candidates.push({
    url: "https://www.fortunedigitalequity.org/_files/ugd/e58568_different.pdf",
    label: "Another schedule",
  });
  assert.throws(() => normalizeCalendarAgendaCapture(extraPdf), /exactly one/);

  const incompleteWeek = sourceFixture();
  incompleteWeek.week.days.pop();
  assert.throws(() => normalizeCalendarAgendaCapture(incompleteWeek), /seven visible days/);
});


test("validates a normalized artifact without a network request", () => {
  const artifact = normalizeCalendarAgendaCapture(sourceFixture());
  assert.deepEqual(validateCalendarAgendaArtifact(artifact), artifact);
});


test("requires an explicit output path and atomically replaces it", async () => {
  assert.throws(() => parseArgs([]), /--output is required/);
  const options = parseArgs(["--output", "/tmp/calendar-source.json"]);
  assert.equal(options.outputPath, "/tmp/calendar-source.json");

  const directory = await mkdtemp(path.join(os.tmpdir(), "fortune-calendar-agenda-"));
  const output = path.join(directory, "calendar-source.json");
  const artifact = normalizeCalendarAgendaCapture(sourceFixture());
  try {
    await writeCalendarAgendaArtifact(output, artifact);
    assert.deepEqual(JSON.parse(await readFile(output, "utf8")), artifact);
    assert.deepEqual(await readdir(directory), ["calendar-source.json"]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
