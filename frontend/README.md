# Frontend

Next.js (App Router) + TypeScript (strict) + Tailwind + TanStack Query. See the main
[README](../README.md) for how to run it with the rest of the stack.

```bash
npm install
npm run dev          # http://localhost:3000 (the API must be running on :8000)
npm run lint && npm run typecheck && npm test
npm run gen:api      # regenerate src/lib/api-schema.ts from the API's OpenAPI schema
npm run e2e          # Playwright smoke test (needs `make up` and a configured LLM)
```

Layout: `src/app` pages, `src/components` UI pieces, `src/lib` API client, validation and
formatting, `src/__tests__` unit tests, `e2e/` browser smoke test.
