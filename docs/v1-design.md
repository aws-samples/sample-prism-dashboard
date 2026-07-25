# Prism Dashboard v1 — AI Usage, DORA & Delivery Metrics Design

**Status:** Draft
**Date:** 2026-07-19
**Scope:** Extend dev-ai-tools-analytics — renamed **Prism Dashboard** — with codeburn as the primary source for AI token/cost/model/tool usage, plus DORA and delivery-quality metrics that can be correlated against AI adoption. The name signals brand affinity with the PRISM ecosystem; the project remains independent of the PRISM CLI (see §7.1).

---

## 1. Goals

1. **codeburn OTLP as the universal baseline for AI usage** — tokens, cost, model, and tool per user for 35+ tools, ingested via an OTLP receiver; authoritative provider APIs (Anthropic Admin API, Kiro service CSV) override it per tool via blending (§4.5). Git-hook trailer token accounting is explicitly rejected (see §2).
2. **DORA metrics** — deployment frequency and lead time for changes (change failure rate and MTTR deferred).
3. **Delivery metrics** — PR/MR cycle time, time-to-first-review, bug injection ratio, sprint burndown and velocity.
4. **Correlation surfaces** — AI usage overlays on every delivery chart (DORA, commits, PR/MRs, Jira stories/epics, Asana tasks, sprints), plus AI efficiency ratios.
5. **Rename to Prism Dashboard** (dashboard title, README, stack/branding) + documentation: codeburn setup guide, DORA configuration, and a clear statement of the relationship to PRISM (§7.1).

### Non-goals (v1)

- Change failure rate / MTTR (requires incident-labeling conventions; phase 2 of DORA).
- Commit-level AI attribution drill-down table (per-commit SHA/tool/model rows).
- Git-hook trailer ingestion of any kind (tokens *or* AI-origin counts) — **dropped permanently**, see §2.1. Commit-level AI attribution is instead planned as a server-side temporal-correlation metric (v1.x, §11).
- BYO IdP for the OTLP receiver (Cognito only in v1).
- Percentile (P50/P90) PR cycle-time stats — v1 ships averages; raw PR-event storage is a follow-up.
- Developer satisfaction / survey metrics.

---

## 2. Why codeburn, not git-hook trailers, for tokens/cost

PRISM's `prepare-commit-msg` hook attributed tokens via cumulative-snapshot deltas at commit time. Known systematic failure modes (all observed in production use):

| Failure mode | Effect |
|---|---|
| Snapshot burn — deltas on amended/rebased/never-PR'd commits advance the baseline but are never emitted | Undercount |
| kiro-cli/kiro-ide fallback race — mixes per-session CLI baseline with IDE lifetime cumulative | Overcount |
| Wrong-bucket attribution — all tokens since last commit attributed to next commit, including unrelated chat/exploration; Kiro parent-directory workspaces return 0; Cursor orphan bucket swallows unmapped composers | Misattribution |
| Heuristic env-var tool detection — Cursor swallows other agent CLIs in its terminal; askpass detection over-attributes | Wrong tool |
| Observed drift — negative-cost-delta clamps and reinstall reset-detection were required | Untrustworthy |

codeburn reads provider-reported usage directly from session files, session-scoped, with no snapshot arithmetic and no commit-timing dependency. It ships 35+ provider parsers (claude, codex, cursor, kiro, gemini, copilot, goose, cline, …) and exports OTLP to a backend discovered via `/.well-known/codeburn-export.json`.

**Cost source-of-truth hierarchy** (highest wins where both exist):

1. Anthropic Admin API (provider-reported) — existing `claude_code_analytics` plugin
2. codeburn OTLP (estimated from tokens × pricing)

(The Bedrock CUR 2.0 billed-cost pipeline was removed from the platform — codeburn and the
Anthropic Admin API are the only AI cost sources.)

The dashboard labels codeburn cost as "estimated". This hierarchy is enforced mechanically
by the blending rule in §4.5.

### 2.1 Trailers dropped permanently (including AI-Origin)

