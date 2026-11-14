# Screenshots

Captured from the running stack (`make up`) with synthetic sample documents only, in light and
dark mode (`prefers-color-scheme`), with reduced motion so nothing is caught mid-animation.

- `*.png` in this folder: the current design ("after").
- `before/`: the previous design (indigo accent, white sidebar), kept for comparison.

| Page | Desktop (1440 x 900) | Phone (375 px) |
|---|---|---|
| Assistant | `assistant-light.png`, `assistant-dark.png` | |
| Dashboard | `dashboard-light.png`, `dashboard-dark.png` | `dashboard-mobile-light.png`, `dashboard-mobile-dark.png` |
| Documents | `documents-light.png`, `documents-dark.png`; date filter open: `documents-date-filter-*.png` | |
| Review (batch PDF) | `review-light.png`, `review-dark.png` | `review-mobile-light.png`, `review-mobile-dark.png` |
| Review queue | `review-queue-light.png`, `review-queue-dark.png` | |
| Cross-check | `cross-check-light.png`, `cross-check-dark.png`; document picker open: `cross-check-picker-*.png` | |
| Search | `search-light.png`, `search-dark.png` | |

## What changed in the redesign

- **Palette.** A warm cream page, near-white cards and a solid near-black sidebar with a
  soft highlight on the active item. The indigo accent is gone: primary actions are black
  pills (light pills in dark mode) and secondary actions are outlined pills.
- **Pastel summary blocks.** Butter, dusty pink, sage and periwinkle cards with dark text and
  one faint CSS/SVG shape each. They are used only for summaries: dashboard KPIs, the
  assistant's "right now" tiles and the cross-check summary. Tables and the review panel stay
  calm.
- **Status pills** use the same pastels and always carry a word: sage processed, butter needs
  review, periwinkle processing, pink failed, peach partly failed.
- **Greeting.** A large greeting with one live line under it ("5 invoices need your review and
  5 failed."), built from `/stats` by a tested helper.
- **Custom controls, no browser-default widgets:**
  - one Select built on Radix UI (same height and radius as inputs, themed menu, keyboard
    support);
  - a searchable document picker showing file type, supplier, invoice count and date;
  - one "Invoice date" range control, empty by default ("Any date"), with presets and a themed
    calendar;
  - a date field with a calendar in the field editor;
  - a switch instead of a checkbox.
- **Filters** sit in one compact toolbar, with removable chips and "Clear all".
- **Disabled buttons** are outlined and muted (not faded black). Hovering or focusing one shows
  a short reason, e.g. "Pick two documents to compare".
- **Removed:** the indigo, the gradients (sparkline fill, chat fade), backdrop blur, the
  "sparkles" icon and glyph icons, generic placeholders ("e.g. acme", "Choose a document…",
  "e.g. Ayesha") and pre-filled date inputs.
- **Charts.** Thin ink lines, rounded ink bars (the latest day in butter), dashed horizontal
  gridlines only, and a pastel status donut.
- **Accessibility.** Body and muted text meet WCAG AA in both themes, checked by script
  (pill text 7:1 or better, muted text 5.3:1 or better). Focus rings are visible on every
  control (a scripted Tab pass), and the sidebar has its own light ring.
- **Checks.** Every page was checked at 375, 768 and 1440 px in both themes, with no horizontal
  scrolling.
