# Show Your Green Impact

Every token your team prevents from being processed shows up in our public counter. If you want, you can show that impact on your own site, your README, your sustainability report, or your email signature, using a free embed kit we put together.

Whether you're on the free GitHub release or a commercial license, the community counter is the same and the badges are the same.

This page tells you how.

---

## Quick version (for email)

> **You're part of the jCodeMunch-MCP impact program.**
>
> Every token your team prevents adds to our public CO₂ counter, currently sitting at over 100,000 kg CO₂ saved across the community. If you want to show that on your site, grab a badge, a live widget, or an embeddable SVG from `https://github.com/jgravelle/jcodemunch-mcp/blob/main/badge-kit.md`. All three are free, configurable, and link to a public verification page.
>
> No tracking, no required attribution, no strings. Just a clean way to show you are reducing AI's carbon footprint.

---

## The numbers come from this page

All of the embed options pull the live total from our public counter at:

```
https://j.gravelle.us/APIs/savings/total.php
```

Returns JSON. Anyone can query it. The current kg-CO₂ figure is computed from `total_tokens × 0.00000012`, which is the upper-bound estimate from our SCI for AI case study. Methodology is published in the [225-Billion-Token Case Study Against SCI for AI](https://github.com/jgravelle/jcodemunch-mcp/wiki/Token-Reduction-as-an-Energy%E2%80%90Efficiency-Action:-A-225%E2%80%90Billion%E2%80%90Token-Case-Study-Against-SCI-for-AI).

Click any badge or widget and the visitor lands on the impact page, where the full math, the equivalents, and the verification trail are spelled out. That page is the procurement-defensible destination for the badge.

---

## Option 1: README badge (one line of Markdown)

Best for: GitHub READMEs, GitLab project pages, plaintext-friendly contexts. shields.io-style SVG, two-tone, auto-refreshes once per hour.

### Default (dark, kg)
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php)](https://jcodemunch.com/impact.php)
```markdown
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php)](https://jcodemunch.com/impact.php)
```

Renders as a clickable badge that links to the impact page.

### Light theme
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?theme=light)](https://jcodemunch.com/impact.php)
```markdown
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?theme=light)](https://jcodemunch.com/impact.php)
```

### Imperial units (pounds)
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?units=lbs)](https://jcodemunch.com/impact.php)
```markdown
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?units=lbs)](https://jcodemunch.com/impact.php)
```

### Tonnes (good for larger numbers)
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?units=tonnes)](https://jcodemunch.com/impact.php)
```markdown
[![CO₂ prevented by jCodeMunch-MCP](https://jcodemunch.com/badge.php?units=tonnes)](https://jcodemunch.com/impact.php)
```

### Combine options

Query params stack:
[![CO₂ prevented](https://jcodemunch.com/badge.php?theme=light&units=lbs)](https://jcodemunch.com/impact.php)
```markdown
[![CO₂ prevented](https://jcodemunch.com/badge.php?theme=light&units=lbs)](https://jcodemunch.com/impact.php)
```

### HTML version (for sites that don't render Markdown)
<a href="https://jcodemunch.com/impact.php">
  <img src="https://jcodemunch.com/badge.php" alt="CO₂ prevented by jCodeMunch-MCP">
</a>

```html
<a href="https://jcodemunch.com/impact.php">
  <img src="https://jcodemunch.com/badge.php" alt="CO₂ prevented by jCodeMunch-MCP">
</a>
```

---

## Option 2: Live JS widget (richer display)

Best for: marketing site footers, sustainability dashboards, About pages. Card format, larger, updates every 60 seconds, fully styled.

### Basic embed
<div data-jcodemunch-impact></div>
<script src="https://jcodemunch.com/embed.js" async></script>

```html
<div data-jcodemunch-impact></div>
<script src="https://jcodemunch.com/embed.js" async></script>
```

That's it. The widget injects its own CSS, fetches the live counter, and renders a card. Whole card is clickable to the impact page by default.

### Configured
<div data-jcodemunch-impact
     data-units="lbs"
     data-theme="light"
     data-compact="false"
     data-link="true">
</div>
<script src="https://jcodemunch.com/embed.js" async></script>

```html
<div data-jcodemunch-impact
     data-units="lbs"
     data-theme="light"
     data-compact="false"
     data-link="true">
</div>
<script src="https://jcodemunch.com/embed.js" async></script>
```

### Compact (for tight spaces, e.g. email signatures, sidebars)
<div data-jcodemunch-impact data-compact="true"></div>
<script src="https://jcodemunch.com/embed.js" async></script>

```html
<div data-jcodemunch-impact data-compact="true"></div>
<script src="https://jcodemunch.com/embed.js" async></script>
```

### Configuration reference

| Attribute | Values | Default | What it does |
|---|---|---|---|
| `data-units` | `kg`, `lbs`, `tonnes` | `kg` | Which unit to display |
| `data-theme` | `dark`, `light` | `dark` | Color palette |
| `data-link` | `true`, `false` | `true` | Whether the whole card is a clickable link to the impact page |
| `data-compact` | `true`, `false` | `false` | Smaller form factor |

The widget injects a small style block on first load. No external font files, no jQuery, no framework dependencies, no third-party trackers. Works anywhere modern JavaScript runs.

---

## Option 3: Direct SVG (download and host yourself)

Best for: print materials, PDF reports, places where you cannot reference a remote image. The image is static at point of download, so it will not auto-update.

### Download URLs

Right-click and save as:

- Dark, kg: `https://jcodemunch.com/badge.php`
- Light, kg: `https://jcodemunch.com/badge.php?theme=light`
- Dark, tonnes: `https://jcodemunch.com/badge.php?units=tonnes`

The SVG includes the current number at time of download. If you need it to stay current, use Option 1 or Option 2 instead.

---

## Common questions

### Does my license tier affect what I can display?

No. Anyone using jCodeMunch-MCP, free GitHub release or commercial license, can use any of the badges, widgets, or SVGs. The community total reflects everyone's combined impact. We don't gate the badges by tier or by purchase status.

### Can I show my own team's contribution instead of the community total?

Not yet. The current badges show the aggregate community total. Per-organization attribution is on the roadmap and will require opt-in license-key telemetry. If you want to be on the early list when that ships, email `jjgravelle [at] gmail.com` with the subject line "per-team badge interest" and we will let you know when it's live.

### Can I edit the badge text?

The SVG badge text is generated server-side and isn't user-configurable beyond theme and units. If you need custom wording for an internal sustainability report, download the SVG and edit the text directly in any vector editor. Please don't misrepresent the number or claim individual credit for the community total.

### How do I report my own per-team savings to procurement?

Two options. First, every LLM provider's API returns `usage.input_tokens` per call. Log it with and without jCodeMunch-MCP in your toolchain and compute the per-task reduction directly. Second, the full SCI for AI methodology is at `https://j.gravelle.us/jCodeMunch/case-study`, which procurement teams can audit against the published numbers.

### Is this greenwashing?

We are very mindful of that line. SCI for AI explicitly rejects carbon offsets, RECs, PPAs, and other financial-instrument-based "reductions." The only kind of reduction the standard credits is elimination, meaning fewer GPU-seconds actually consumed. That's what jCodeMunch-MCP does: it causes fewer tokens to be processed, which means fewer GPU-seconds, which means less energy used. The number on the badge represents tokens that were never sent to inference. No offsets are involved.

### Can I use the badge if I run jCodeMunch in CI but not as a developer?

Yes. Any deployment that contributes to the public counter contributes to the community total. CI usage counts.

### What if the API goes down?

The badges and widgets degrade gracefully. The SVG badge will return the last cached value (1-hour cache). The JS widget will keep the previously rendered number on screen and silently retry. The impact page will fall back to a sensible default. No 404s, no broken images, no error states surfaced to your visitors.

### Can I host the embed.js file myself to avoid third-party dependencies?

Yes. Download it from `https://jcodemunch.com/embed.js`, host it on your CDN, and replace the script src in the embed code. The widget still calls our public API for the live number, but the JavaScript itself can live anywhere.

---

## Tell us what you build

If you put the badge somewhere interesting (an annual report, a conference talk, a customer-facing dashboard), we would love to know. Send the URL to `jjgravelle [at] gmail.com` and we will add notable placements to a community wall on the impact page.

No obligation. The badge is a gift, not a transaction.

---

*jCodeMunch-MCP is maintained by J. Gravelle, GSF-credentialed Green Software Practitioner. The community impact counter, the SCI for AI case study, and the full methodology are at `https://j.gravelle.us/jCodeMunch/case-study`.*