Beyond the token-accounting flaws above, the trailer mechanism as a whole is retired — including
per-commit `AI-Origin`/`AI-Tool` attribution:

- **AI-Origin never measured authorship, only IDE presence.** Detection was env-var heuristics:
  any commit from inside Cursor → `ai-generated` regardless of who wrote the code; Kiro askpass
  detection fired on any Source Control panel commit. "AI-generated %" was really "% of commits
  made from an AI-capable environment" — a tool-presence proxy that codeburn sessions provide
  directly and more accurately.
- **Absence is ambiguous.** No trailer means *either* human-authored *or* hook-not-installed.
  Hook coverage (per-repo, per-machine install; `--global` only affects future clones) silently
  corrupts the denominator.
- **No integrity.** Trailers are plain commit-message text — amendable, spoofable, and mangled
  by squash merges and rebases.
- **Precedence swallowing.** Cursor always won the AI-Tool slot, absorbing other agent CLIs
  running in its terminal.
- **Operational drag.** bash-only (Windows jq/bc gap), per-repo version skew, `AI-Model` blank
  for Cursor.

The PRISM hook may live on as an in-repo provenance breadcrumb for humans reading `git log`,
but the dashboard never consumes trailers. Commit-level AI attribution is replaced by the
server-side temporal-correlation metric (§11).

---

## 3. Architecture (delta)

```
                       ┌─ existing ─────────────────────────────────────────┐
Kiro CSV → S3 → Lambda │ ETL Orchestrator → SQS → Workers → Aggregations    │
                       │ API GW (Cognito JWT) → API Handler → DynamoDB      │
                       └────────────────────────────────────────────────────┘
NEW:
codeburn (dev machine)
  ── OAuth PKCE (Cognito, 127.0.0.1 callback) → JWT
  ── GET  /.well-known/codeburn-export.json   (discovery, unauthenticated)
  ── POST /v1/traces  (OTLP/HTTP, JWT)  → OTLP Receiver Lambda
                                            ├─ S3 OTLP archive (dt=YYYY-MM-DD)
                                            └─ AiUsageTable (dedup spans)
ETL Worker: `codeburn` built-in plugin reads AiUsageTable → daily aggregates

GitHub/GitLab plugins: + Deployments API → DeploymentEventsTable + daily counts
                       + PR timestamps → cycle-time sum/count keys
Jira plugin:           + bugs-opened JQL, + sprint burndown snapshots
```

---

## 4. Phase 1 — codeburn ingestion

### 4.1 OTLP receiver

New construct **`AiUsageIngestion`** (`cdk/lib/constructs/ai-usage-ingestion.ts`):

- **Routes** (added to the existing API Gateway):
  - `GET /.well-known/codeburn-export.json` — unauthenticated discovery document (schema
    verified against codeburn `src/sync/discovery.ts`):
    ```json
    {
      "version": 1,
      "issuer": "https://cognito-idp.<region>.amazonaws.com/<user-pool-id>",
      "client_id": "<pkce-client-id>",
      "scopes": ["openid", "email"],
      "traces_path": "/v1/traces",
      "max_batch_size": 500
    }
    ```
    `issuer` must serve OIDC discovery (`/.well-known/openid-configuration`) — Cognito user
    pool issuer URLs do. Static MOCK integration.
  - `POST /v1/traces` — **OTLP/HTTP JSON only** (codeburn sends `Content-Type:
    application/json`; protobuf support unnecessary). Authorized by a Cognito authorizer.
    Handled by a new **OTLP Receiver Lambda** (Python 3.12), *not* the API handler.
- **Auth**: new Cognito **public app client** (no secret), authorization-code + PKCE (S256),
  callbacks `http://127.0.0.1:19876/callback`, `:19877`, `:19878` (codeburn `CALLBACK_PORTS`,
  tried in order). The JWT identifies the user; **no git-email mapping needed**.
