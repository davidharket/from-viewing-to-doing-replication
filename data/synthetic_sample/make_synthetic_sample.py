#!/usr/bin/env python3
"""Generate a tiny, fully synthetic sample of the onboarding experiment export.

The real telemetry is pseudonymised personal data under the GDPR and is not
distributed with this repository. This script writes five CSV files with the
*same schema* as the real export but populated with random, non-personal values,
so that reviewers can run ``analysis/run_analysis.py`` end-to-end.

The numbers produced from this sample are meaningless. They exist only to prove
that the analysis code runs against data shaped like the real export. They are
NOT the results reported in the thesis.

Usage::

    python data/synthetic_sample/make_synthetic_sample.py
    python analysis/run_analysis.py --input-dir data/synthetic_sample --skip-figures
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

OUT_DIR = Path(__file__).resolve().parent
SEED = 20260519
N_PER_ARM = 14  # small but enough to keep two-by-two tables non-degenerate
WATCH_THRESHOLDS = [0, 25, 50, 75, 90, 100]
BASE_TIME = datetime(2026, 5, 6, 8, 0, 0)
VIDEO_DURATION_MS = 169_000  # the stimulus videos run 2:49

# Event types the analysis references by name. Keeping at least a few of each in
# the sample exercises every code path (e.g. video_create_failed must exist or
# the downstream-outcome table cannot be built).
EVENT_TYPES = [
    "onboarding_modal_shown",
    "onboarding_video_started",
    "onboarding_video_progress_25",
    "onboarding_video_progress_50",
    "onboarding_video_progress_75",
    "onboarding_video_progress_90",
    "onboarding_video_completed",
    "first_meaningful_action",
    "onboarding_cta_clicked",
    "create_video_page_opened",
    "video_create_started",
    "video_create_failed",
    "first_video_created",
]


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    rng = random.Random(SEED)
    assignment_rows: list[dict] = []
    event_rows: list[dict] = []
    daily_counter: dict[tuple[str, str, str], dict[str, int]] = {}
    event_id = 1

    for index in range(N_PER_ARM * 2):
        assignment_id = index + 1
        variant = "avatar" if index % 2 == 0 else "control"
        assigned = BASE_TIME + timedelta(hours=rng.uniform(0, 24 * 8))
        exposure = assigned + timedelta(seconds=rng.uniform(2, 40))

        # Avatar arm gets a small, deliberately fake nudge so the demo tables are
        # not perfectly flat. These probabilities are invented, not estimated.
        bump = 0.10 if variant == "avatar" else 0.0
        started = rng.random() < 0.70 + bump
        reached_50 = started and rng.random() < 0.55 + bump
        reached_75 = reached_50 and rng.random() < 0.6
        reached_90 = reached_75 and rng.random() < 0.6
        completed = reached_90 and rng.random() < 0.6
        cta = rng.random() < 0.45 + bump
        meaningful = cta or rng.random() < 0.5
        page_opened = meaningful and rng.random() < 0.7
        create_started = page_opened and rng.random() < 0.30 + bump
        create_failed = create_started and rng.random() < 0.25
        created = create_started and not create_failed and rng.random() < 0.4

        watch_percentage = rng.choice(
            [t for t in WATCH_THRESHOLDS if t <= (100 if completed else 90 if reached_90 else 75 if reached_75 else 50 if reached_50 else 25 if started else 0)]
            or [0]
        )

        # ---- per-episode event stream ----
        def add_event(name: str, when: datetime, payload: dict | None = None) -> None:
            nonlocal event_id
            event_rows.append(
                {
                    "id": event_id,
                    "assignment_id": assignment_id,
                    "onboarding_episode_id": f"synthetic-episode-{assignment_id:04d}",
                    "variant": variant,
                    "event_name": name,
                    "source": "synthetic",
                    "created_at": iso(when),
                    "payload_json": json.dumps(payload or {}),
                }
            )
            event_id += 1
            key = (when.strftime("%Y-%m-%d"), variant, name)
            bucket = daily_counter.setdefault(key, {"events": 0, "users": set()})
            bucket["events"] += 1
            bucket["users"].add(assignment_id)

        has_gclid = rng.random() < 0.5
        add_event(
            "onboarding_modal_shown",
            exposure,
            {
                "has_gclid": has_gclid,
                "attribution_source": "google" if has_gclid else "direct",
                "attribution_medium": "cpc" if has_gclid else "none",
                "attribution_campaign": "synthetic_campaign" if has_gclid else None,
                "analytics_consent_state": "granted",
                "clarity_enabled": rng.random() < 0.7,
            },
        )

        cursor = exposure
        max_position_ms = 0
        if started:
            cursor += timedelta(seconds=rng.uniform(1, 5))
            add_event("onboarding_video_started", cursor,
                      {"video_current_time_ms": 0, "video_duration_ms": VIDEO_DURATION_MS})
            ladder = [
                (reached_50 or watch_percentage >= 25, "onboarding_video_progress_25", 0.25),
                (reached_50, "onboarding_video_progress_50", 0.50),
                (reached_75, "onboarding_video_progress_75", 0.75),
                (reached_90, "onboarding_video_progress_90", 0.90),
            ]
            for fired, name, fraction in ladder:
                if fired:
                    cursor += timedelta(seconds=rng.uniform(10, 30))
                    position = int(VIDEO_DURATION_MS * fraction)
                    max_position_ms = max(max_position_ms, position)
                    add_event(name, cursor,
                              {"video_current_time_ms": position, "video_duration_ms": VIDEO_DURATION_MS})
            if completed:
                cursor += timedelta(seconds=rng.uniform(5, 20))
                max_position_ms = VIDEO_DURATION_MS
                add_event("onboarding_video_completed", cursor,
                          {"video_current_time_ms": VIDEO_DURATION_MS, "video_duration_ms": VIDEO_DURATION_MS})

        cta_at = None
        if cta:
            cursor += timedelta(seconds=rng.uniform(1, 30))
            cta_at = cursor
            add_event("onboarding_cta_clicked", cursor)
        meaningful_at = None
        if meaningful:
            cursor += timedelta(seconds=rng.uniform(1, 30))
            meaningful_at = cursor
            add_event("first_meaningful_action", cursor)
        if page_opened:
            cursor += timedelta(seconds=rng.uniform(1, 30))
            add_event("create_video_page_opened", cursor)
        if create_started:
            cursor += timedelta(seconds=rng.uniform(1, 60))
            add_event("video_create_started", cursor)
        if create_failed:
            cursor += timedelta(seconds=rng.uniform(1, 20))
            add_event("video_create_failed", cursor, {"reason": "synthetic_failure"})
        created_at = None
        if created:
            cursor += timedelta(minutes=rng.uniform(1, 120))
            created_at = cursor
            add_event("first_video_created", cursor)

        within_7_days = 1 if created else 0
        time_to_first_video_hours = (
            round((created_at - exposure).total_seconds() / 3600, 3) if created_at else ""
        )

        assignment_rows.append(
            {
                "assignment_id": assignment_id,
                "variant": variant,
                "assigned_at": iso(assigned),
                "first_exposure_at": iso(exposure),
                "completed_at": iso(cursor),
                "excluded_from_analysis": 0,
                "exclusion_reason": "",
                "clarity_observable": 1 if rng.random() < 0.72 else 0,
                "tracked_event_count": int(rng.uniform(4, 18)),
                "video_started_at": iso(exposure) if started else "",
                "video_completed_at": iso(cursor) if completed else "",
                "video_skipped_at": "",
                "watch_percentage": watch_percentage,
                "cta_clicked_at": iso(cta_at) if cta_at else "",
                "first_meaningful_action_at": iso(meaningful_at) if meaningful_at else "",
                "first_video_created_at": iso(created_at) if created_at else "",
                "first_video_within_7_days": within_7_days,
                "time_to_first_video_hours": time_to_first_video_hours,
            }
        )

    assignments = pd.DataFrame(assignment_rows)
    events = pd.DataFrame(event_rows)

    # ---- aggregate views (stored for completeness; not used in estimation) ----
    def arm_counts(variant: str) -> dict:
        arm = assignments[assignments["variant"] == variant]
        arm_events = events[events["assignment_id"].isin(arm["assignment_id"])]

        def n_events(name: str) -> int:
            return int((arm_events["event_name"] == name).sum())

        return {
            "variant": variant,
            "assigned": len(arm),
            "exposed": int((arm["video_started_at"] != "").sum()),
            "video_started": n_events("onboarding_video_started"),
            "watched_25": n_events("onboarding_video_progress_25"),
            "watched_50": n_events("onboarding_video_progress_50"),
            "watched_75": n_events("onboarding_video_progress_75"),
            "watched_90": n_events("onboarding_video_progress_90"),
            "video_completed": n_events("onboarding_video_completed"),
            "skipped_or_dismissed": 0,
            "cta_clicked": n_events("onboarding_cta_clicked"),
            "first_meaningful_action": n_events("first_meaningful_action"),
            "video_create_started": n_events("video_create_started"),
            "first_video_created": n_events("first_video_created"),
            "clarity_observable": int(arm["clarity_observable"].sum()),
        }

    funnel = pd.DataFrame([arm_counts("avatar"), arm_counts("control")])

    summary = pd.DataFrame(
        [
            {
                "variant": row["variant"],
                "assigned": row["assigned"],
                "exposed": row["exposed"],
                "clarity_observable_exposed": row["clarity_observable"],
                "analysis_exposed": row["assigned"],
                "first_video_created": row["first_video_created"],
                "cta_clicked": row["cta_clicked"],
            }
            for _, row in funnel.iterrows()
        ]
    )

    daily = pd.DataFrame(
        [
            {
                "event_date_utc": date,
                "variant": variant,
                "event_name": name,
                "events": bucket["events"],
                "assignments": len(bucket["users"]),
                "users": len(bucket["users"]),
                "clarity_enabled_events": 0,
            }
            for (date, variant, name), bucket in sorted(daily_counter.items())
        ]
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT_DIR / "tosee-onboarding-experiment-summary.csv", index=False)
    funnel.to_csv(OUT_DIR / "tosee-local-onboarding-experiment-funnel-by-variant.csv", index=False)
    daily.to_csv(OUT_DIR / "tosee-local-onboarding-experiment-daily-rollup.csv", index=False)
    assignments.to_csv(OUT_DIR / "tosee-onboarding-experiment-clarity-observable.csv", index=False)
    events.to_csv(OUT_DIR / "tosee-local-onboarding-experiment-events.csv", index=False)

    print(f"Wrote synthetic sample to {OUT_DIR}")
    print(f"  assignments: {len(assignments)} episodes ({N_PER_ARM} per arm)")
    print(f"  events:      {len(events)} rows")


if __name__ == "__main__":
    main()
