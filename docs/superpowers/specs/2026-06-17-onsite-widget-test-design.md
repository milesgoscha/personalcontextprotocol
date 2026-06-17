# Onsite Scheduler Widget — In-the-Wild Test Pages

**Date:** 2026-06-17
**Status:** Approved design, pending spec review

## Goal

Prove that the Onsite scheduling widget renders and works when embedded on a real,
public website (pcp.bio). The test should simulate, as faithfully as possible, two
real experiences:

1. **The embedder's experience** — a user of the Onsite platform copy/pasting the
   provided snippet into their own site. So we paste the snippets *verbatim*.
2. **The visitor's experience** — a customer landing on a site that has the widget
   embedded. So the snippets live inside realistic, in-context page content rather
   than a bare test harness.

This is a temporary, throwaway test surface. It will be removed in a single revert
commit when testing is done.

## Constraints / non-goals

- **Do not** modify `base.html`, auth, user nodes, the dashboard data paths, or any
  production data path.
- **Do not** load the Onsite script anywhere except these dedicated test pages.
- No CSP exists on the site, so the external script loads without header changes.
- Pages are public (no auth) — stakeholders without accounts can reach them by URL.
- Keep everything namespaced under `widget-test` for trivial teardown.

## The widget

Both snippets load the same script and init the same workspace id. They must **not**
share a page (double script load + duplicate `init` on the same mount `<div>` would
make the test misleading), so each variant gets its own page.

- **Script:** `https://scheduler.staging.useonsite.com/webscheduler/shim.js` (staging)
- **Workspace id:** `wss_o2PPE2qV0Fl1oQlA`
- **Inline init:** `OnsiteScheduler('init', 'wss_o2PPE2qV0Fl1oQlA', { theme: 'adaptive' })`
- **Popup init:** `OnsiteScheduler('init', 'wss_o2PPE2qV0Fl1oQlA', { theme: 'light', mode: 'modal', brandColorLight: '#318C7D', brandColorDark: '#573D3D' })`

## Routes

Added as one clearly-commented, easy-to-delete block in
`hosted/control/routes/dashboard.py`. All public (no `require_auth`).

| URL | Contents |
|---|---|
| `/widget-test` | Index hub: short blurb + links to the three variants. The shareable URL. |
| `/widget-test/inline` | Inline snippet, verbatim, inside a realistic page |
| `/widget-test/popup` | Modal snippet, verbatim, inside a realistic page |
| `/widget-test/custom-button` | Modal triggered by our own button ("bring your own button") |

## Templates

New folder `hosted/control/templates/widget_test/`:

- `_layout.html` — minimal local layout (own `<html>`/`<head>`/`<body>`). Does **not**
  extend `base.html`, so Tailwind CDN / HTMX / Alpine / dashboard chrome can't
  interfere with the embed. Provides just a clean, realistic page shell.
- `index.html` — hub page linking the variants.
- `inline.html`, `popup.html`, `custom_button.html` — the three variant pages.

### Realistic host content — "Onsite Electric"

The host site is a polished, believable small electrical-services company,
**Onsite Electric** (mirrors the Onsite staging mock brand). Goal: it should look
like a genuine customer website a visitor would land on, so the widget is tested in
real context — not a bare harness.

Shared chrome (in `_layout.html`): a real-looking header with logo/nav
(Services / About / Contact / phone number), and a footer (hours, service area,
copyright). A simple, professional electrician aesthetic — clean type, a deep blue /
amber palette, licensed-&-insured trust cues. Self-contained styling in the layout
(no dependence on `base.html` or its Tailwind config). Quality matters here: this is
the part that sells "embedded on a real site."

Per-variant body content:

- **inline.html** — a homepage-style page (hero: "Licensed electricians, scheduled
  in seconds"; a few service cards). The inline widget sits in a natural
  "Schedule a visit" section mid-page.
- **popup.html** — the same site shell; the verbatim modal snippet, using whatever
  default trigger the modal mode provides.
- **custom_button.html** — the same site shell with a styled "Book a job" CTA button
  (matching the Onsite Electric brand) wired to open the modal ourselves
  (the bring-your-own-button case).

## Custom-button trigger (unknown API)

Onsite's API for opening the modal from a custom element is not yet known. Before
implementing `custom_button.html`:

1. Fetch and read the public `shim.js` to find the real trigger mechanism (a data
   attribute the shim binds to, or a queued call such as `OnsiteScheduler('open')`).
2. Wire the button to that mechanism.
3. If the mechanism cannot be determined confidently, implement a best-guess button,
   and explicitly flag what must be manually verified — do not claim it works unverified.

## Discoverability

A discreet "Widget test" link in the dashboard sidebar nav
(`templates/components/nav.html`) for the logged-in team. Stakeholders without
accounts use the public `pcp.bio/widget-test` URL directly.

## Teardown

Everything is namespaced under `widget-test`:
- one route block in `dashboard.py`
- one template folder `templates/widget_test/`
- one nav link in `components/nav.html`

Removal = single revert commit. The only external references are the staging script
URL and the workspace id.

## Verification

Run the site locally (`hosted/docker/docker-compose.dev.yml`) and load each page to
confirm the external script loads and the widget mounts/opens, before calling it done.