- **Receiver flow** (order matters): validate JWT → parse OTLP → **archive raw batch to S3**
  (`ai-usage-otlp-{account}-{region}`, prefix `otlp/dt=YYYY-MM-DD/`, KMS CMK, access-logs
  bucket per project convention) → conditional per-span writes to DynamoDB.
- **Response contract** (verified against codeburn `src/sync/push.ts`):
  - Full success → `200` with `{}` (empty body also treated as full success). codeburn then
    ledgers the batch client-side and never re-sends those spans.
  - Partial failure → `200` with `{"partialSuccess": {"rejectedSpans": N}}`. codeburn does
    **not** ledger the batch and re-sends the *whole batch* next push — deterministic span
    IDs + our conditional writes make this safe. Prefer this over 5xx for per-span issues.
  - `429` with `Retry-After` → codeburn waits (cap 120 s) and retries same batch, 3 attempts.
    API Gateway throttling produces 429s natively — backpressure is handled for free.
  - `401` → codeburn stops and refreshes auth; `5xx` → stops, retries next push.
- **Throttling**: method-level throttle lower than the global 50 r/s (e.g. 10 r/s / 20 burst).

### 4.2 AiUsageTable

| | |
|---|---|
| PK | `userId` (stable internal UUID, resolved from the JWT email via UserTable) |
| SK | `SPAN#<startTimeUnixNano>#<spanId>` |
| Dedup | `ConditionExpression: attribute_not_exists(sk)` — codeburn span IDs are **deterministic** (`sha256(deduplicationKey)[:16]`), so re-sent batches dedup exactly |
| TTL | 90 days (`ttl` attribute) |
| Encryption | KMS CMK, PITR, PAY_PER_REQUEST (project convention) |

**Span attribute mapping** (verified against codeburn `src/sync/otlp.ts`):

| OTLP attribute | Type | → Table attribute |
|---|---|---|
| `ai.provider` | string | `tool` (verbatim; alias-normalized `cursor-agent`→`cursor`) |
| `ai.model` | string | `model` |
| `ai.input_tokens` | intValue (string-encoded int64) | `inputTokens` |
| `ai.output_tokens` | intValue (string-encoded int64) | `outputTokens` |
| `ai.cost_usd` | double | `costUsd` — **codeburn computes cost client-side; no server pricing table needed** |
| `ai.cost_estimated` | bool | `costEstimated` (true for kiro credit-derived / zero-token estimates) |
| `ai.project` | string | `project` |
| `ai.speed` | string | `speed` |
| `ai.tools` | string array | `toolsUsed` (agent tool names, optional) |
| span `traceId` | `sha256(sessionId)[:32]` | `sessionTraceId` — session identity for `aiSessions` counting |
| resource `codeburn.device_id` | string | `deviceId` (pseudonymous host hash) |

Note: OTLP `intValue` arrives as a JSON **string** (proto3 int64 mapping) — receiver must
`int()` it.

**Provider handling**: codeburn ships 35+ providers (claude, codex, cursor, cursor-agent,
kiro, gemini, copilot, goose, cline, …). No curated mapping — `ai.provider` is stored
verbatim in this table, and daily aggregation uses **dynamic per-provider keys** derived at
runtime (see §4.3). Alias normalization only where two providers are one product
(`cursor-agent`→`cursor`).

Model granularity lives **only** here (query-time), not in daily aggregates.

### 4.3 `codeburn` ETL plugin

Built-in plugin (Kiro pattern — no credentials, `datasources/codeburn.py`):

- `fetch_activity(date_str)`: query `AiUsageTable` for the user's spans on that date (spans bucketed by **span timestamp**, not arrival date).
- `get_aggregation_fields()` → **fixed totals + dynamic per-provider keys**:

```
Fixed:    aiTokensIn, aiTokensOut, aiCost, aiSessions
Dynamic:  aiTokensIn_<provider>, aiTokensOut_<provider>, aiCost_<provider>
          (provider derived from ai.provider at runtime, e.g. aiCost_goose)
```

  DynamoDB is sparse — absent attribute = 0, so only providers with actual usage produce
  attributes. No per-provider code changes when codeburn adds providers.
