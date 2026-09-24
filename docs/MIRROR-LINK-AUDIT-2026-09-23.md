# Mirror navigation audit — September 23, 2026

## Confirmed mirror defects repaired

The visual builder rewrote every official-site URL to a mirror route, including
links explicitly marked as live actions. As a result, 155 captured registration,
submission, sharing, booking, and other action links plus the calendar's live
continuation link reopened inert mirror pages. The current deployed calendar was
confirmed to have all 49 REGISTER links pointing back to its static mirror.

The builder now preserves both live-action markers. Normal page navigation still
stays within the mirror. A browser click on REGISTER in the corrected local
calendar opened the official calendar in a new tab. Clicking REGISTER there
opened the Booking Form for the selected Open Computer Lab Session, with Client
Details, Booking Details, and REGISTER NOW. No fields were entered or submitted.

Four captured embed links depended on Wix's parent-page runtime: the homepage
carousel, Tech Fair and calendar maps, and the members widget. The members URL
returned HTTP 401. These links now open the exact official page that supplied
the embed; expiring Wix instance URLs are no longer published as destinations.
The capture step now applies the same rule to future snapshots.

Native fragments now preserve the source destinations for the homepage service
and learning-path buttons, Program Updates, the contact FAQs link, other training
locations, and support office hours. The old calendar `#class-locations` link now
uses the currently published `#locations` target. Source browser clicks verified
the Program Updates and office-hours destinations. Fragment existence is checked
against the generated target page, including cross-page links.

Opening SERVICES or RESOURCES also exposed captured inline positioning that
expanded the menu item and pushed CONTACT offscreen. Removing that capture-only
positioning lets the existing dropdown CSS keep all navigation labels in place.

## Verification

- All 150 generated pages were audited.
- 4,130 local links/resource destinations resolve; linked fragments exist.
- All 161 live-action destinations are external URLs, never static mirror reloads.
- 31 builder tests, 20 crawler tests, and 18 snapshot-generator tests pass.
- Browser: REGISTER opens the official calendar; office hours navigates to the
  retained `#locations` target.
- Browser: both desktop dropdowns at 1,024 px preserve the closed-menu coordinates,
  keep CONTACT visible, and produce zero horizontal overflow.

The builder now rejects missing local targets/fragments and local live-action
destinations before publication.

The calendar also states its capture date and provides a current-registration
link at the top of the schedule. Captured spot counts do not establish present
availability. Current availability must be read on the official calendar.

## Model read surface

The index is refreshed from the exact manifest-bound snapshots used to build the
mirror: September 21, 2026 at 20:47 UTC, Wix revision 2090. Its 150 pages retain
the existing authority classifications and statuses. The previous index used an
earlier capture of the same revision. Differences comprise blog view counts and
two older news cards no longer on the captured first news page; those posts still
have their own indexed routes.

Live verification on September 23 (September 24 UTC) found exactly the same 150
URLs in the official sitemap/feed inventory, with no missing or extra indexed
routes. All 150 official pages returned HTTP 200 and published Wix revision 2090.
This establishes parity with the currently published site revision; it does not
make captured booking availability or blog view counts live.

577 labeled main-content links now accompany the public text as `source_links`.
They are extracted from actual anchors, with no guessed route associations.
Calendar REGISTER points to the calendar; Contact REGISTER HERE also points to
the calendar. Header/footer navigation and added mirror presentation text are
excluded. The extractor removes only generated slideshow/navigation labels and
the added live-action suffix when recovering the original action label.

## Source-owned failures still present in the official website

The external check fetched 118 unique destinations. These failures were also
verified to be present in the current official HTML, so no replacement was guessed:

| Source page | Destination | Result |
| --- | --- | --- |
| About / Tech Fair | `https://www.nyccaliteracy.org/nycade.html` | HTTP 404 |
| Special Events | `http://c.l.ai.r.a/` | DNS lookup failed |
| Device Distribution | `http://www.ACPBenefit.org` | DNS lookup failed |
| Staff Workshops | `https://jira.fortunesociety.org/servicedesk/customer/portal/4/` | Certificate hostname mismatch |

Other sites returned HTTP 403 to the audit client; that does not establish that
their links fail for ordinary visitors. Older opaque contact-anchor references
also remain in the official source after their anchor was removed; the mirrored
links preserve the still-valid contact-page destination.

This report records local verification before release. Deployment confirmation
must be checked separately against the released revision.
