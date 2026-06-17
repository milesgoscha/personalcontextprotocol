# Onsite Scheduler Widget Test Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a temporary, public, isolated "Onsite Electric" mock-company site on pcp.bio that embeds the Onsite scheduling widget three ways (inline, popup, bring-your-own-button) to prove it renders and works in the wild.

**Architecture:** Four public FastAPI routes under `/widget-test` (no auth, no DB) in the existing control-plane dashboard router, each rendering a Jinja template from a new `templates/widget_test/` folder. All pages extend a self-contained `_layout.html` (the Onsite Electric chrome) that does NOT extend `base.html`, so the site's own Tailwind/HTMX/Alpine never interfere with the embed. A discreet nav link makes it reachable for the logged-in team.

**Tech Stack:** FastAPI, Jinja2, plain HTML/CSS (self-contained, no framework), the external Onsite shim (`https://scheduler.staging.useonsite.com/webscheduler/shim.js`), workspace id `wss_o2PPE2qV0Fl1oQlA`.

**Verification note (why not unit TDD):** The deliverable is a *remotely-loaded, client-side* widget rendering. A Python unit test cannot observe the iframe mounting. So verification is: (a) cheap `curl` checks that each route returns 200 and contains the exact widget markers, and (b) a browser check against the local dev stack that the widget visibly mounts/opens. Both are concrete and required below.

---

## File Structure