- **Dynamic-key guards** (`ai.provider` is client-controlled and becomes an attribute name):
  1. Sanitize: lowercase, `[a-z0-9-]` only, max 32 chars; anything else → `other`
  2. Cardinality cap: max 20 distinct providers per user-day; overflow → `other`
     (protects the 400 KB item limit and chart legibility)
  3. Raw table keeps the original provider string verbatim for forensics
- **Registry changes**: `_AGG_METRIC_KEYS`, `_GLOBAL_KEYS` (API handler); org-only keys register in `lambda/shared/metric_keys.py`
  (API handler) accept a **prefix rule** — any key matching `ai*` passes — instead of
  enumerating per-provider keys. Register the plugin in `registry.py`.
- Late-arrival handling: exports can lag (laptop offline). The plugin re-reads whatever is in `AiUsageTable` per date; the orchestrator's existing "today always reprocessed" behavior plus a **3-day re-process window** (same pattern as the CE-proxy 3-day refresh) covers stragglers.

### 4.4 Frontend — unified AI Dashboard

One **AI Dashboard page** (`AiDashboardPage.tsx`) **replaces** the separate Kiro and Claude
API pages — all AI providers in a single view, powered by the blended metrics (§4.5):

- **Summary cards**: blended AI cost, tokens, sessions, AI-active users
  (users with ≥1 blended AI activity key > 0 that day)
- **Stacked-by-tool cost/tokens charts** — providers **discovered by scanning returned
  record keys for the `aiCost_`/`aiTokens*_` prefixes**; legends self-populate as new
  tools appear
- **Model breakdown table** (per tool/model from a new `GET /ai-usage/{userId}` endpoint
  reading `AiUsageTable`). **No per-provider tabs or cards** — Kiro and Claude have no
  dedicated UI; they surface only through the generic provider displays (stacked charts,
  breakdown table, badges). Provider-unique metrics (Kiro messages/conversations, Claude
  LOC/tool-acceptance) remain in the aggregates but are not charted in v1
- **Source badges**: each tool labeled *provider-reported*, *derived* (e.g. Kiro credits × rate), or *estimated* per §4.5
- **Tokens are optional per tool**: cost is the universal cross-tool metric (every tool gets a blended dollar value); token charts render only the tools that report tokens and omit the rest — no placeholder zeros
- Data-scoped (self or same team), like other activity endpoints
- **Nav**: "AI Dashboard" replaces the Kiro and Claude API entries under User Activity
- `overlayConfig.ts`: `DS_CATEGORIES.codeburn = 'CodeTool'`; overlays `{ key: 'aiCost', label: 'AI Cost ($)', field: 'aiCost', isCurrency: true }` and `{ key: 'aiTokensOut', label: 'AI Output Tokens', field: 'aiTokensOut' }` — automatically surfaces on all IDP/WorkManagement pages. Blended `aiCost`/`aiTokensOut` are the only CodeTool overlays — no provider-specific overlay entries (the former Kiro Credits overlay is removed; credits feed `aiCost_kiro` via the $/credit conversion).
- **Setup UX**: a "Connect codeburn" card (AI Dashboard) showing the discovery URL and the one-liner to configure codeburn export.

### 4.5 Source precedence and blending

codeburn is the **universal baseline** (35+ tools, estimated); authoritative provider APIs
**override** it per tool. Applied at aggregation time in the ETL worker, per **(user, day,
tool)** — never globally per tool, since authoritative coverage can be partial (API lag,
missing seats).

