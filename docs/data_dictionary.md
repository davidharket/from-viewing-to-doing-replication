# Data dictionary

This document describes the **schema** of the five CSV exports the analysis
consumes. The real files are **not** included in this repository: they contain
pseudonymised behavioural telemetry that is personal data under the GDPR and may
not be redistributed. The descriptions below give column names and types only —
never real rows.

To run the pipeline you need a directory containing all five files with these
exact names. `data/synthetic_sample/` holds a random, non-personal example with
the same schema (see [`../data/README.md`](../data/README.md)).

Timestamps are ISO-8601 UTC strings (`YYYY-MM-DDTHH:MM:SSZ`). Empty cells denote
absence (e.g. a video that was never completed).

---

## 1. `tosee-onboarding-experiment-clarity-observable.csv`

The primary per-episode table. One row per onboarding episode (one assigned
user's first onboarding). This is the unit of analysis. Rows with
`excluded_from_analysis = 1` are dropped before estimation.

| Column | Type | Description |
| --- | --- | --- |
| `assignment_id` | int | Pseudonymous episode/assignment identifier (join key to events). |
| `variant` | str | Experimental condition: `avatar` or `control` (`non_avatar` is normalised to `control`). |
| `assigned_at` | datetime | When the user was allocated to a condition. |
| `first_exposure_at` | datetime | First time the onboarding stimulus was shown. |
| `completed_at` | datetime | When the episode record was finalised. |
| `excluded_from_analysis` | int (0/1) | 1 if the row should be excluded (internal/QA/bot/ineligible). |
| `exclusion_reason` | str | Free-text reason when excluded; empty otherwise. |
| `clarity_observable` | int (0/1) | Diagnostic flag: whether session-replay (Clarity) telemetry was observable for the episode. Used only as a coverage diagnostic, not as a consent denominator. |
| `tracked_event_count` | int | Number of first-party events logged for the episode (logging-coverage diagnostic). |
| `video_started_at` | datetime | When onboarding video playback began; empty if never started. |
| `video_completed_at` | datetime | When the onboarding video completed; empty otherwise. |
| `video_skipped_at` | datetime | When the video was skipped/dismissed; empty otherwise. |
| `watch_percentage` | int | Highest watch threshold reached. Threshold-derived, not continuous: one of {0, 25, 50, 75, 90, 100}. |
| `cta_clicked_at` | datetime | When the onboarding call-to-action was clicked; empty otherwise. |
| `first_meaningful_action_at` | datetime | First substantive post-onboarding action; empty otherwise. |
| `first_video_created_at` | datetime | When the user's first generated video completed; empty otherwise. |
| `first_video_within_7_days` | int (0/1) | Whether a generated video was completed within seven days (downstream boundary outcome). |
| `time_to_first_video_hours` | float | Hours from exposure to first generated video; empty if none. |

## 2. `tosee-local-onboarding-experiment-events.csv`

Long-format first-party event log. Many rows per episode. The analysis derives
per-episode behavioural flags by counting events per `assignment_id`, and reads
fine-grained fields out of `payload_json`.

| Column | Type | Description |
| --- | --- | --- |
| `id` | int | Event row identifier. |
| `assignment_id` | int | Episode identifier (join key to the table above). |
| `onboarding_episode_id` | str | Pseudonymous episode identifier. |
| `variant` | str | Condition (`avatar` / `control`). |
| `event_name` | str | Event type (vocabulary below). |
| `source` | str | Logging source label. |
| `created_at` | datetime | Event timestamp. |
| `payload_json` | str (JSON) | Event-specific payload (keys below). |

### `event_name` vocabulary used by the analysis

The estimator references these event types. Each becomes a per-episode
`has_<event_name>` indicator and a count:

- `onboarding_modal_shown` — carries acquisition/consent context in its payload.
- `onboarding_video_started`, `onboarding_video_completed`
- `onboarding_video_progress_25`, `_50`, `_75`, `_90` — watch-depth thresholds.
- `onboarding_cta_clicked`, `first_meaningful_action`, `create_video_page_opened`
  — collapsed into the **funnel-entry trigger** (task-entry intent).
- `video_create_started` — the primary **creation-initiation** endpoint.
- `video_create_failed` — downstream boundary diagnostic.
- `first_video_created` — generated-video completion (downstream, paywall-constrained).

Other event types may appear in a real export (e.g. `onboarding_video_paused`,
`onboarding_video_seeked`, `onboarding_video_speed_changed`); they are ignored by
the current analysis.

### `payload_json` keys read by the analysis

From video events (`onboarding_video_*`):

| Key | Type | Description |
| --- | --- | --- |
| `video_current_time_ms` | int | Playback position in milliseconds. The per-episode maximum, divided by duration, gives the secondary "maximum observed video position" measure. |
| `video_duration_ms` | int | Stimulus duration in milliseconds. |

From `onboarding_modal_shown`:

| Key | Type | Description |
| --- | --- | --- |
| `has_gclid` | bool | Whether a Google click identifier was retained (acquisition-balance diagnostic). |
| `attribution_source` | str | Acquisition source. |
| `attribution_medium` | str | Acquisition medium. |
| `attribution_campaign` | str | Acquisition campaign. |
| `analytics_consent_state` | str | Analytics consent state at modal display. |
| `clarity_enabled` | bool | Whether session-replay was enabled for the episode. |

## 3. `tosee-onboarding-experiment-summary.csv`

Three-row condition summary (stored in the rebuilt database for completeness; not
used in estimation).

| Column | Type |
| --- | --- |
| `variant` | str |
| `assigned` | int |
| `exposed` | int |
| `clarity_observable_exposed` | int |
| `analysis_exposed` | int |
| `first_video_created` | int |
| `cta_clicked` | int |

## 4. `tosee-local-onboarding-experiment-funnel-by-variant.csv`

Per-condition funnel counts (stored for completeness; not used in estimation).

Columns: `variant`, `assigned`, `exposed`, `video_started`, `watched_25`,
`watched_50`, `watched_75`, `watched_90`, `video_completed`,
`skipped_or_dismissed`, `cta_clicked`, `first_meaningful_action`,
`video_create_started`, `first_video_created`, `clarity_observable` — all `int`
except `variant`.

## 5. `tosee-local-onboarding-experiment-daily-rollup.csv`

Daily event roll-up (stored for completeness; not used in estimation).

| Column | Type |
| --- | --- |
| `event_date_utc` | str (date) |
| `variant` | str |
| `event_name` | str |
| `events` | int |
| `assignments` | int |
| `users` | int |
| `clarity_enabled_events` | int |

---

## Derived measures (computed by `run_analysis.py`)

- **Task-entry intent** — 1 if any of CTA click, first meaningful action, or
  create-video-page open occurred (movement from onboarding into the task
  environment).
- **Time to task entry** — minutes from first exposure to the
  `first_meaningful_action_at` timestamp, treated as the canonical marker of
  task-environment entry; computed only for episodes that have that timestamp.
- **High-intent creation attempt** — 1 if `video_create_started` occurred (the
  primary creation-initiation endpoint).
- **Maximum observed video position (%)** — per-episode max
  `video_current_time_ms` / `video_duration_ms`, clipped to [0, 100].
- **Watch thresholds reached** (`watched_25/50/75/90`, `video_completed`) — from
  the corresponding progress events.
- **Paywall-constrained success** — `first_video_within_7_days`, reported only as
  a downstream boundary check, not a causal endpoint.
