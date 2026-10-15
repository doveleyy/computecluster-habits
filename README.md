# Habits

Habits is one household application with Overview, Water, Budget, Study and
Progress pages. Each linked platform user owns separate drinks, spending,
allowances, a sinking fund, savings targets and focus sessions. All pages share
one process, one container and one SQLite database. Its SQLite schema, money
rules and web assets are unchanged in version `0.13.1`. This release uses the
published Home Platform client for identity and builds independently of the
platform repository.

## Develop locally

Use Python 3.11 or later. Run these commands from this checkout.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
hp-app dev --definition manifest.template.json --command python -m services.habit_tracker
```

Open the printed fictional gateway URL and choose Alex or Sam. The development
runner provides a disposable database and credential, a local HTTP platform,
and a random app port. Ctrl-C stops the app and removes its temporary state.
The backend uses the same published client and identity endpoint as managed
operation. There is no development identity switch in the app. The platform
navigation iframe requires the real platform; the fictional gateway does not
provide it.

Run the standalone checks and build the actual image.

```sh
python -m tools.check
docker build -t habits:local .
python vendor/hp-app-check.pyz --manifest manifest.template.json --template --image habits:local --json
```

The checks cover the existing water, budget, study and progress behavior,
actual client HTTP identity, denied direct and forged requests, owner
isolation, immediate revocation, study reads that never wait on the write lock,
offline identity adoption, unchanged state and publication evidence. The
boundary check rejects imports from the platform implementation. The portable
checker starts only its own disposable containers and reports seven contract
checks. Start Docker before using it.

Kit version `0.1.1` is pinned in `vendor/SHA256SUMS`. CI verifies those hashes
before installing the wheels or executing the checker. Keep the reviewed wheel,
checker and hash file together when updating the kit. The runtime image includes
only the client wheel and app dependencies. It excludes devtools, tests,
credentials and Git history.

## Source map

```text
services/habit_tracker/
  main.py                 app factory, identity, page rendering, API routes
  settings.py             managed runtime settings
  water.py                drink persistence, classification, daily water goals
  budget.py               spending, daily allowances, savings targets, ledger
  study.py                named focus sessions and daily study totals
  progress.py             typed weekly summaries and saved event partitioning
  stats.py                read-only week, month and year statistics
  state_probe.py          read-only hash of the records this version reads
  templates/              overview, water, budget, study and progress pages
  static/                 page styles and scripts
  tests/                  app, data, migration and authorization checks
