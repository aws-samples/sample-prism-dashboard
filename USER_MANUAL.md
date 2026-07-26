# Prism Dashboard — User Manual

This manual covers day-to-day use of the Prism Dashboard after it has been deployed. For deployment, infrastructure, and configuration of the AWS stack itself, see the [README](README.md).

**Audience:**

- **Administrators** — set up users, teams, and data sources; manage the platform
- **Team owners** — monitor their team's delivery and AI-usage metrics
- **Members / developers** — view their own metrics and connect AI-usage telemetry (codeburn)

---

## Table of Contents

1. [Signing In](#1-signing-in)
2. [Roles and Permissions](#2-roles-and-permissions)
3. [First-Run Setup (Administrators)](#3-first-run-setup-administrators)
4. [Connecting Data Sources](#4-connecting-data-sources)
5. [Sending AI Usage Data (Developers)](#5-sending-ai-usage-data-developers)
6. [Navigating the Dashboard](#6-navigating-the-dashboard)
7. [Understanding DORA Metrics](#7-understanding-dora-metrics)
8. [Understanding AI Usage Data](#8-understanding-ai-usage-data)
9. [Teams](#9-teams)
10. [Data Freshness and Refresh](#10-data-freshness-and-refresh)
11. [Troubleshooting](#11-troubleshooting)
- [Appendix A: Obtaining Credentials per Data Source](#appendix-a-obtaining-credentials-per-data-source)

---

## 1. Signing In

Open the CloudFront URL for your deployment (your administrator will provide it; it is emitted as a stack output at deploy time).

- **SAML SSO (recommended):** if your administrator configured IAM Identity Center, choose the SSO provider on the Cognito sign-in page and authenticate with your corporate identity.
- **Cognito username/password:** used when SSO is not configured, or for admin-created accounts.

**First login on a fresh deployment:** the user whose email matches the configured `initialAdminEmail` is automatically bootstrapped as the first admin. Until that user signs in, the API rejects requests from everyone else. Any other user signing in before being registered will see **Access Denied** — see [Troubleshooting](#11-troubleshooting).

**Self-registration:** if the deployment has `allowSelfRegistration` enabled (non-production environments only), any authenticated Cognito user is auto-registered as a regular member on first login. Otherwise an admin must register you (via Identity Center sync or manual creation) before you can use the app.

## 2. Roles and Permissions

Access is enforced server-side on every API call — the UI simply reflects what you are allowed to see.

| Role | Can see | Can manage |
|------|---------|------------|
| **Member** | Their own per-user metrics, plus aggregate (team-level) numbers for their team | Their own limited profile fields |
| **Team owner** | Everything a member sees, plus per-user metrics for members of the team(s) they own | — |
| **Admin** | All users' data, all aggregates | Users, roles, teams, data sources, data refresh |

Practical effects you will notice:

- The user-selection dropdown on activity pages only lists users you are entitled to view.
- Settings pages (User Management, Teams, Data Sources) are functional for admins; other roles will receive authorization errors on write operations.
- Team-view toggles show aggregates for your team even if you cannot see individual teammates' rows.

## 3. First-Run Setup (Administrators)

A recommended order of operations on a fresh deployment:

1. **Sign in as the initial admin** (the `initialAdminEmail` user).
2. **Sync users** — go to **Settings → User Management** and run the Identity Center sync. This imports users (email, name, Identity Center ID) as members.
3. **Assign roles** — edit users to promote team owners or additional admins as needed.
4. **Set per-user identifiers (only where they differ from email)** — by default, the platform matches users to each data source by searching the provider for their platform **email**, so most users need no configuration. Edit a user and set a source-specific identifier (GitHub username, GitLab username, Jira email, Asana email, Claude API email, …) only when their identity on that source differs — e.g., a GitHub handle unrelated to their email, or a personal email on the provider side. A set identifier overrides the email-based lookup for that source; internal/numeric IDs are resolved and cached automatically on the next ETL run.
5. **Add data sources** — see [Section 4](#4-connecting-data-sources).
6. **Map platform teams** — see [Section 9](#9-teams).
7. **(Optional) Upload Kiro activity CSVs** to the S3 bucket under the `kiro-user-activity-logs/` prefix (bucket name is a stack output; schema is documented in the README).
8. **Refresh data** — on **User Management**, use **Refresh Data** to trigger an immediate ETL run instead of waiting for the 24-hour schedule.

## 4. Connecting Data Sources

**Settings → Data Sources** lists all configured integrations. Click **Add Data Source** to open the wizard.

| Source | Category | Auth options | Notes |
|--------|----------|--------------|-------|
| GitHub | IDP | [PAT, GitHub App](#a1-github) | Set **prodEnvironments** for DORA deployment tracking |
| GitLab | IDP | [PAT, OAuth App](#a2-gitlab) | Set **prodEnvironments** for DORA deployment tracking |
| Jira | WorkManagement | [API Token, OAuth 2.0](#a3-jira) | Set **Incident Labels** for DORA CFR/MTTR |
| Asana | WorkManagement | [PAT, OAuth App](#a4-asana) | |
| Claude API | CodeTool | [Admin API Key](#a5-claude-api-anthropic) | Authoritative Anthropic usage/cost |

See [Appendix A](#appendix-a-obtaining-credentials-per-data-source) for step-by-step instructions on obtaining each credential type.

**Built-in sources (nothing to add):** codeburn and Kiro do not appear in the wizard and require no credentials or setup on this page — they are deployed with the platform and ingest through dedicated paths:

- **codeburn** — activates as soon as developers run `codeburn sync push` (Section 5)
- **Kiro** — activates as soon as activity CSVs are uploaded to the S3 bucket under the `kiro-user-activity-logs/` prefix; cost is derived at a configurable $/credit rate

Key behaviors:

- **Credentials are validated on save** — a failing token or key is rejected immediately with the provider's error.
- **OAuth-based sources** (Jira Cloud, Asana, GitLab OAuth): after creating the source with client credentials, click **Authorize** on its row to complete the browser OAuth flow. The redirect URL to register with the provider is `https://<your-cloudfront-domain>/oauth-callback`.
- Fields are validated for SSRF-safety — private/loopback addresses are rejected in URL fields.
- New sources take effect on the next ETL run (or immediately after **Refresh Data**).

## 5. Sending AI Usage Data (Developers)

codeburn is the recommended way to get your AI-tool usage (tokens, cost, model, sessions for 35+ tools) into the platform. Each developer connects once from their own machine:

```bash
npm install -g codeburn

# One-time: point codeburn at the platform and log in (opens a browser, PKCE)
codeburn sync setup <ApiUrl>     # ask your admin for the ApiUrl stack output

# Push usage whenever you like (already-sent data is deduplicated server-side)
codeburn sync push

codeburn sync status             # shows endpoint + auth status
```

Important:

- **You must be a registered platform user first** (synced or admin-created) — pushes from unregistered emails are rejected. After you're registered, replay history with `codeburn sync reset --confirm && codeburn sync push --since all`.
- Your usage appears on the **AI Dashboard** after the next ETL run (or when an admin clicks **Refresh Data**).
- Pushing again is always safe — spans are deduplicated on the server.

## 6. Navigating the Dashboard

The left navigation contains:

### Global Metrics
Org-wide summary cards (blended AI cost, commits, issues, bug injection ratio, AI-efficiency ratios such as AI cost per story point / commit / merged PR) plus daily bar charts. CodeTool overlay toggles let you superimpose AI usage lines on delivery charts to eyeball correlation.

### DORA
The four DORA metrics with Elite/High/Medium/Low tier badges and trend charts:
Deployment Frequency, Lead Time for Changes, Change Failure Rate, and MTTR. Per-team drill-down is available. See [Section 7](#7-understanding-dora-metrics) for how the numbers are computed.

### User Activity
- **AI Dashboard** — all AI tools in one view: blended cost and token charts stacked by tool, source badges (provider-reported / derived / estimated), and model-level breakdowns.
- **GitHub / GitLab / Jira / Asana** — per-source activity pages. *These entries only appear when a data source of that type exists.* Each page offers:
  - a user selector (scoped to what your role allows),
  - a team/group view toggle,
  - a date-range picker (persisted across visits),
  - CodeTool overlay toggles.

  Metrics per page: commits and PRs/MRs per day, average cycle time and time-to-first-review (GitHub/GitLab); issues, story points, bug injection ratio, sprint burndown and velocity (Jira); tasks and story points (Asana).

### Settings
- **User Management** — sync users, edit identifiers and roles, trigger **Refresh Data** (admin only).
- **Teams** — define platform teams and map source-specific team constructs to them ([Section 9](#9-teams)).
- **Data Sources** — add/edit/delete integrations ([Section 4](#4-connecting-data-sources)).

Common controls: all charts share the date-range picker conventions (presets plus custom range), and every IDP/WorkManagement page supports the same CodeTool overlay toggles configured by the deployment.

## 7. Understanding DORA Metrics

| Metric | How it's computed | What feeds it |
|--------|-------------------|---------------|
| **Deployment Frequency** | Count of deployments to production environments | GitHub/GitLab deployment events where the environment matches the source's **prodEnvironments** list (default `production,prod`) |
| **Lead Time for Changes** | Deploy time − head-commit time, per deployment (a lower bound); percentiles computed at query time | Same deployment events |
| **Change Failure Rate** | (failed deployments + incidents opened) ÷ deployments | Deployment statuses + Jira issues carrying an **Incident Label** |
| **MTTR** | Mean resolution time of incidents | Jira incident issues (created → resolved) |

To get meaningful numbers:

- Ensure your CI/CD actually records **deployments** (GitHub Deployments API / GitLab deployments) to environments named in **prodEnvironments** — merges alone don't count.
- Tag production incidents in Jira with a label listed in the Jira source's **Incident Labels** field (default `incident`) — see below.

### Incidents (Change Failure Rate and MTTR)

Incidents are ordinary **Jira issues carrying an incident label**. There is no separate incident tracker — your existing Jira workflow is the source of truth.

**Setup:**

1. On the Jira data source (**Settings → Data Sources → edit**), set **Incident Labels** — a comma-separated list of Jira labels that mark an issue as a production incident. Default: `incident`. Example: `incident, sev1, sev2, outage`.
2. In Jira, apply one of those labels to any issue representing a production incident. Issue type doesn't matter (Bug, Task, custom types all work) — only the label does.
3. **Resolve the Jira issue when the incident is over** — MTTR is derived from Jira's resolution timestamp.

**How incidents are counted:**

- **Scope is org-wide** — every Jira project the API credential can see is searched; incidents are not filtered by assignee or by registered platform users. A per-project breakdown is also recorded for team drill-downs.
- **Change Failure Rate:** an issue counts toward CFR on the day it was **created** (label present at ETL time). CFR = (failed deployments + incidents opened) ÷ deployments for the period.
- **MTTR:** an issue contributes on the day it was **resolved**. Its resolution time is `resolution date − created date`; MTTR is the mean across incidents resolved in the period.

**Practices and caveats:**

- **Label promptly, resolve honestly.** The clock starts at issue *creation* — creating the Jira issue well after the incident started understates MTTR; leaving issues unresolved after recovery overstates it.
- **Labels are read at ETL time.** Adding an incident label to an issue later is picked up as long as its created/resolved date falls in a day the ETL still reprocesses (today is always re-fetched; older days are not revisited once processed).
- **One label is enough** — an issue matching *any* configured label counts once.
- Up to 200 incidents per day are counted — far beyond any realistic volume, but worth knowing for bulk-import scenarios.
- If CFR/MTTR show no data, verify the label spelling matches exactly (Jira labels are case-sensitive) and that the Jira credential can see the projects where incidents are filed.

## 8. Understanding AI Usage Data

Multiple sources can report usage for the same person and tool. The platform blends them with a precedence rule so nothing is double-counted:

- **codeburn** provides the estimated baseline for every tool it sees.
- **Authoritative sources** (Anthropic Admin API via the Claude API plugin, Kiro service credits) override codeburn's numbers for that tool on that day.

The AI Dashboard's **source badges** tell you which figure you're looking at:

| Badge | Meaning |
|-------|---------|
| provider-reported | Exact figure from the vendor's API |
| derived | Computed from authoritative inputs (e.g., Kiro credits × configured $/credit rate) |
| estimated | codeburn client-side estimate |

Costs shown for estimated sources are directionally useful but not billing-grade; provider-reported figures are.

## 9. Teams

Different tools have different team constructs (GitHub teams/orgs, GitLab groups, Jira projects, Asana teams). The **Settings → Teams** page lets admins define **platform teams** — the single canonical team used for aggregates, team-owner visibility, and DORA drill-downs — and map each source's construct onto them.

### Creating a platform team

1. Go to **Settings → Teams** and click **Create Team**.
2. **Team Name** — a unique name (e.g., "Platform Engineering").
3. **Owners** — select one or more registered users. Owners can see their team members' individual data, and are automatically added to the team as members with the owner role.
4. Save. Members and mappings can be added now or later via **Edit**.

### Mapping data source teams

Each team's edit modal has a **Mappings** section per connected source (GitHub, GitLab, Jira, Asana):

- **Select from discovered teams** — the dropdown is pre-populated with teams/groups/projects the ETL has discovered from each data source (GitHub team slugs, GitLab group paths, Jira project keys, Asana team names). Discovery happens on each ETL run, so a freshly added data source shows its teams after the next run (or a **Refresh Data**).
- **Add custom value** — if a team hasn't been discovered yet (or you want to pre-map before the first ETL run), type the exact source-side name and click **Add**. It behaves identically to a discovered value once the ETL sees it.
- **One-to-one claiming** — a source team can only be mapped to one platform team; teams already claimed by another platform team are marked in the dropdown so you don't double-map.
- Remove a mapping by dismissing its token.

Once mapped, activity attributed to users in those source teams rolls up to the platform team on subsequent ETL runs.

### Assigning members

Team **membership** is managed from **Settings → User Management** — edit a user and assign their team there. The Teams page displays members read-only (owners are the exception: adding an owner auto-adds them as a member).

### Repositories

Each team also has a **Repositories** list used for team-level DORA attribution:

- **Derived repos** are computed automatically on each ETL run from the team's source mappings (e.g., repos belonging to a mapped GitHub team) and refresh with a timestamp shown in the modal.
- You can also **add repos manually** (`org/repo-name`) for anything the mappings don't capture.

### Notes

- Source-side team memberships discovered by the ETL expire after 7 days if a source stops reporting them, so stale memberships age out automatically.
- Deleting a platform team removes the mapping and rollup — it does not touch any source-side teams or user data.

## 10. Data Freshness and Refresh

- The ETL runs automatically **every 24 hours**, fetching each user's activity from every configured source for any unprocessed days. Today's partial data is always re-fetched on the next run.
- Admins can trigger an immediate run with **Refresh Data** on the User Management page. Global aggregates rebuild shortly after (a self-healing regenerator also runs hourly).
- New users, new identifiers, and new data sources all take effect on the next run — use **Refresh Data** to avoid waiting.

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| **Access Denied / 403 for everyone on a fresh deployment** | `initialAdminEmail` not configured, or the matching user hasn't signed in yet | Configure `initialAdminEmail` in the stage config and redeploy; then sign in with that email once to bootstrap the admin |
| **I can sign in but see "Access Denied"** | You authenticated with Cognito but aren't registered in the platform | Ask an admin to sync users from Identity Center or create your user; self-registration only works if enabled |
| **`codeburn sync push` is rejected** | Your email isn't a registered platform user yet | Get registered, then `codeburn sync reset --confirm && codeburn sync push --since all` to replay |
| **My activity shows zero on GitHub/GitLab/Jira/Asana pages** | The default email-based lookup didn't find you on that source (different handle/email there), or a set identifier is wrong | Admin: edit the user in User Management and set the correct source-specific identifier, then Refresh Data |
| **A source page (e.g., GitLab) is missing from the nav** | No data source of that type is configured | Add it under Settings → Data Sources |
| **DORA shows no deployments** | CI/CD isn't creating deployment events, or environment names don't match **prodEnvironments** | Emit deployments from CI/CD and/or align the environment list on the data source |
| **Change Failure Rate / MTTR are empty** | No Jira issues carry a configured incident label | Label production incidents in Jira with a label from the source's **Incident Labels** |
| **Data source creation fails on save** | Credential validation failed, or a URL points at a private address (blocked by SSRF protection) | Re-check the token/key and use the provider's public URL |
| **OAuth "Authorize" fails** | Redirect URL not registered with the provider | Register `https://<cloudfront-domain>/oauth-callback` in the provider's app settings |
| **Dashboard numbers look stale** | ETL hasn't run since your change | Admin: click **Refresh Data**; allow a few minutes for aggregates to rebuild |
| **Kiro data missing** | CSVs not uploaded, or wrong S3 prefix | Upload to the `kiro-user-activity-logs/` prefix in the ingestion bucket (see README for the CSV schema) |

---

## Appendix A: Obtaining Credentials per Data Source

The wizard's fields differ by source and auth option. This appendix walks through obtaining each credential. General notes:

- For OAuth-based options, register the redirect URL `https://<your-cloudfront-domain>/oauth-callback` in the provider's app settings, then click **Authorize** on the data source row after saving.
- Prefer a dedicated service/bot account over a personal account so credentials survive personnel changes.
- Grant read-only scopes wherever the provider allows it — the platform never writes to your tools.

### A.1 GitHub

**Option 1 — Personal Access Token (simplest):**

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token** (or a classic token).
2. Resource owner: your organization. Repository access: all repositories you want tracked.
3. Permissions (read-only): **Contents**, **Pull requests**, **Deployments**, **Members** (organization permission, for team discovery).
   - Classic token equivalent: `repo` (or `public_repo`), `read:org`.
4. Copy the token into the wizard's **Personal Access Token** field.

**Option 2 — GitHub App (recommended for orgs; no user-bound token):**

1. Org **Settings → Developer settings → GitHub Apps → New GitHub App**.
2. Permissions (read-only): Repository **Contents**, **Pull requests**, **Deployments**; Organization **Members**. No webhook needed — uncheck "Active" under Webhook.
3. Create the app, then note the **App ID** and generate a **Private Key** (downloads a `.pem` — paste its full contents into the wizard's **Private Key (PEM)** field).
4. **Install the app** on your organization (App page → Install App) and note the **Installation ID** (the number at the end of the installation URL: `.../settings/installations/<id>`).
5. Wizard fields: **App ID**, **Private Key (PEM)**, **Installation ID**.

### A.2 GitLab

**Option 1 — Personal Access Token:**

1. GitLab → **User Settings (avatar) → Access tokens → Add new token** (a group access token also works for group-wide coverage).
2. Scopes: `read_api` (covers commits, MRs, deployments, and group membership).
3. Copy the token into **Personal Access Token**. For self-managed GitLab, also set **GitLab URL** (defaults to gitlab.com when blank).

**Option 2 — OAuth App:**

1. GitLab → **User Settings → Applications** (or group/instance-level **Applications**) → **Add new application**.
2. Redirect URI: `https://<your-cloudfront-domain>/oauth-callback`. Scope: `read_api`. Uncheck "Confidential" only if instructed otherwise — the platform stores the secret server-side, so confidential is fine.
3. Copy the **Application ID → Client ID** and **Secret → Client Secret** into the wizard; set **GitLab URL** for self-managed instances.
4. Save the data source, then click **Authorize** to complete the browser consent flow.

### A.3 Jira

Both options are for **Jira Cloud**. The **Jira Base URL** field is your site URL, e.g. `https://your-org.atlassian.net`.

**Option 1 — API Token (simplest):**

1. Sign in at **id.atlassian.com → Security → Create and manage API tokens → Create API token**.
2. Wizard fields: **Jira Account Email** (the Atlassian account the token belongs to), **API Token**, **Jira Base URL**.
3. The account needs browse access to the projects you want tracked (including projects where incidents are filed).

**Option 2 — OAuth 2.0 App:**

1. Go to the **Atlassian Developer Console** (developer.atlassian.com) → **Create → OAuth 2.0 integration**.
2. Add the **Jira API** with classic scopes: `read:jira-work`, `read:jira-user`, plus `offline_access` (refresh tokens — required, since the ETL runs unattended).
3. Authorization → Callback URL: `https://<your-cloudfront-domain>/oauth-callback`.
4. Copy **Client ID** and **Client Secret** into the wizard along with **Jira Base URL**; save, then click **Authorize**.

Optional fields for either option: **Story Points Field** (your custom field id/name if not the default) and **Incident Labels** (see [Incidents](#incidents-change-failure-rate-and-mttr)).

### A.4 Asana

The **Workspace ID** is required for both options: find it at `https://app.asana.com/api/1.0/workspaces` while signed in (the `gid` value), or from your Asana admin console URL.

**Option 1 — Personal Access Token:**

1. Asana → **Settings → Apps → Developer apps → Manage developer apps → Create new token** (or app.asana.com/0/my-apps).
2. Copy the token into **Personal Access Token** and set **Workspace ID**.

**Option 2 — OAuth App:**

1. **developers.asana.com → My apps → Create new app**.
2. OAuth → Redirect URL: `https://<your-cloudfront-domain>/oauth-callback`. Default scopes are sufficient (read access to tasks and teams).
3. Copy **Client ID** and **Client Secret** into the wizard, set **Workspace ID**, save, then click **Authorize**.

Optional: **Story Points Field Name** if your workspace tracks points in a custom field.

### A.5 Claude API (Anthropic)

Requires an Anthropic organization with an **Admin API key** (distinct from regular API keys — it reads org-wide usage/cost, not model inference):

1. Anthropic **Console → Settings → Organization → Admin keys** (you must be an org admin).
2. Create an Admin API key (`sk-ant-admin...`) and paste it into the wizard's **Admin API Key** field.
3. Users are matched by their Anthropic account email — set **Claude API email** on users whose Anthropic email differs from their platform email (see [Section 3](#3-first-run-setup-administrators), step 4).

### Built-in sources (no credentials)

**codeburn** and **Kiro** need nothing from this appendix — see [Section 4](#4-connecting-data-sources) and [Section 5](#5-sending-ai-usage-data-developers).
