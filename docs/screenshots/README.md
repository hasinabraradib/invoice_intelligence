# Screenshots

Captured from the running stack (`make up`) with synthetic sample documents, in light and dark
mode (`prefers-color-scheme`), with reduced motion so nothing is caught mid-animation.

| Page | Desktop (1440 x 900) | Phone (375 px) |
|---|---|---|
| Assistant | `assistant-light.png`, `assistant-dark.png` | |
| Dashboard | `dashboard-light.png`, `dashboard-dark.png` | `dashboard-mobile-light.png`, `dashboard-mobile-dark.png` |
| Documents | `documents-light.png`, `documents-dark.png` | |
| Review (batch PDF) | `review-light.png`, `review-dark.png` | `review-mobile-light.png`, `review-mobile-dark.png` |
| Review queue | `review-queue-light.png`, `review-queue-dark.png` | |
| Cross-check | `cross-check-light.png`, `cross-check-dark.png` | |
| Search | `search-light.png`, `search-dark.png` | |

To retake them, open each page in a browser with device emulation, or script it with
Playwright (`page.emulateMedia({ colorScheme, reducedMotion: "reduce" })`).