| Tool | Authoritative source | codeburn role |
|---|---|---|
| `claude` (codeburn's provider name, stored verbatim per §4.2) | Anthropic Admin API plugin (`ccAnalyticsCost`, `ccAnalyticsInputTokens/OutputTokens`) → feeds `aiCost_claude`, `aiTokens*_claude` | Fallback when the plugin is inactive or returned no data for that user-day |
| `kiro` | Kiro service CSV — authoritative for **credits/messages/conversations**, and for **cost via conversion**: `aiCost_kiro = creditsUsed × KIRO_CREDIT_USD` (configurable, default **$0.04/credit**), badged *derived* | Fallback for cost when no CSV data; supplies token detail where its Kiro session parsing yields it (tokens are optional — see §4.4) |
| everything else (cursor, codex, gemini, copilot, …) | — | Sole source |

**Blending rule** (worker, when building daily aggregates): for each tool, the blended
per-provider keys (`aiCost_<tool>`, `aiTokens*_<tool>`) and the fixed totals (`aiCost`,
`aiTokensIn/Out`, `aiSessions`) take the authoritative value when present for that
user-day, else the codeburn span-derived value. Raw source keys (`ccAnalytics*`,
`creditsUsed`, raw spans in `AiUsageTable`) are all preserved unchanged — blending only
determines what feeds the canonical `ai*` keys. A per-tool source marker
(`aiSource_<tool>` = `provider` | `derived` | `estimated`) records which side won, driving the UI badges.

**Never sum overlapping sources** — blending replaces, it does not add. This subsumes the
§2 cost hierarchy and eliminates double-counting by construction. Future authoritative
plugins (GitHub Copilot Metrics API, Cursor Admin API, OpenAI Usage API — v1.x candidates)
just register their tool in the precedence map.

---

## 5. Phase 2 — DORA + delivery metrics

### 5.1 Deployments (deployment frequency + lead time)

- **GitHub plugin**: Deployments API (`/repos/{repo}/deployments` + statuses). **GitLab plugin**: Deployments API filtered by environment.
- New per-datasource config field `prodEnvironments` (comma-separated env names, default `production,prod`) — teams define what counts as prod.
- **`DeploymentEventsTable`**: PK=`service` (repo/project path), SK=`deployedAt#deployId`; attrs: `status`, `environment`, `commitSha`, `commitTimestamp`, `leadTimeSeconds` (deploy − commit timestamp). Lead time uses the **deploy's head commit** — a standard simplification that yields the lower bound per deploy; per-commit lead time is a possible refinement once raw PR events exist. KMS CMK, PITR.
- Daily keys: `deployments`, `failedDeployments`, `leadTimeSumSeconds`, `leadTimeCount` (global + team aggregations; deployments are service-level, so they roll into **global/team** aggregates only — not per-user).
- **DORA page**: four metric cards — Deployment Frequency and Lead Time live with Elite/High/Medium/Low tier badges; CFR and MTTR rendered greyed "requires incident configuration". Trend charts with CodeTool overlay toggles. P50/P90 lead time computed at query time from `DeploymentEventsTable`.

### 5.2 PR/MR cycle time + time-to-first-review

- GitHub plugin: PRs merged on date → `merged_at − created_at`; first review via Reviews API. GitLab: MR `merged_at − created_at`; first note/approval timestamp.
- Daily keys (sum/count pattern → averages in v1): `prCycleTimeSum`, `prCycleTimeCount`, `prFirstReviewTimeSum`, `prFirstReviewTimeCount` (and `mr*` GitLab equivalents).
- Charts on GitHub/GitLab pages: average cycle time and time-to-first-review lines. **Percentiles deferred** — noted as a limitation on the chart.

### 5.3 Bug injection ratio

- Jira plugin: new JQL `issuetype = Bug AND created >= <date> AND created < <date+1>` → `jiraBugsOpened` (alongside existing `jiraBugsResolved`).
- Frontend computes ratio `jiraBugsOpened / (jiraStoriesResolved + jiraTasksResolved)`; chart on Jira page + Global Metrics card. This is the quality counterweight to AI-accelerated throughput.

---

## 6. Phase 3 — correlation + efficiency

### 6.1 Overlays

`CODE_TOOL_OVERLAYS` additions (§4.4) surface automatically on GitHub, GitLab, Jira, Asana pages. DORA and sprint charts adopt the same `useCodeToolOverlays` hook.

### 6.2 Sprint burndown + velocity

- `SprintAggregationsTable` today stores per-user sprint totals. Add sprint-level daily snapshots: PK=`sprintId`, SK=`SNAPSHOT#<date>` with `remainingPoints`, `totalPoints`, `completedPoints` — written once per worker run per active sprint (conditional write so only the first worker of the day writes it).
- Frontend: burndown chart (ideal line vs actual) and velocity trend (completed points per closed sprint) on the Jira page. Carryover and scope creep (points added mid-sprint) derived from snapshot series.

### 6.3 AI efficiency ratios

Computed **frontend-side** from existing aggregates; cards + trend lines on Global Metrics and the AI Dashboard.

**Cost basis**: the blended `aiCost` (§4.5) — the precedence rule already picks
provider-reported values over estimates per tool and never sums overlapping sources, so no
basis toggle is needed. Ratios read the same canonical keys as every other chart.

| Ratio | Formula |
|---|---|
| AI cost per merged PR/MR | `aiCost / (githubPullRequests + gitlabMergeRequestsMerged)` — cost basis (universal across tools; replaced the earlier tokens-per-PR ratio, which only covered token-reporting tools) |
| AI cost per story point | `aiCost / (jiraStoryPoints + asanaStoryPoints)` |
| AI cost per commit | `aiCost / (githubCommits + gitlabCommits)` |

Guards: default grouping **weekly**; suppress the ratio (show "—") when the denominator for the bucket is below a floor (e.g. < 3) to avoid low-denominator spikes. Framed as trend correlation, not causation.

---

## 7. Phase 4 — rename + docs

- Naming: renamed to **Prism Dashboard** — dashboard title, README, repo description, **and**
  CDK stack/construct IDs (`PrismDashboardStack`, stack ID `PrismDashboard-<stage>`, REST API
  name `PrismDashboard-UserApi-<stage>`, Cognito domain prefix `prism-dashboard-<stage>`).
  **Deployment note**: the stack-ID rename means the next `cdk deploy` creates a brand-new
  stack (new tables, user pool, buckets, CloudFront domain); existing deployments keep running
  under the old stack until manually deleted — data and Cognito users do not carry over.
  Acceptable pre-v1; done ahead of Phase 1 so v1 resources are created under the new name.
  The name signals brand affinity with the PRISM ecosystem — it does **not** create a
  dependency on the PRISM CLI (§7.1).
- Update `.kiro/steering/internaldoc.md` as each phase lands (per project convention).
- README: document codeburn export setup (discovery URL, PKCE login flow) and the DORA
  `prodEnvironments` configuration.

### 7.1 Relationship to PRISM

Prism Dashboard and the PRISM CLI (`sample-prism-d1-velocity`) are **independent projects**
sharing a brand family:

- **No dependency in either direction.** This platform does not require the PRISM CLI, and
  PRISM does not require this backend. The shared client-side dependency is **codeburn**,
  which belongs to neither project.
- **This platform never consumes PRISM trailers** (§2.1). The PRISM git hook may continue to
  exist as an in-repo provenance breadcrumb for humans reading `git log`.
- **Functional overlap**: this platform supersedes the *capabilities* of PRISM's
  `metrics-pipeline-stack` / `prism-d1-ai-usage` AI-usage backend (with higher-fidelity data
  via codeburn OTLP). Whether the PRISM repo removes its metrics backend or recommends this
  project instead is a decision for that repo's maintainers, out of scope here.
- **Deployment migration**: teams running PRISM's metrics stack who want to move to this
  platform follow a separate migration runbook (parallel-run, cut over codeburn export
  endpoint, tear down the old stack). That runbook is deployment-specific and lives outside
  this design doc.

---

## 8. Security considerations

- OTLP receiver is JWT-authenticated (Cognito); discovery document is public but contains no secrets (issuer/client-id/endpoint only — same as any OIDC discovery).
- PKCE public client has no secret by design. **No custom resource-server scope in v1**: requiring OAuth scopes on the API GW method forces access tokens, and Cognito access tokens carry no `email` claim (breaking `userId` extraction). V1 uses the ID-token Cognito authorizer (same as the web app); the residual risk — a web-app token could call `/v1/traces` — is accepted because span writes are keyed to the caller's own identity (no cross-user impact).
- Receiver validates payload size (API GW 10 MB cap; reject > 1 MB batches), span count per batch, and sanitizes string attributes before storage (existing `validation.py` patterns).
- New S3 bucket + table follow project conventions: KMS CMK, BlockPublicAccess, enforceSSL, access-logs bucket, PITR.
- `AiUsageTable` writes are keyed by the caller's own stable `userId` (resolved server-side from the JWT email; unregistered users are rejected) — a user can only write their own spans. No cross-user injection.

---

## 9. Open questions

1. ~~Exact codeburn OTLP span attribute names~~ — **RESOLVED** (verified against codeburn
   source, `src/sync/otlp.ts`): `ai.provider`, `ai.model`, `ai.input_tokens`,
   `ai.output_tokens`, `ai.cost_usd`, `ai.cost_estimated`, `ai.project`, `ai.speed`,
   `ai.tools`; resource attr `codeburn.device_id`. See §4.2.
2. ~~Whether codeburn emits estimated cost~~ — **RESOLVED**: codeburn emits `ai.cost_usd`
   (client-side pricing) with an `ai.cost_estimated` flag. No server-side pricing table.
3. GitLab first-review signal: first non-author note vs first approval — pick during implementation.
4. Service→team mapping for DORA team scoping (repo naming convention vs explicit config).
5. ~~Cognito user-identity claim~~ — **RESOLVED empirically (live test with codeburn
   0.9.16)**: codeburn sends the **access token** as the Bearer. Fix applied: the
   `/v1/traces` method sets `authorizationScopes: ['openid']` so the Cognito authorizer
   validates access tokens, and the receiver resolves identity from the `username` claim
   (this pool creates users with username = email) with `email` as first preference for
   ID-token callers. Note: SAML-federated usernames are provider-prefixed (not emails) —
   federated users exporting via codeburn would need a `sub`→UserTable mapping (v1.x).

---

## 10. Delivery order

| Phase | Contents | Depends on |
|---|---|---|
| 1 | OTLP receiver, AiUsageTable, codeburn plugin, blending (§4.5), AI Dashboard page, overlays | — |
| 2 | Deployments + DORA page, PR cycle time, bug ratio | — (parallel with 1) |
| 3 | Sprint snapshots/burndown, DORA/sprint overlays, efficiency ratios | 1 (ratios need AI keys) |
| 4 | Docs (codeburn setup, DORA config), steering updates. *Rename to Prism Dashboard: **done** (2026-07-19, ahead of Phase 1)* | 1–3 |

---

## 11. v1.x — AI-active commits (temporal correlation)

Server-side replacement for trailer-based AI-origin attribution. Both input streams exist by
the end of Phase 1:

- **Commits**: git plugins (author, repo, timestamp)
- **AI sessions**: `AiUsageTable` (user, project, span timestamps)

**Classification**: a commit is *AI-active* when the same user has codeburn span activity in
the same project within a window ending at the commit timestamp (proposed: 60 minutes,
configurable). Computed in the ETL worker during commit ingestion — one query against
`AiUsageTable` per user-date, matched in memory.

**Metric keys**: `aiActiveCommits`, `nonAiActiveCommits` → "AI-active commit %" charts and
overlays, replacing what trailer `AI-Origin` claimed to show.

**Properties vs trailers**:

- Uniform methodology — no install-coverage corruption of the denominator (codeburn is the
  only client dependency, already required for v1)
- Not spoofable via commit messages; survives squash/rebase (recomputed from platform data)
- Honest framing: measures *correlation* between AI session activity and commits, which is
  all the trailer heuristics ever measured — without pretending to be ground truth

**Open items**: window size tuning; project↔repo name matching (codeburn project slug vs git
repo path — reuse the basename-matching lessons from PRISM); whether to backfill when the
window config changes.
