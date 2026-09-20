# SummaryStrip responsive regression

`chat-ui/src/ui/primitives/SummaryStrip.jsx` owns the layout of its metric
cells, not its placement in a page or section. Its columns use
`repeat(auto-fit, minmax(min(100%, 10rem), 1fr))`: available parent width and
item count determine the number of equal-width tracks. The preferred 10rem
minimum leaves room for labels and padding, while the 100% cap lets a single
track fit parents narrower than that minimum.

Labels wrap without ellipsis; long unbroken labels, values, and visible
details can break within words. The existing radius and color tokens, minimum
cell height, spacing, item filtering, and mobile detail visibility are retained.
The outer strip clips its corners; individual cells do not assume first/last
items occupy the ends of a single row.

Tradeoffs: narrow panels become taller instead of hiding text. An incomplete
last row retains the same column widths and may have an empty final position.
The component does not expand its parent page-grid span or change schemas.

## Run

With the web shell's existing dependencies and Playwright Chromium installed:

```sh
cd web_shell
node --test tests/summaryStrip.browser.test.js
```

The test bundles the actual component with esbuild and compiles the shipped
Tailwind stylesheet. It serves an isolated fixture on an ephemeral loopback
port, denies unrelated browser requests, and closes its own server/browser.
It does not use a running app, credentials, or generated source.

Coverage includes 1, 3, 4, and 6 metrics in a 276px compact parent and 1120px
wide parent at a 1440px viewport, plus a 390px mobile viewport with 16px page
padding. It checks complete text, DOM text bounds, page/cell overflow,
non-overlap, equal-width tracks, and absence of unused trailing columns on the
first row. Additional cases cover container-only resizing, pathological long
text down to a 120px parent, and existing empty/invalid-item filtering.

Screenshots are written to `web_shell/test-results/summary-strip/` (ignored).
`SUMMARY_STRIP_SCREENSHOT_DIR` optionally selects a separate evidence directory
for red/green runs. Inspect the compact, wide, and mobile screenshots alongside
the geometric assertions; this fixture does not replace generated-app visual
acceptance or cross-browser coverage.
