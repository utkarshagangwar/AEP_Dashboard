# AEP Dashboard — Frontend

Next.js (App Router) client for the AEP dashboard. Every page is a client component that
fetches through a Next.js API-route proxy layer to the FastAPI backend — this app holds no
business logic, no database access, and (today) no real auth enforcement of its own; FastAPI
is the single source of truth for both.

> Keep this file in sync with the code. Where the codebase has drifted (dead dependencies,
> two competing implementations of the same thing, half-finished migrations) this doc says so
> explicitly — see [Known issues](#known-issues-found-2026-07-15) — rather than describing the
> intended end state as if it were shipped.

## Tech stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16.2 (App Router), React 18.3 (not React 19) |
| Language | TypeScript 5.9 — but see [routing](#routing--pages-src-app-app-router): most top-level pages are still plain `.jsx` |
| UI primitives | **`@base-ui/react`** 1.6 — *not* Radix UI, despite this being a "shadcn-style" component set (`components.json` style: `base-nova`) |
| Styling | Tailwind CSS **v3.4** (not v4, despite v4-style oklch color tokens) + `class-variance-authority` + `tailwind-merge` |
| Icons | `lucide-react` |
| Data fetching / cache | TanStack Query v5 — the actual state-management layer for nearly everything |
| Charts | Hand-drawn inline `<svg>` — `recharts` is a dependency but is not used anywhere |
| Forms | Hand-rolled `useState` + plain `<input>`/`<textarea>` with manual validation — `react-hook-form`/`yup` are dependencies but unused |

**A number of declared dependencies are unused today**: `recharts`, `zustand`, `sonner`,
`motion`, `react-hook-form`, `yup`, `date-fns`, `classnames`, `cmdk`,
`@tanstack/react-table`, `lodash-es` (the last even has a dedicated `transpilePackages` entry
in `next.config.js` for a package nothing imports). Treat these as either reserved-for-later
or safe to prune — see [Known issues](#known-issues-found-2026-07-15).

## npm scripts

```bash
npm run dev         # next dev
npm run build        # next build
npm run start         # next start
npm run lint            # next lint
npm run typecheck        # tsc --noEmit
```

There is no `test` script. `vitest.config.ts` exists (jsdom env, points at a
`test/setupTests.ts` that doesn't exist) but there isn't a single `*.test.*` file in the
project — test tooling is scaffolded, not adopted.

## Routing / pages (`src/app/`, App Router)

Every route is a client component (`"use client"`) wrapped in `<AppShell>` (persistent
sidebar/topbar). There is no server-side data fetching — TanStack Query on the client does
all of it.

| Route | File | Purpose |
|---|---|---|
| `/` | `page.jsx` | Splash — checks `localStorage` for a token, redirects to `/dashboard` or `/login` |
| `/login` | `login/page.jsx` | Login form (button `onClick`, not a real `<form>`) |
| `/dashboard` | `dashboard/page.jsx` | KPI cards, recent runs, top open defects, project filter |
| `/projects` | `projects/page.jsx` | Project list, search, "Discover Project" (calls `/api/projects/discover-suites`), inline edit |
| `/projects/[id]` | `projects/[id]/page.jsx` | Project detail — environments editor, test-suite table |
| `/defects` | `defects/page.jsx` | Defect list, severity/status/project filters, create/edit modals |
| `/execute` | `execute/page.jsx` | Trigger a run, live progress via SSE, persists in-flight state to `sessionStorage` so a refresh doesn't lose it |
| `/reports` | `reports/page.jsx` | Paginated run report list with filters |
| `/reports/[run_id]` | `reports/[run_id]/page.jsx` | Run detail — results table, video panel, AI-suggestion review/approve, JSON export, log-defect-from-result |
| `/test-results` | `test-results/page.jsx` | Flat test-result list, split detail panel |
| `/ai-testing` | `ai-testing/page.tsx` | The "Vibe Testing" hub — see [AI Testing](#ai-testing-vibe-testing) |
| `/admin/users` | `admin/users/page.jsx` | User management, role + per-feature permission checkboxes — admin/qa_lead only |
| `/admin/audit-logs` | `admin/audit-logs/page.jsx` | Audit trail viewer — admin/qa_lead only, the one legacy page fully built on shadcn `Table`/`Card`/`Badge` |

Every route has a matching `loading.jsx`/`error.jsx` (Next.js conventions), consistently
applied across the whole app including admin routes.

**`.jsx`/`.tsx` split**: every top-level page is `.jsx` **except** `ai-testing/page.tsx`.
Conversely, almost everything under `src/components/ai-testing/*` and the shared shadcn
primitives in `src/components/ui/*` are `.tsx`. In practice: legacy/core CRUD = untyped JS,
newer AI-testing surfaces = typed TS. This is real and ongoing, not incidental — new feature
work keeps extending the `.tsx` side.

## API proxy layer (`src/app/api/`)

Nothing in this app talks to Postgres or holds real business logic — `src/app/api/utils/sql.js`
is a deliberate stub that throws if anything tries to import a SQL client here, with a comment
stating direct DB access is FastAPI's job exclusively. Instead, almost every route under
`src/app/api/` is a thin proxy through the shared `proxyToFastAPI()` helper
(`src/app/api/utils/proxy.js`) to `${FASTAPI_URL}/api/v1/...`. Covers: auth, projects
(+ suites, discovery), defects, test-suites, test-runs, test-results, execute (+ SSE stream),
reports (+ videos, AI suggestions), dashboard stats, admin (users, audit-logs, seed), and the
full Vibe Testing surface (runs, environments, credential profiles, skills). A catch-all route
(`api/v1/[...path]/route.js`) forwards any other `/api/v1/*` path verbatim — used by the
Visual QA components that call `/api/v1/visual-audits/*` and `/api/v1/orchestrator/*` directly
rather than through a dedicated route file.

**Why the proxy layer exists, concretely:**
- **Auth/cookie handling**: `/api/auth/login` and `/api/auth/refresh` strip the refresh token
  out of FastAPI's JSON response and re-set it as an **httpOnly, `SameSite=Strict` cookie**
  (`aep_refresh_token`, scoped to `Path=/api/auth`) so client-side JS can never read it — an
  XSS mitigation for the refresh token specifically (the access token is still handled
  client-side, see [Auth](#auth) below).
- **Body-size correctness**: `middleware.js` explicitly excludes `/api/*` from its matcher
  because Next.js Edge Middleware imposes a 10MB body cap that was silently truncating
  multipart video uploads before FastAPI ever saw them.
- **Response cleanup**: `proxyToFastAPI` strips hop-by-hop/encoding headers
  (`transfer-encoding`, `content-encoding`, …) that would otherwise cause
  `ERR_CONTENT_DECODING_FAILED` in the browser, since Node's `fetch()` already decompresses.

Two routes stream **Server-Sent Events**: `execute/[id]/stream` and
`ai-testing/runs/[run_id]/stream`. Both accept the JWT as a `?token=` query param (because
`EventSource` can't set custom headers) and forward it as `Authorization: Bearer` upstream.

## Auth

**Updated 2026-07-15** — `src/utils/apiClient.js` (the client every page actually uses) now
shares its access-token storage with `src/lib/api.ts`'s in-memory model instead of
localStorage. The two were previously separate, competing implementations with only the
weaker one wired up; they're now unified on the stronger one. What changed and what didn't:

- **Access token: in-memory only**, via `src/lib/api.ts`'s module-level `setAccessToken`/
  `getAccessToken` (imported by `apiClient.js`, so both files share one source of truth). No
  longer written to `localStorage` — it dies with the tab, which is the actual XSS-hardening
  win (a disk-persisted token survives browser restarts and is trivially exfiltrated by any
  later-running script; an in-memory one only exists while the tab is open).
- **Refresh token: httpOnly cookie only**, as before — `/api/auth/login` and
  `/api/auth/refresh` strip it from the JSON response and set it as `aep_refresh_token`
  (`HttpOnly`, `SameSite=Strict`, `Path=/api/auth`). Client JS has never been able to read it;
  that part of the design already had this property before 2026-07-15.
- **Session length is now 24 hours** (`REFRESH_TOKEN_EXPIRE_DAYS=1`, down from 7 days) — the
  access token stays short-lived (`ACCESS_TOKEN_EXPIRE_MINUTES=15`, down from a `1440` value
  that had crept into the real `dashboard/.env.example`, defeating the point of a short-lived
  token) and refreshes silently in the background for as long as the 24h session lasts.
- **`apiFetch()`'s own contract is unchanged** — still native `fetch`, still returns a
  Fetch-API `Response`, still auto-retries once on a `401` via `/api/auth/refresh`. This was
  deliberate: `apiFetch` is called directly (not just via `apiGet`/`apiPost`/etc.) from 15+
  places across the app, including raw-`FormData` upload flows — swapping the underlying HTTP
  client (e.g. to axios) would have meant touching every one of those call sites and changing
  their response-handling shape. Only *where the token lives and how it's obtained* changed.
- **On a hard page reload**, the in-memory token is gone (that's the tradeoff for not
  persisting it). `Providers.jsx` now does a one-time silent-refresh bootstrap on mount
  (redeeming the httpOnly cookie via `/api/auth/refresh` before the rest of the app's queries
  fire) so this doesn't show up as a burst of doomed 401s on every navigation — though even
  without that bootstrap, `apiFetch`'s existing 401-triggered refresh would have handled it
  correctly, just with one extra round trip on the first call.
- **Route protection** (`src/middleware.js`, unchanged): Edge middleware still verifies a
  separate, short-lived, non-httpOnly `aep_token` cookie (a plain copy of the access token,
  same value/lifetime) using Web Crypto — this cookie exists solely so middleware can gate
  `/dashboard`, `/projects`, etc. without a network round trip; it is never read by client JS
  to authorize an API call, only the in-memory token is used for that. Public: `/`, `/login`.
  Role-gated: `/admin/users` (admin only), `/admin/audit-logs` (admin + qa_lead).
- `AppShell.jsx`'s logout now also clears the in-memory token (`clearTokens()` from
  `apiClient.js`) in addition to the cached user profile and the `aep_token` cookie.
- `src/app/api/utils/auth.js` (a full `createAccessToken`/`verifyToken`/`requireAuth`/
  `requirePermission`/`requireRole`/`ROLE_PERMISSIONS` implementation) is **still dead** — no
  API route calls any of it; `middleware.js`'s comment claiming "API routes handle their own
  auth via requireAuth/requireRole" is still inaccurate. All real enforcement happens in
  FastAPI; Next.js just forwards the `Authorization` header through untouched. Left alone in
  this pass — removing or wiring up dead code that isn't causing active harm was out of scope.

**API client** (`src/utils/apiClient.js`, used everywhere): `apiGet`/`apiPost`/`apiPut`/
`apiPatch`/`apiDelete` all call relative paths (`BASE = ""`) — every request goes to this
Next.js app's own origin, which proxies server-side. There is no client-facing
`NEXT_PUBLIC_API_URL`; the server-side backend URL is `process.env.FASTAPI_URL` (default
`http://backend:8000`, the Docker Compose service name). `Content-Type` is omitted
automatically when the body is `FormData` so the browser sets the multipart boundary — used
by every file-upload flow. Errors from FastAPI (`{detail: string}` or a 422 validation array)
are normalized into a single readable `Error` message.

`getStoredUser`/`setStoredUser`/`clearStoredUser` (`src/utils/authStore.js`) are unaffected by
any of the above — they cache the user's *profile* (email/role/permissions for rendering nav
and gating UI), not a credential, and still live in `localStorage` deliberately (so the app
has an instant "who's probably logged in" signal on first paint, before the silent-refresh
bootstrap resolves).

## State management / data fetching

**TanStack Query v5** is the dominant pattern — `useQuery`/`useMutation`/`useQueryClient`
throughout, with defaults set once in `Providers.jsx` (`staleTime: 5min`, `cacheTime: 30min`,
`retry: 1`, `refetchOnWindowFocus: false`). No Context/Redux/Zustand for app state despite
`zustand` being a dependency — the current user lives in `localStorage`, re-read per component
via `getStoredUser()`.

**Auto-refresh / live-update mechanisms:**
- Dashboard stats: `refetchInterval: 30_000` (30s). **This was deliberately lowered from
  10s** — the code comment explains it cuts background DB load (and Neon cold-start wake-ups)
  by two-thirds per open dashboard tab. If you've seen "auto-refresh every 10 seconds"
  documented elsewhere in this repo, that's now stale.
- AI Testing "Results" tab: `refetchInterval: 15_000` (15s) for run history.
- Live in-flight progress (Execute page, AI Testing's live run view) uses **SSE**
  (`EventSource`), not polling.
- `AutonomousQASection.tsx` and `SowCheckpointsSection.tsx` are the exception: they poll via
  plain `setInterval` (2s and 3s respectively) against the orchestrator/SOW endpoints instead
  of TanStack Query's `refetchInterval` or SSE — inconsistent with the rest of the app, worth
  normalizing eventually but not currently broken.

## Components

**`src/components/`**
- `AppShell.jsx` — sidebar/topbar shell for every authenticated page. Nav items filtered by
  `user.permissions` (admin sees everything); separate Admin section gated by role.
- `Providers.jsx` — the single `QueryClientProvider`.
- `AutonomousQASection.tsx` — combined orchestrator-run form: goal + live URL + environment +
  three upload dropzones (Figma PNG / video / SOW) + saved-reference picker + credential
  profile, submitted to `POST /api/v1/orchestrator/runs`. Renders live engine-status cards
  (THE BRAIN / THE HANDS / THE JUDGE / THE LINE / MEMORY BANK) reflecting the orchestrator's
  routing decisions, polls every 2s until terminal. Feature-detects `VISUAL_AUDIT_ENABLED` by
  probing `GET /api/v1/visual-audits/references` on mount and rendering nothing on a 404.
- `SowCheckpointsSection.tsx` — one component, two variants via a `variant` prop: `"sow"`
  (upload a spec doc) and `"video"` (upload a walkthrough, requires a mandatory platform-name
  field). Supports multi-part document analysis, "reused — no AI credits used" messaging for
  previously-parsed content, and an "Use as goal" action feeding a checkpoint into the parent
  goal box. Also feature-detected the same way.
- `FigmaImportSection.tsx`, `VisualAuditSection.tsx` — fully built, real, but **not wired into
  any page as of the in-progress mode-picker change** (see [below](#in-progress-vibe-testing-mode-picker)).

**`src/components/ai-testing/`**
- `ModeSelector.tsx` — new (in progress, uncommitted) — see below.
- `shared.tsx` — shared types (`RunEvent`, `Skill`, `OrchestratorDecision`, `VisualFinding`,
  `RunResult`) and sub-components (`FindingCard`, `ColorSwatch`, `StepIcon`, `ScreenshotPane`,
  `StepRow`, `RunStatusBadge`) reused across the Vibe Testing surface.
- `ResultsTab.tsx` — history of past AI runs, drills into `RunDetail` (goal-based) or
  `OrchestratorRunDetail` (autonomous QA runs).
- `SkillsTab.tsx` — the skills library: project-scoped, sortable, bulk select/assign/delete;
  "recorded" skills replay without an LLM call, "prompt" skills (from SOW/video) run a full
  AI-planned session.
- `SkillDetailModal.tsx`, `RunDetail.tsx`, `OrchestratorRunDetail.tsx` — detail views.

**`src/components/ui/`** — shadcn-CLI-managed primitives (`avatar`, `badge`, `button`, `card`,
`input`, `select`, `separator`, `skeleton`, `table`, `tooltip`), each wrapping a
`@base-ui/react/*` primitive, styled via `class-variance-authority` + `cn()`. `avatar.tsx` and
`tooltip.tsx` exist but aren't used by anything today.

## AI Testing ("Vibe Testing")

The flagship AI feature set, at `/ai-testing`, three tabs: **New**, **Results**, **Skills**.
The **New** tab is mid-refactor (see below) around four modes that all map onto real,
already-functioning backend features:

| Mode | Backing component | Backend feature |
|---|---|---|
| Quick | plain goal box | goal-based `browser-use` agent run |
| Visual | `AutonomousQASection` | orchestrator ("The Brain") — routes to Hands/Judge/self-execute |
| SOW | `SowCheckpointsSection` (`variant="sow"`) | spec-document → checkpoint extraction |
| Video | `SowCheckpointsSection` (`variant="video"`) | walkthrough-video → checkpoint extraction |

Every functional checkpoint extracted from a SOW or video is saved straight to the **Skills**
tab as a runnable prompt skill — no live browser run needed to produce it.

### In-progress: Vibe Testing mode picker (uncommitted at time of writing)

The current working tree (not yet committed) reworks the New-test flow from a long stacked
list of independent cards into a single 4-card mode picker
(`src/components/ai-testing/ModeSelector.tsx`, new/untracked). Per its own doc comment, this
is a pure UI reorganization — Quick/Visual/SOW/Video map 1:1 onto the already-working features
above; switching between modes only toggles visibility (`hidden` class) rather than
mounting/unmounting, so an in-flight upload or poll in a background mode keeps running.
`VisualAuditSection` and `FigmaImportSection` were explicitly removed from this page "per
product decision" — their component files remain in the tree but are no longer reachable from
any page. `ai-testing/page.tsx` also gained a "Web app testing" / "Android app testing"
top-level toggle, where **Android is a stubbed "Coming soon" placeholder with no backend
behind it**. Six other pages in this same working tree
(`dashboard`, `defects`, `execute`, `reports`, `test-results`, `admin/users`) have an
unrelated, mechanical change in progress alongside this: migrating plain HTML `<select>`
elements to the shadcn `Select` composition.

## Styling

Tailwind v3 with oklch design tokens (`--background`, `--foreground`, `--primary`, `--muted`,
`--destructive`, `--border`, `--ring`, `--chart-1..5`, `--sidebar-*`) defined in
`src/app/global.css` — the stylesheet actually loaded by `layout.jsx`. A second stylesheet,
`src/index.css`, duplicates similar tokens and is what `components.json` points the shadcn CLI
at, but it's **never imported by the app** — running `npx shadcn add ...` would write into a
file nothing loads (see [Known issues](#known-issues-found-2026-07-15)).

A full `.dark { ... }` token block and `dark:` Tailwind variants exist on several `ui/*`
components, but `tailwind.config.cjs` sets no `darkMode` strategy and nothing in the app ever
adds a `.dark` class — dark mode is scaffolded at the token level but not reachable by an end
user today.

Two styling conventions coexist: legacy pages (`dashboard`, `projects`, `defects`, `execute`,
`reports`, `admin/users`) use inline `style={{}}` objects with hardcoded hex colors; the newer
AI-testing surfaces and `admin/audit-logs` use Tailwind + the design tokens above.

Font is Inter, loaded via a raw Google Fonts `@import` inside a `<style>` tag in `layout.jsx`
(not `next/font/google`).

## Environment variables

| Variable | Where used | Purpose |
|---|---|---|
| `FASTAPI_URL` | all server-side proxy routes | backend base URL, default `http://backend:8000` |
| `SECRET_KEY` (or `AUTH_SECRET`) | `middleware.js`, `api/utils/auth.js` | HMAC secret for the app's own JWT verification — must match FastAPI's signing secret |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | `api/utils/auth.js`, login/refresh routes | token lifetimes, also sets the httpOnly refresh cookie's `Max-Age`. Must match the backend's values. As of 2026-07-15: `15` / `1` (24-hour sessions) — see [Auth](#auth) |
| `NODE_ENV` | login/refresh routes, logger | gates adding `Secure` to the refresh cookie in production |
| `LOG_LEVEL` | `api/utils/logger.js` | default `info` |

`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_BASE_URL` are both defined in places but neither is
read by any live code path — see [Known issues](#known-issues-found-2026-07-15).

## Known issues (found 2026-07-15)

Found while writing this documentation pass — flagging for a human decision, nothing here has
been changed:

1. **`middleware.js`'s auth model has drifted from its own comment.** It states "API routes
   handle their own auth via requireAuth/requireRole" — but `src/app/api/utils/auth.js`'s
   `requireAuth`/`requirePermission`/`requireRole` functions are fully implemented and **never
   called by any route**. Every API route just forwards the `Authorization` header to FastAPI
   and lets it enforce everything. Not exploitable on its own (FastAPI does enforce), but the
   comment is actively misleading and the dead code should either be wired up or removed.
2. ~~`/api/auth/login` had no rate limiting~~ — **fixed 2026-07-15**: `/api/auth/login` now
   calls the previously-unused `checkRateLimit`/`getClientIp`/`resetRateLimit`
   (`src/app/api/utils/rateLimit.js`) before ever proxying to FastAPI — 5 attempts per 15
   minutes per IP, reset on a successful login, `429` + `Retry-After` header when exceeded.
   Verified live (6 rapid attempts against a running dev server: the first several proxied
   through, the rest got `429`). `/api/auth/refresh` was deliberately left unlimited — it
   requires already possessing a valid httpOnly cookie, which is a much harder thing to brute
   force than a password.
3. ~~Two competing API-client implementations~~ — **resolved 2026-07-15**: `apiClient.js` now
   imports its token storage from `src/lib/api.ts` (`setAccessToken`/`getAccessToken`) instead
   of maintaining a separate localStorage-based implementation — see [Auth](#auth) above for
   the full story. `apiClient.js`'s own HTTP mechanics (native `fetch`, not axios) were kept
   as-is deliberately, since rewriting 15+ direct `apiFetch()` call sites (including raw
   `FormData` uploads) onto axios's different response shape was a much larger, riskier change
   than the actual security goal required. `src/lib/api.ts`'s axios instance itself is still
   not imported by any page directly — only its token-storage functions are reused now — so
   it's more "the useful half was extracted" than "fully adopted," worth knowing if you go
   looking for where axios is actually used (nowhere, still).
4. **`seed.mjs`** at the frontend root (gitignored, not committed, confirmed via `git
   ls-files`/`git log`) contains a commented-out but still-plaintext Neon connection string
   plus a hardcoded admin email/password, used for manual local seeding. Not currently exposed
   since it's untracked, but recommend deleting it or moving any real values out of source
   entirely, and rotating them if they were ever active — see the matching, more severe finding
   in [dashboard/backend/README.md](../backend/README.md#known-issues-found-2026-07-15) about
   `dashboard/.env.example`, which *is* committed.
5. **Dead/orphaned files worth pruning**: `src/utils/proxy.js` (a second, unused
   `proxyToFastAPI` reading an env var — `NEXT_PUBLIC_API_URL` — that's defined nowhere),
   `src/app/api/utils/upload.js` (posts to a hardcoded placeholder domain
   `api.anything.com`) and `src/app/api/utils/create.js` (generic `/api/db/*` helper) — both
   look like unmodified starter-template boilerplate, not AEP code. `src/app/api/utils/audit.js`
   (`writeAuditLog`) is also never called — the real audit log is written by FastAPI.
   `VisualAuditSection.tsx`/`FigmaImportSection.tsx` are real but currently unreachable from any
   page (see [above](#in-progress-vibe-testing-mode-picker)).
6. **`components.json` points the shadcn CLI at `src/index.css`**, but the app actually loads
   `src/app/global.css`. Running the shadcn CLI to add a new component today would silently
   write theme tokens into a file with no effect.
7. **Dark mode is unreachable.** Tokens and `dark:` variants exist on several components, but
   there's no `darkMode` strategy in `tailwind.config.cjs` and no toggle/mechanism anywhere
   that ever applies a `.dark` class.
8. **A long list of declared-but-unused npm dependencies** (`recharts`, `zustand`, `sonner`,
   `motion`, `react-hook-form`, `yup`, `date-fns`, `classnames`, `cmdk`,
   `@tanstack/react-table`, `lodash-es`) — worth pruning `package.json`, or explicitly
   documenting which ones are intentionally reserved for near-term work (e.g. if `sonner` is
   meant to replace the current inline-red-banner error pattern).
9. **`tailwind.config.cjs` is ~1600 lines**, almost entirely a `fontFamily` map of hundreds of
   Google Font names that appear to be unmodified scaffold/generator boilerplate — only Inter
   is actually used anywhere in the app. Safe to trim significantly.
10. **Test tooling is fully scaffolded but 0% adopted** — `vitest.config.ts` references a
    `test/setupTests.ts` that doesn't exist, there's no `test` npm script, and there isn't a
    single test file in the frontend.