- Create `hosted/control/templates/widget_test/_layout.html` — Onsite Electric shared chrome (header, footer, self-contained CSS). One responsibility: the host-site shell.
- Create `hosted/control/templates/widget_test/index.html` — hub page linking the three variants.
- Create `hosted/control/templates/widget_test/inline.html` — homepage-style page with the inline embed.
- Create `hosted/control/templates/widget_test/popup.html` — page with the verbatim modal embed (shim's default button).
- Create `hosted/control/templates/widget_test/custom_button.html` — page with our own branded button as the modal trigger.
- Modify `hosted/control/routes/dashboard.py` — append one clearly-commented block of four routes.
- Modify `hosted/control/templates/components/nav.html` — add one discreet "Widget test" link.

All widget-test code is namespaced under `widget-test` / `widget_test` so teardown is a single revert.

---

### Task 1: Onsite Electric layout shell

**Files:**
- Create: `hosted/control/templates/widget_test/_layout.html`

- [ ] **Step 1: Create the layout template**

```html
<!-- TEMPORARY: Onsite scheduler widget in-the-wild test. Mock host site
     "Onsite Electric". Self-contained — intentionally does NOT extend base.html
     so the site's own styling never interferes with the embedded widget.
     Teardown: delete templates/widget_test/, the /widget-test routes in
     routes/dashboard.py, and the nav link in components/nav.html. -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Onsite Electric{% endblock %}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --navy: #0c2a43; --navy-700: #123a5c; --amber: #f5a623; --amber-600: #e0930c;
      --ink: #14202b; --muted: #5b6b78; --line: #e4e9ee; --bg: #f6f8fa; --white: #fff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: 'Inter', system-ui, -apple-system, sans-serif;
      color: var(--ink); background: var(--bg); line-height: 1.6;
    }
    a { color: inherit; }
    .wrap { max-width: 1080px; margin: 0 auto; padding: 0 24px; }
    /* Top bar */
    .topbar { background: var(--navy); color: #cfe0ee; font-size: 13px; }
    .topbar .wrap { display: flex; justify-content: space-between; align-items: center; height: 38px; }
    .topbar .badge { color: var(--amber); font-weight: 600; }
    /* Header */
    header.site { background: var(--white); border-bottom: 1px solid var(--line); }
    header.site .wrap { display: flex; align-items: center; justify-content: space-between; height: 72px; }
    .brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 20px; color: var(--navy); text-decoration: none; }
    .brand .bolt { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 9px; background: var(--amber); color: var(--navy); font-size: 20px; }
    nav.main { display: flex; gap: 26px; font-weight: 500; color: var(--muted); }
    nav.main a { text-decoration: none; }
    nav.main a:hover { color: var(--navy); }
    .phone { font-weight: 700; color: var(--navy); text-decoration: none; }
    /* Hero */
    .hero { background: linear-gradient(160deg, var(--navy) 0%, var(--navy-700) 100%); color: var(--white); }
    .hero .wrap { padding: 64px 24px; }
    .hero h1 { font-size: 40px; line-height: 1.15; margin: 0 0 14px; font-weight: 800; max-width: 18ch; }
    .hero p { font-size: 18px; color: #c9dbea; margin: 0 0 28px; max-width: 52ch; }
    .pill { display: inline-block; background: var(--amber); color: var(--navy); font-weight: 700;
            padding: 13px 24px; border-radius: 9999px; text-decoration: none; }
    .pill:hover { background: var(--amber-600); }
    /* Sections */
    section.block { padding: 56px 0; }
    h2 { font-size: 26px; font-weight: 700; color: var(--navy); margin: 0 0 8px; }
    .lede { color: var(--muted); margin: 0 0 28px; }
    .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
    .card { background: var(--white); border: 1px solid var(--line); border-radius: 14px; padding: 22px; }
    .card .ic { font-size: 24px; }
    .card h3 { margin: 10px 0 6px; font-size: 17px; color: var(--navy); }
    .card p { margin: 0; color: var(--muted); font-size: 14px; }
    /* Scheduler host panel */
    .scheduler-panel { background: var(--white); border: 1px solid var(--line); border-radius: 16px;
                       padding: 28px; box-shadow: 0 1px 3px rgba(12,42,67,.06); }
    /* Footer */
    footer.site { background: var(--navy); color: #aac2d6; margin-top: 40px; }
    footer.site .wrap { display: flex; flex-wrap: wrap; gap: 40px; padding: 40px 24px; }
    footer.site h4 { color: var(--white); font-size: 14px; margin: 0 0 10px; }
    footer.site .col { font-size: 14px; }
    footer.site .copy { border-top: 1px solid rgba(255,255,255,.1); font-size: 12px; color: #7f9bb3; }
    footer.site .copy .wrap { padding: 16px 24px; }
    .testbar { background: #fff8e6; border-bottom: 1px solid #f3e2b4; color: #6b5310; font-size: 12px; }
    .testbar .wrap { padding: 7px 24px; display: flex; gap: 10px; align-items: center; justify-content: center; }
    @media (max-width: 760px) { .cards { grid-template-columns: 1fr; } nav.main { display: none; } .hero h1 { font-size: 30px; } }
  </style>
  {% block head %}{% endblock %}
</head>
<body>
  <div class="testbar"><div class="wrap">Internal widget test — Onsite Electric is a mock site. <a href="/widget-test">All variants →</a></div></div>
  <div class="topbar"><div class="wrap"><span class="badge">⚡ Licensed &amp; Insured · 24/7 Emergency Service</span><span>Serving the Greater Bay Area</span></div></div>
  <header class="site"><div class="wrap">
    <a class="brand" href="/widget-test"><span class="bolt">⚡</span> Onsite Electric</a>
    <nav class="main"><a href="#services">Services</a><a href="#about">About</a><a href="#reviews">Reviews</a><a href="#schedule">Schedule</a></nav>
    <a class="phone" href="tel:+15105550148">(510) 555-0148</a>
  </div></header>

  {% block body %}{% endblock %}

  <footer class="site">
    <div class="wrap">
      <div class="col"><h4>Onsite Electric</h4>Trusted residential &amp; commercial electricians. Panel upgrades, EV chargers, lighting, diagnostics.</div>
      <div class="col"><h4>Hours</h4>Mon–Sat 7am–7pm<br>24/7 emergency dispatch</div>
      <div class="col"><h4>Service Area</h4>Oakland · Berkeley · Fremont<br>San Jose · Hayward</div>
    </div>
    <div class="copy"><div class="wrap">© 2026 Onsite Electric (mock). License #C10-998877.</div></div>
  </footer>
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/widget_test/_layout.html
git commit -m "feat(widget-test): add Onsite Electric mock-site layout shell"
```

---

### Task 2: Inline embed page

**Files:**
- Create: `hosted/control/templates/widget_test/inline.html`

- [ ] **Step 1: Create the inline page (verbatim inline snippet inside a real page)**

```html
{% extends "widget_test/_layout.html" %}
{% block title %}Onsite Electric — Schedule a Visit{% endblock %}
{% block body %}
<div class="hero"><div class="wrap">
  <h1>Licensed electricians, scheduled in seconds.</h1>
  <p>Panel upgrades, EV charger installs, troubleshooting, and 24/7 emergency service. Book a visit below — no phone tag.</p>
  <a class="pill" href="#schedule">Schedule a visit</a>
</div></div>

<section class="block" id="services"><div class="wrap">
  <h2>What we do</h2>
  <p class="lede">Fully licensed and insured, with upfront pricing.</p>
  <div class="cards">
    <div class="card"><div class="ic">🔌</div><h3>EV Charger Installation</h3><p>Level 2 home charging, permitted and inspected.</p></div>
    <div class="card"><div class="ic">⚡</div><h3>Panel Upgrades</h3><p>100A → 200A service upgrades for modern loads.</p></div>
    <div class="card"><div class="ic">💡</div><h3>Lighting &amp; Diagnostics</h3><p>Recessed lighting, rewires, and fault finding.</p></div>
  </div>
</div></section>

<section class="block" id="schedule"><div class="wrap">
  <h2>Schedule a visit</h2>
  <p class="lede">Pick a time that works for you. We'll confirm by text.</p>
  <div class="scheduler-panel">
    <!-- BEGIN Onsite inline embed (verbatim customer snippet) -->
    <div class="onsite-scheduler" data-onsite-scheduler="wss_o2PPE2qV0Fl1oQlA"></div>
    <script>
      (function (w, d, s, src, n) {
        w[n] = w[n] || function () { (w[n].q = w[n].q || []).push(arguments); };
        var js = d.createElement(s); js.src = src; js.async = true;
        var first = d.getElementsByTagName(s)[0]; first.parentNode.insertBefore(js, first);
      })(window, document, 'script', 'https://scheduler.staging.useonsite.com/webscheduler/shim.js', 'OnsiteScheduler');
      OnsiteScheduler('init', 'wss_o2PPE2qV0Fl1oQlA', { theme: 'adaptive' });
    </script>
    <!-- END Onsite inline embed -->
  </div>
</div></section>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/widget_test/inline.html
git commit -m "feat(widget-test): add inline embed page"
```

---

### Task 3: Popup (modal) embed page

**Files:**
- Create: `hosted/control/templates/widget_test/popup.html`

- [ ] **Step 1: Create the popup page (verbatim modal snippet — shim injects its own button)**

```html
{% extends "widget_test/_layout.html" %}
{% block title %}Onsite Electric — Book Online{% endblock %}
{% block body %}
<div class="hero"><div class="wrap">
  <h1>Need an electrician? Book in one click.</h1>
  <p>Tap the button to open our scheduler. The popup is the Onsite widget in <strong>modal</strong> mode — exactly what a customer pastes.</p>
</div></div>

<section class="block"><div class="wrap">
  <h2>Book your appointment</h2>
  <p class="lede">The button below is rendered and themed by the Onsite widget itself (brand colors from the snippet).</p>
  <div class="scheduler-panel">
    <!-- BEGIN Onsite popup embed (verbatim customer snippet). With no custom trigger
         present, the shim injects its own themed "Book now" button into this div. -->
    <div class="onsite-scheduler" data-onsite-scheduler="wss_o2PPE2qV0Fl1oQlA"></div>
    <script>
      (function (w, d, s, src, n) {
        w[n] = w[n] || function () { (w[n].q = w[n].q || []).push(arguments); };
        var js = d.createElement(s); js.src = src; js.async = true;
        var first = d.getElementsByTagName(s)[0]; first.parentNode.insertBefore(js, first);
      })(window, document, 'script', 'https://scheduler.staging.useonsite.com/webscheduler/shim.js', 'OnsiteScheduler');
      OnsiteScheduler('init', 'wss_o2PPE2qV0Fl1oQlA', { theme: 'light', mode: 'modal', brandColorLight: '#318C7D', brandColorDark: '#573D3D' });
    </script>
    <!-- END Onsite popup embed -->
  </div>
</div></section>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/widget_test/popup.html
git commit -m "feat(widget-test): add popup/modal embed page"
```

---

### Task 4: Bring-your-own-button page

**Files:**
- Create: `hosted/control/templates/widget_test/custom_button.html`

**Why this works (from reading shim.js):** in modal mode the shim's trigger resolver
checks, in order: (1) the `trigger` init option (a CSS selector), (2) any element with
`data-onsite-scheduler-trigger` (blank or matching the workspace id), (3) else it injects
its own button. So adding `data-onsite-scheduler-trigger="wss_o2PPE2qV0Fl1oQlA"` to our own
styled button makes the shim bind that button — no change to the init snippet, which is the
realistic "customer brings their own button" flow. The button must exist in the DOM before
init runs, so it is placed above the script.

- [ ] **Step 1: Create the custom-button page**

```html
{% extends "widget_test/_layout.html" %}
{% block title %}Onsite Electric — Request Service{% endblock %}
{% block head %}
<style>
  .oe-cta { font: inherit; font-weight: 700; cursor: pointer; border: 0; border-radius: 9999px;
            background: var(--amber); color: var(--navy); padding: 14px 28px; font-size: 16px; }
  .oe-cta:hover { background: var(--amber-600); }
  .oe-cta.secondary { background: var(--navy); color: #fff; }
</style>
{% endblock %}
{% block body %}
<div class="hero"><div class="wrap">
  <h1>Your button, our scheduler.</h1>
  <p>These are <strong>our own</strong> branded buttons wired to open the Onsite modal via the
     <code>data-onsite-scheduler-trigger</code> attribute — no widget-injected button.</p>
</div></div>

<section class="block"><div class="wrap">
  <h2>Request service</h2>
  <p class="lede">Click either button — both open the same Onsite scheduler modal.</p>
  <div class="scheduler-panel">
    <!-- Our own buttons. The shim binds any element with data-onsite-scheduler-trigger. -->
    <p style="display:flex; gap:14px; flex-wrap:wrap; margin:0;">
      <button class="oe-cta" data-onsite-scheduler-trigger="wss_o2PPE2qV0Fl1oQlA">⚡ Book a job</button>
      <button class="oe-cta secondary" data-onsite-scheduler-trigger="wss_o2PPE2qV0Fl1oQlA">Schedule an estimate</button>
    </p>

    <!-- Verbatim modal snippet kept for parity. With our triggers present, the shim
         uses them and does NOT inject its own button. The mount div stays empty.
         (Alternative API: pass { trigger: '.oe-cta' } in the init options instead
         of the data attribute — either works.) -->
    <div class="onsite-scheduler" data-onsite-scheduler="wss_o2PPE2qV0Fl1oQlA"></div>
    <script>
      (function (w, d, s, src, n) {
        w[n] = w[n] || function () { (w[n].q = w[n].q || []).push(arguments); };
        var js = d.createElement(s); js.src = src; js.async = true;
        var first = d.getElementsByTagName(s)[0]; first.parentNode.insertBefore(js, first);
      })(window, document, 'script', 'https://scheduler.staging.useonsite.com/webscheduler/shim.js', 'OnsiteScheduler');
      OnsiteScheduler('init', 'wss_o2PPE2qV0Fl1oQlA', { theme: 'light', mode: 'modal', brandColorLight: '#318C7D', brandColorDark: '#573D3D' });
    </script>
  </div>
</div></section>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/widget_test/custom_button.html
git commit -m "feat(widget-test): add bring-your-own-button page"
```

---

### Task 5: Index hub page

**Files:**
- Create: `hosted/control/templates/widget_test/index.html`

- [ ] **Step 1: Create the hub page**

```html
{% extends "widget_test/_layout.html" %}
{% block title %}Onsite Electric — Widget Test{% endblock %}
{% block body %}
<div class="hero"><div class="wrap">
  <h1>Onsite widget — in-the-wild test</h1>
  <p>This is a mock electrician site hosting the Onsite scheduling widget three ways, to
     simulate both the embedder's setup and a customer's experience. Pick a variant.</p>
</div></div>
<section class="block"><div class="wrap">
  <div class="cards">
    <a class="card" href="/widget-test/inline" style="text-decoration:none">
      <div class="ic">📅</div><h3>Inline embed</h3><p>Widget rendered directly in the page (theme: adaptive).</p></a>
    <a class="card" href="/widget-test/popup" style="text-decoration:none">
      <div class="ic">🪟</div><h3>Popup / modal</h3><p>Widget-provided button opens a modal (brand themed).</p></a>
    <a class="card" href="/widget-test/custom-button" style="text-decoration:none">
      <div class="ic">🔘</div><h3>Bring your own button</h3><p>Our branded button triggers the modal.</p></a>
  </div>
</div></section>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/widget_test/index.html
git commit -m "feat(widget-test): add index hub page"
```

---

### Task 6: Add public routes

**Files:**
- Modify: `hosted/control/routes/dashboard.py` (append at end of file, after the `/docs` route)

- [ ] **Step 1: Append the route block**

Add to the very end of `hosted/control/routes/dashboard.py`:

```python


# --- TEMPORARY: Onsite scheduler widget test pages ---
# Public, no-auth, no-DB. Mock host site "Onsite Electric" embedding the Onsite
# scheduler widget. Teardown = delete this block, templates/widget_test/, and the
# nav link in components/nav.html.
# Spec: docs/superpowers/specs/2026-06-17-onsite-widget-test-design.md


@router.get("/widget-test", response_class=HTMLResponse)
async def widget_test_index(request: Request):
    """Hub page linking the widget embed variants."""
    return templates.TemplateResponse("widget_test/index.html", {"request": request})


@router.get("/widget-test/inline", response_class=HTMLResponse)
async def widget_test_inline(request: Request):
    """Inline embed variant."""
    return templates.TemplateResponse("widget_test/inline.html", {"request": request})


@router.get("/widget-test/popup", response_class=HTMLResponse)
async def widget_test_popup(request: Request):
    """Popup/modal embed variant."""
    return templates.TemplateResponse("widget_test/popup.html", {"request": request})


@router.get("/widget-test/custom-button", response_class=HTMLResponse)
async def widget_test_custom_button(request: Request):
    """Bring-your-own-button modal variant."""
    return templates.TemplateResponse("widget_test/custom_button.html", {"request": request})
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/routes/dashboard.py
git commit -m "feat(widget-test): add public /widget-test routes"
```

---

### Task 7: Add discreet nav link for the team

**Files:**
- Modify: `hosted/control/templates/components/nav.html`

- [ ] **Step 1: Add a link inside the sidebar `<nav>`**

Add this as a new section just before the closing `</nav>` of the main sidebar navigation
(after the last `Access Control` link block). Match the existing link styling used by the
other nav `<a>` elements:

```html
<!-- TEMPORARY: Onsite widget test (remove with the widget-test feature) -->
<div class="mb-6">
  <p class="px-3 mb-2 text-xs font-medium text-surface-400 uppercase tracking-wider">Testing</p>
  <a href="/widget-test"
     class="group flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 text-surface-600 hover:bg-surface-100 hover:text-surface-900">
    <svg class="w-5 h-5 text-surface-400 group-hover:text-surface-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 10V3L4 14h7v7l9-11h-7z"/>
    </svg>
    Widget test
  </a>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add hosted/control/templates/components/nav.html
git commit -m "feat(widget-test): add discreet sidebar link"
```

---

### Task 8: Verify in the local dev stack

**Files:** none (verification only)

- [ ] **Step 1: Start the dev stack**

Run:
```bash
cd hosted/docker && docker compose -f docker-compose.dev.yml up -d --build
```
Expected: control plane, Postgres, and Traefik come up healthy. The control plane is served at `http://pcp.localhost` via Traefik.

- [ ] **Step 2: Curl each route — expect HTTP 200 and the widget markers present**

Run:
```bash
for p in "" /inline /popup /custom-button; do
  echo "== /widget-test$p =="
  curl -fsS -o /tmp/wt.html -w "HTTP %{http_code}\n" "http://pcp.localhost/widget-test$p"
  grep -c "wss_o2PPE2qV0Fl1oQlA" /tmp/wt.html
done
```
Expected: each request prints `HTTP 200`. The index prints `0` matches (it only links variants); `/inline`, `/popup`, and `/custom-button` each print `1` or more (workspace id present). If `pcp.localhost` does not resolve, retry with `--resolve pcp.localhost:80:127.0.0.1` or `-H "Host: pcp.localhost" http://localhost/widget-test$p`.

- [ ] **Step 3: Browser check (the real proof)**

Open each URL in a browser and confirm:
- `/widget-test/inline` — the scheduler iframe mounts inline in the "Schedule a visit" panel.
- `/widget-test/popup` — a themed "Book now" button appears; clicking it opens the modal with the scheduler iframe.
- `/widget-test/custom-button` — our amber "Book a job" / navy "Schedule an estimate" buttons appear (no widget-injected button); clicking either opens the modal.
- Check the browser devtools Network tab: `shim.js` loads (200) and the portal iframe (`portal.staging.useonsite.com/book/...`) loads.

Note any variant that does not mount and report it rather than assuming success.

- [ ] **Step 4: Tear down the dev stack (optional)**

Run:
```bash
cd hosted/docker && docker compose -f docker-compose.dev.yml down
```

---

## Teardown (when testing is done)

Single revert: delete `hosted/control/templates/widget_test/`, the `/widget-test` route block
in `hosted/control/routes/dashboard.py`, and the nav link block in
`hosted/control/templates/components/nav.html`. No other code references the widget.

---

## Self-Review

**Spec coverage:**
- Two simulated experiences (embedder paste + visitor in-context) → verbatim snippets (Tasks 2–4) inside the Onsite Electric site (Task 1). ✓
- One page per variant (mount-guard rationale) → Tasks 2, 3, 4. ✓
- Inline / popup / custom-button variants → Tasks 2, 3, 4. ✓
- Custom-button unknown API resolved by reading shim.js → resolved (data-attribute trigger), Task 4. ✓
- Public no-auth routes, pretty URLs, index hub → Tasks 5, 6. ✓
- Isolation from base.html/auth/DB → `_layout.html` standalone (Task 1), routes take only `request` (Task 6). ✓
- Discoverability via discreet team link → Task 7. ✓
- No CSP concern → confirmed during design; no header changes needed. ✓
- Teardown in one revert → namespaced; teardown section. ✓
- Verification via curl + browser → Task 8. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/undefined references. All template and route code is complete.

**Type/name consistency:** Workspace id `wss_o2PPE2qV0Fl1oQlA`, script URL, template paths (`widget_test/*.html`), and route paths (`/widget-test*`) match across all tasks. CSS vars used in `custom_button.html` (`--amber`, `--amber-600`, `--navy`) are all defined in `_layout.html`.