```

## Connect to the platform

The platform supplies the managed runtime settings.

| Setting | Purpose |
| --- | --- |
| `APP_ID` | `habits` for the release manifest |
| `APP_BASE_PATH` | External gateway prefix, such as `/habits` |
| `APP_STATE_DIR` | Writable state directory containing `habit-tracker.db` |
| `APP_CREDENTIAL_FILE` | Read-only file containing the app credential |
| `PLATFORM_API_URL` | Reachable HTTP endpoint ending in `/api/apps/v1` |
| `TZ` | Household timezone, such as `Asia/Singapore` |

`TZ` decides where each local day begins: daily water totals, allowance
settlement, backdated entries and focus totals all use it. It defaults to
`UTC`, so set the household timezone explicitly. The process defaults to port
`8100`. `APP_PORT` and `APP_HOST` can override the listener for local
development. The container uses non-root UID and GID `10001`. Provide writable
state owned by that user, a read-only credential mount and the platform's
network and resource restrictions. Keep credentials outside the checkout and
image.

The gateway removes `APP_BASE_PATH` before forwarding. The backend serves `/`
internally and uses the configured prefix in navigation, asset and API URLs. A
page URL with a trailing slash redirects to the page inside the prefix. Every
user request resolves the opaque `X-Platform-User-Context` through the
platform. Stable owner keys remain `home-platform:<user UUID>`. Missing or
refused context fails closed. Core unavailability returns `503`. Legacy
`HABIT_TRACKER_*` settings and variables beginning with `HP_FIXTURE_` are
refused even when set to false.

The owner rows are written only when a user is new or the platform reports a
changed display name. Reads by an existing user therefore do not take the
SQLite write lock. Rows stored under an earlier network-derived key move to the
stable key only through the repositories' transactional adoption steps, run
offline from a reviewed mapping; requests never adopt rows.

`/health` checks nothing outside the process. `/ready` checks local SQLite.
`/version` reports service `habits` and app version `0.13.1`. The app has no
scheduled maintenance. Expired focus sessions complete on the owner's next
request.

## Inspect state

To inspect a database without modifying it, run the read-only probe.

```sh
python -m services.habit_tracker.state_probe /path/to/disposable/habit-tracker.db
```

The output contains a row count and a hash for each table that this version
reads. `TABLES` in the probe declares those tables, their columns and their
keys. Each hash covers only the declared columns, in key order, so a database
whose columns were appended by `ALTER TABLE` gives the same output as a fresh
one. The output contains no drink, money or study records and no owner keys.

A backward-compatible migration that adds a nullable or defaulted column, a
table or an index leaves the output unchanged. A changed, added or deleted
record changes it. A missing declared table or column, which a destructive
migration causes, makes the probe fail, as does a failed SQLite integrity or
foreign-key check. SQL text, column order, `sqlite_sequence` and planner
statistics are not part of the output. NULL values and empty tables are not
encoded, so a later release can add a new nullable column or table to `TABLES`
and still print the same output for existing records. Keep a column added with
a non-NULL default out of `TABLES` until the probe omits that default. Compare
complete output before and after startup against a disposable copy of old
state. This release adds no schema migration. Production cutover requires the
operator's verified backup, owner checks and current-data recovery plan. The
runtime image includes this probe, so the operator can use it for managed
upgrade compatibility checks without a platform source checkout.

## Behavior reference

`GET /api/progress?week=YYYY-MM-DD` accepts a Monday on or before the current
week. Omitting `week` selects the current local week. The response contains
fourteen daily summaries, the selected week's records, and the current water
goal. Future days have null metrics. Daily allowances retain their effective
dates; dates before a budget plan have no allowance. Spending includes only
purchases. Saved focus sessions are split at local midnight. Comparisons
include completed weekdays only; the total and average explicitly include
partial today.

`GET /api/stats?metric=water|spending|focus&period=week|month|year&anchor=YYYY-MM-DD`
returns one metric for the week (Monday to Sunday), calendar month or
calendar year containing `anchor`, together with the period before it.
Omitting `anchor` selects today. A future anchor is refused. So is an anchor
whose period, previous period and heatmap together span more than 731 local
days, which keeps every request to at most two years of records.
Week and month buckets are days and year buckets are months. Values are
millilitres, cents of purchases or focus seconds, and future buckets are null.
Water days are judged against the current goal because goals have no history.
Spending days are judged against the allowance effective on that day, and days
before the budget plan have no allowance. A day counts for focus when it has
any focus. The comparison with the previous period covers the same number of
completed days. The response also has a 365-day heatmap and streak ending
today, a breakdown by drink type, category or activity, an hour-of-day
histogram that omits backdated entries, and cumulative pace for water and
spending weeks and months. The route reads each table once over one date range
and writes nothing except the existing completion of expired focus sessions.

Water's goal calendar and Budget's date controls select the day shown in the
logging and activity cards. Entries added while a past day is selected are
assigned to that local date. Future dates are rejected. Budget entries cannot
predate the account's budget plan. The daily allowance control edits only today
and takes effect from today forward. Every allowance change is also recorded in
the append-only `budget_adjustments` audit. Past entries are stored at local
noon, with a separate recording timestamp. Editing an earlier day's spending
recalculates the sinking fund from the revised daily settlement.

The unnamespaced water API routes (`/api/today`, `/api/drinks`,
`/api/settings`, `/api/history`) remain compatibility aliases. New UI code uses
`api/water/...` beneath the prefix.

## Publish an update

Push changes to this app repository. Pull requests run its checks, build the
image and run the template checker. A successful `main` push additionally builds
Linux amd64 and arm64 images, fills the commit and immutable image digest into
the final manifest, and checks that exact final image.

The workflow uploads `manifest.json` and `publication.json` as both a workflow
artifact and a GitHub release. The proof identifies this repository, commit,
publishing workflow, run and exact manifest bytes. It uses only the repository's
own `GITHUB_TOKEN`. It needs no platform source, SSH access or operator secret.
Private image and release readers are configured once by the platform owner.

Publication makes the release available for discovery. The platform owner
selects a staging preview and approves deployment of that exact release.
Passing CI does not authorize a production update.
