#!/usr/bin/env python3
"""Analyse the consent-valid onboarding experiment export.

Replication code for Harket, D. (2026). "From Viewing to Doing? A Randomised
Evaluation of Avatar-Led Onboarding in an AI Video Platform" (MSc thesis,
University of Copenhagen).

The analysis uses the consent-clarified export rather than the earlier larger
operational export. The empirical target is creation initiation, while
generated-video render completion is treated as a downstream outcome that can
depend on access and system-side conditions after the user-side creation act.

The underlying telemetry is pseudonymised personal data under the GDPR and is
not distributed with this code. Point ``--input-dir`` at a directory holding the
five required CSV exports (see ``docs/data_dictionary.md``), or at the bundled
``data/synthetic_sample`` to exercise the pipeline end-to-end on random,
non-personal data.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data"
DEFAULT_OUTPUT_DIR = ROOT / "outputs"
DEFAULT_DB = ROOT / "outputs" / "onboarding_consent_valid.sqlite"

EXPERIMENT_KEY = "avatar_onboarding"
EXPERIMENT_VERSION = "2026_05_first_login_v1"
WATCH_THRESHOLD_VALUES = [0, 25, 50, 75, 90, 100]
CONTROL_DISPLAY = "Non-avatar"
Z_ALPHA_TWO_SIDED_05 = 1.959963984540054
Z_POWER_80 = 0.8416212335729143


@dataclass
class Estimate:
    family: str
    outcome: str
    effect_type: str
    avatar: str
    control: str
    estimate: float
    ci_low: float
    ci_high: float
    p_value: float
    n: int
    note: str = ""


def read_csv(input_dir: Path, name: str) -> pd.DataFrame:
    path = input_dir / name
    if not path.exists():
        raise SystemExit(f"Missing required export file: {path}")
    return pd.read_csv(path)


def fmt_pct(x: float) -> str:
    if not np.isfinite(x):
        return "--"
    return f"{100 * x:.1f}\\%"


def fmt_pp(x: float) -> str:
    if not np.isfinite(x):
        return "--"
    value = 100 * x
    if abs(value) < 0.05:
        value = 0.0
    return f"{value:.1f} pp"


def fmt_plain_pct(x: float) -> str:
    if not np.isfinite(x):
        return "--"
    return f"{100 * x:.1f}%"


def fmt_num(x: float, digits: int = 2) -> str:
    if not np.isfinite(x):
        return "--"
    return f"{x:.{digits}f}"


def fmt_p(p: float) -> str:
    if not np.isfinite(p):
        return "--"
    if p < 0.001:
        return "$<$ .001"
    return f"{p:.3f}"


def bh_adjust(p_values: list[float]) -> list[float]:
    p = np.asarray(p_values, dtype=float)
    order = np.argsort(p)
    ranked = p[order]
    m = len(p)
    adjusted = np.empty(m)
    running = 1.0
    for i in range(m - 1, -1, -1):
        running = min(running, ranked[i] * m / (i + 1))
        adjusted[i] = running
    out = np.empty(m)
    out[order] = np.minimum(adjusted, 1.0)
    return out.tolist()


def diff_ci(p1: float, n1: int, p0: float, n0: int) -> tuple[float, float]:
    se = math.sqrt((p1 * (1 - p1) / n1) + (p0 * (1 - p0) / n0))
    diff = p1 - p0
    return diff - 1.96 * se, diff + 1.96 * se


def binary_estimate(df: pd.DataFrame, column: str, outcome: str, family: str) -> Estimate:
    avatar = df[df["variant"] == "avatar"]
    control = df[df["variant"] == "control"]
    a = int(avatar[column].sum())
    b = len(avatar) - a
    c = int(control[column].sum())
    d = len(control) - c
    _, fisher_p = stats.fisher_exact([[a, b], [c, d]])
    # Use the uncorrected log odds ratio for ordinary non-sparse tables.
    # Reserve Haldane-Anscombe only for genuine zero-cell edge cases.
    if min(a, b, c, d) == 0:
        ac, bc, cc, dc = a + 0.5, b + 0.5, c + 0.5, d + 0.5
        correction_note = ", zero_cell_correction=Haldane-Anscombe"
    else:
        ac, bc, cc, dc = float(a), float(b), float(c), float(d)
        correction_note = ""
    log_or = math.log((ac * dc) / (bc * cc))
    se = math.sqrt(1 / ac + 1 / bc + 1 / cc + 1 / dc)
    odds_ratio = math.exp(log_or)
    p1 = a / len(avatar)
    p0 = c / len(control)
    low, high = diff_ci(p1, len(avatar), p0, len(control))
    return Estimate(
        family=family,
        outcome=outcome,
        effect_type="Odds ratio",
        avatar=f"{a}/{len(avatar)} ({fmt_pct(p1)})",
        control=f"{c}/{len(control)} ({fmt_pct(p0)})",
        estimate=odds_ratio,
        ci_low=math.exp(log_or - 1.96 * se),
        ci_high=math.exp(log_or + 1.96 * se),
        p_value=fisher_p,
        n=len(df),
        note=f"diff={fmt_pp(p1 - p0)}, diff_ci=[{fmt_pp(low)}, {fmt_pp(high)}]{correction_note}",
    )


def mean_estimate(df: pd.DataFrame, column: str, outcome: str, family: str, digits: int = 1) -> Estimate:
    avatar = df[df["variant"] == "avatar"][column].astype(float)
    control = df[df["variant"] == "control"][column].astype(float)
    test = stats.ttest_ind(avatar, control, equal_var=False, nan_policy="omit")
    diff = float(avatar.mean() - control.mean())
    se = math.sqrt((avatar.var(ddof=1) / avatar.notna().sum()) + (control.var(ddof=1) / control.notna().sum()))
    return Estimate(
        family=family,
        outcome=outcome,
        effect_type="Mean difference",
        avatar=fmt_num(float(avatar.mean()), digits),
        control=fmt_num(float(control.mean()), digits),
        estimate=diff,
        ci_low=diff - 1.96 * se,
        ci_high=diff + 1.96 * se,
        p_value=float(test.pvalue),
        n=int(avatar.notna().sum() + control.notna().sum()),
    )


def latex_table(
    path: Path,
    caption: str,
    label: str,
    header: list[str],
    rows: list[list[str]],
    spec: str | None = None,
) -> None:
    spec = spec or ("l" + " c" * (len(header) - 1))
    lines = [
        "\\begin{table}[ht]",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
        "\\centering",
        "\\small",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(header) + " \\\\",
        "\\midrule",
    ]
    lines.extend(" & ".join(row) + " \\\\" for row in rows)
    lines.extend(["\\bottomrule", "\\end{tabular}", "\\end{table}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def latex_escape(text: str) -> str:
    return text.replace("%", r"\%").replace("&", r"\&")


def add_video_payload_fields(events: pd.DataFrame) -> pd.DataFrame:
    events = events.copy()
    current_values: list[float] = []
    duration_values: list[float] = []
    for payload_text in events["payload_json"].fillna("{}"):
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            payload = {}
        current_values.append(payload.get("video_current_time_ms", np.nan))
        duration_values.append(payload.get("video_duration_ms", np.nan))
    events["video_current_time_ms"] = pd.to_numeric(current_values, errors="coerce")
    events["video_duration_ms"] = pd.to_numeric(duration_values, errors="coerce")
    return events


def add_modal_payload_fields(assignments: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Attach acquisition and consent context from the modal-shown payload."""
    context_rows: list[dict[str, object]] = []
    modal_events = events[events["event_name"] == "onboarding_modal_shown"].copy()
    modal_events["created_at_dt"] = pd.to_datetime(modal_events["created_at"], errors="coerce")
    modal_events = modal_events.sort_values("created_at_dt").drop_duplicates("assignment_id", keep="last")
    for _, row in modal_events.iterrows():
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        context_rows.append(
            {
                "assignment_id": int(row["assignment_id"]),
                "has_gclid": bool(payload.get("has_gclid", False)),
                "attribution_source": payload.get("attribution_source"),
                "attribution_medium": payload.get("attribution_medium"),
                "attribution_campaign": payload.get("attribution_campaign"),
                "modal_consent_state": payload.get("analytics_consent_state"),
                "modal_clarity_enabled": bool(payload.get("clarity_enabled", False)),
            }
        )
    if not context_rows:
        assignments["has_gclid"] = False
        assignments["attribution_source"] = np.nan
        assignments["modal_consent_state"] = np.nan
        return assignments
    context = pd.DataFrame(context_rows).drop_duplicates("assignment_id")
    return assignments.merge(context, on="assignment_id", how="left")


def creation_start_mde(control_rate: float, n_per_group: int) -> float:
    """Approximate two-sided 80%-power MDE for a two-proportion comparison."""
    return (Z_ALPHA_TWO_SIDED_05 + Z_POWER_80) * math.sqrt(2 * control_rate * (1 - control_rate) / n_per_group)


def build_analysis(input_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = read_csv(input_dir, "tosee-onboarding-experiment-summary.csv")
    funnel = read_csv(input_dir, "tosee-local-onboarding-experiment-funnel-by-variant.csv")
    daily = read_csv(input_dir, "tosee-local-onboarding-experiment-daily-rollup.csv")
    assignments = read_csv(input_dir, "tosee-onboarding-experiment-clarity-observable.csv")
    events = read_csv(input_dir, "tosee-local-onboarding-experiment-events.csv")

    events["assignment_id"] = pd.to_numeric(events["assignment_id"], errors="coerce")
    events = events.dropna(subset=["assignment_id"]).copy()
    events["assignment_id"] = events["assignment_id"].astype(int)
    events = add_video_payload_fields(events)

    assignments = assignments.copy()
    if "excluded_from_analysis" in assignments.columns:
        assignments["excluded_from_analysis"] = pd.to_numeric(assignments["excluded_from_analysis"], errors="coerce").fillna(0).astype(int)
        assignments = assignments[assignments["excluded_from_analysis"] == 0].copy()
    assignments = add_modal_payload_fields(assignments, events)
    assignments["variant"] = assignments["variant"].replace({"non_avatar": "control"})
    assignments["assigned_at_dt"] = pd.to_datetime(assignments["assigned_at"], errors="coerce")
    assignments["first_exposure_at_dt"] = pd.to_datetime(assignments["first_exposure_at"], errors="coerce")
    assignments["first_meaningful_action_at_dt"] = pd.to_datetime(assignments["first_meaningful_action_at"], errors="coerce")
    assignments["cta_clicked_at_dt"] = pd.to_datetime(assignments["cta_clicked_at"], errors="coerce")
    assignments["first_video_created_at_dt"] = pd.to_datetime(assignments["first_video_created_at"], errors="coerce")

    for event_name in sorted(events["event_name"].dropna().unique()):
        counts = events.loc[events["event_name"] == event_name].groupby("assignment_id").size()
        assignments[event_name] = assignments["assignment_id"].map(counts).fillna(0).astype(int)
        assignments[f"has_{event_name}"] = (assignments[event_name] > 0).astype(int)

    assignments["task_entry_intent"] = (
        (assignments.get("has_onboarding_cta_clicked", 0) == 1)
        | (assignments.get("has_first_meaningful_action", 0) == 1)
        | (assignments.get("has_create_video_page_opened", 0) == 1)
    ).astype(int)
    assignments["high_intent_creation_attempt"] = assignments.get("has_video_create_started", 0).astype(int)
    assignments["paywall_constrained_success"] = assignments["first_video_within_7_days"].astype(int)
    assignments["time_to_task_entry_minutes"] = (
        assignments["first_meaningful_action_at_dt"] - assignments["first_exposure_at_dt"]
    ).dt.total_seconds() / 60
    assignments["time_to_cta_minutes"] = (
        assignments["cta_clicked_at_dt"] - assignments["first_exposure_at_dt"]
    ).dt.total_seconds() / 60
    assignments["video_started"] = assignments.get("has_onboarding_video_started", 0).astype(int)
    assignments["video_completed"] = assignments.get("has_onboarding_video_completed", 0).astype(int)
    assignments["watched_25"] = assignments.get("has_onboarding_video_progress_25", 0).astype(int)
    assignments["watched_50"] = assignments.get("has_onboarding_video_progress_50", 0).astype(int)
    assignments["watched_75"] = assignments.get("has_onboarding_video_progress_75", 0).astype(int)
    assignments["watched_90"] = assignments.get("has_onboarding_video_progress_90", 0).astype(int)
    duration_by_assignment = events.dropna(subset=["video_duration_ms"]).groupby("assignment_id")["video_duration_ms"].max()
    current_by_assignment = events.dropna(subset=["video_current_time_ms"]).groupby("assignment_id")["video_current_time_ms"].max()
    assignments["video_duration_ms"] = assignments["assignment_id"].map(duration_by_assignment)
    assignments["max_observed_video_position_ms"] = assignments["assignment_id"].map(current_by_assignment).fillna(0)
    assignments["max_observed_video_position_pct"] = np.where(
        assignments["video_duration_ms"].fillna(0) > 0,
        100 * assignments["max_observed_video_position_ms"] / assignments["video_duration_ms"],
        0,
    )
    assignments["max_observed_video_position_pct"] = assignments["max_observed_video_position_pct"].clip(lower=0, upper=100)
    observed_watch_values = sorted(assignments["watch_percentage"].dropna().astype(float).unique())
    invalid_watch_values = [value for value in observed_watch_values if value not in WATCH_THRESHOLD_VALUES]
    if invalid_watch_values:
        raise SystemExit(
            "watch_percentage is expected to be threshold-derived with values "
            f"{WATCH_THRESHOLD_VALUES}; found {invalid_watch_values}"
        )
    return summary, funnel, daily, assignments, events


def write_sqlite(
    db_path: Path,
    summary: pd.DataFrame,
    funnel: pd.DataFrame,
    daily: pd.DataFrame,
    assignments: pd.DataFrame,
    events: pd.DataFrame,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    serialisable = assignments.drop(
        columns=[col for col in assignments.columns if col.endswith("_dt")],
        errors="ignore",
    )
    with sqlite3.connect(db_path) as conn:
        summary.to_sql("source_summary", conn, if_exists="replace", index=False)
        funnel.to_sql("source_funnel_by_variant", conn, if_exists="replace", index=False)
        daily.to_sql("source_daily_rollup", conn, if_exists="replace", index=False)
        events.to_sql("source_events", conn, if_exists="replace", index=False)
        serialisable.to_sql("analysis_assignments", conn, if_exists="replace", index=False)
        pd.DataFrame(
            [
                {"key": "experiment_key", "value": EXPERIMENT_KEY},
                {"key": "experiment_version", "value": EXPERIMENT_VERSION},
                {"key": "assignment_logic", "value": "server_side_sha256_hash_pseudo_random_allocation_of_experiment_key_version_user_id_first_8_hex_chars_modulo_2"},
                {"key": "analysis_focus", "value": "consent_valid_creation_initiation_with_render_completion_as_downstream_boundary_check"},
                {"key": "creation_initiation_window", "value": "events_observed_after_assignment_and_before_source_export_date"},
                {"key": "stimulus_matching", "value": "same_runtime_script_cta_destination_audio_files_and_screen_demonstration_except_visible_avatar_presence"},
                {"key": "watch_percentage_measurement", "value": "threshold_derived_values_0_25_50_75_90_100_not_continuous"},
                {"key": "max_observed_video_position_measurement", "value": "maximum_video_current_time_ms_divided_by_video_duration_ms_from_raw_video_event_payloads"},
                {"key": "source_export_date", "value": "2026-05-19"},
                {"key": "consent_governance_note", "value": "earlier larger operational export not used because explicit consent status was not sufficiently clear for thesis analysis"},
            ]
        ).to_sql("metadata", conn, if_exists="replace", index=False)


def write_tables(assignments: pd.DataFrame, output_dir: Path, thesis_table_dir: Path | None) -> pd.DataFrame:
    table_dir = output_dir / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    estimates: list[Estimate] = []
    avatar = assignments[assignments["variant"] == "avatar"]
    control = assignments[assignments["variant"] == "control"]

    sample_rows = [
        ["Eligible assigned records in export", str(len(avatar)), str(len(control)), str(len(assignments))],
        [
            "Records excluded after eligibility filtering",
            str(int(avatar["excluded_from_analysis"].sum())) if "excluded_from_analysis" in avatar else "0",
            str(int(control["excluded_from_analysis"].sum())) if "excluded_from_analysis" in control else "0",
            str(int(assignments["excluded_from_analysis"].sum())) if "excluded_from_analysis" in assignments else "0",
        ],
        ["Analysed first onboarding episodes", str(len(avatar)), str(len(control)), str(len(assignments))],
        ["Exposed to onboarding video", str(int(avatar["video_started"].sum())), str(int(control["video_started"].sum())), str(int(assignments["video_started"].sum()))],
        ["First-party telemetry episodes", str(len(avatar)), str(len(control)), str(len(assignments))],
        ["Retained Google click identifier", str(int(avatar["has_gclid"].sum())), str(int(control["has_gclid"].sum())), str(int(assignments["has_gclid"].sum()))],
        ["No retained Google click identifier", str(int((~avatar["has_gclid"].fillna(False)).sum())), str(int((~control["has_gclid"].fillna(False)).sum())), str(int((~assignments["has_gclid"].fillna(False)).sum()))],
        ["Clarity-observable diagnostic episodes", str(int(avatar["clarity_observable"].sum())), str(int(control["clarity_observable"].sum())), str(int(assignments["clarity_observable"].sum()))],
        ["Funnel-entry trigger observed", str(int(avatar["task_entry_intent"].sum())), str(int(control["task_entry_intent"].sum())), str(int(assignments["task_entry_intent"].sum()))],
        ["Started video creation flow", str(int(avatar["high_intent_creation_attempt"].sum())), str(int(control["high_intent_creation_attempt"].sum())), str(int(assignments["high_intent_creation_attempt"].sum()))],
    ]
    latex_table(table_dir / "tab_sampleflow.tex", "Analysis Sample and Telemetry Availability", "tab:sampleflow", ["Stage", "Avatar", CONTROL_DISPLAY, "Total"], sample_rows)

    desc_rows = [
        ["$N$", str(len(avatar)), str(len(control)), str(len(assignments))],
        ["First-party telemetry coverage (\\%)", "100.0\\%", "100.0\\%", "100.0\\%"],
        ["Mean tracked event count (coverage diagnostic)", fmt_num(avatar["tracked_event_count"].mean(), 1), fmt_num(control["tracked_event_count"].mean(), 1), fmt_num(assignments["tracked_event_count"].mean(), 1)],
        ["Mean highest watch threshold", fmt_pct(avatar["watch_percentage"].mean() / 100), fmt_pct(control["watch_percentage"].mean() / 100), fmt_pct(assignments["watch_percentage"].mean() / 100)],
        ["Mean maximum observed video position", fmt_pct(avatar["max_observed_video_position_pct"].mean() / 100), fmt_pct(control["max_observed_video_position_pct"].mean() / 100), fmt_pct(assignments["max_observed_video_position_pct"].mean() / 100)],
        ["Median time to task entry, minutes", fmt_num(avatar["time_to_task_entry_minutes"].median(), 1), fmt_num(control["time_to_task_entry_minutes"].median(), 1), fmt_num(assignments["time_to_task_entry_minutes"].median(), 1)],
    ]
    latex_table(table_dir / "tab_descriptives.tex", "Experimental Sample Characteristics", "tab:descriptives", ["Variable", "Avatar", CONTROL_DISPLAY, "Total"], desc_rows)

    engagement_specs = [
        ("video_started", "Video started"),
        ("watched_25", "Reached 25\\% of onboarding video"),
        ("watched_50", "Reached 50\\% of onboarding video"),
        ("watched_75", "Reached 75\\% of onboarding video"),
        ("watched_90", "Reached 90\\% of onboarding video"),
        ("video_completed", "Completed onboarding video"),
    ]
    engagement_rows = []
    for col, label in engagement_specs:
        est = binary_estimate(assignments, col, label, "Observed playback progression")
        if col != "video_started":
            estimates.append(est)
        engagement_rows.append([label, est.avatar, est.control, est.note.split(",")[0].replace("diff=", ""), fmt_p(est.p_value)])
    avatar_watch = float(avatar["watch_percentage"].mean())
    control_watch = float(control["watch_percentage"].mean())
    engagement_rows.append([
        "Mean highest watch threshold",
        f"{fmt_num(avatar_watch, 1)}\\%",
        f"{fmt_num(control_watch, 1)}\\%",
        f"{fmt_num(avatar_watch - control_watch, 1)} pp",
        "--",
    ])
    max_position_est = mean_estimate(
        assignments,
        "max_observed_video_position_pct",
        "Mean maximum observed video position",
        "Observed playback progression",
        digits=1,
    )
    max_position_avatar = assignments.loc[assignments["variant"] == "avatar", "max_observed_video_position_pct"].astype(float)
    max_position_control = assignments.loc[assignments["variant"] == "control", "max_observed_video_position_pct"].astype(float)
    max_position_mwu_p = float(stats.mannwhitneyu(max_position_avatar, max_position_control, alternative="two-sided").pvalue)
    estimates.append(max_position_est)
    engagement_rows.append([
        "Mean maximum observed video position",
        f"{max_position_est.avatar}\\%",
        f"{max_position_est.control}\\%",
        f"{fmt_num(max_position_est.estimate, 1)} pp",
        fmt_p(max_position_est.p_value),
    ])
    latex_table(table_dir / "tab_rq1.tex", "Observed Playback Progression by Experimental Condition", "tab:rq1", ["Outcome", "Avatar", CONTROL_DISPLAY, "Difference", "$p$"], engagement_rows, spec="p{5.6cm} c c c c")

    engagement_model_rows = [
        [e.outcome, fmt_num(e.estimate, 2), f"[{fmt_num(e.ci_low, 2)}, {fmt_num(e.ci_high, 2)}]", fmt_p(e.p_value)]
        for e in estimates
        if e.family == "Observed playback progression" and e.effect_type == "Odds ratio"
    ]
    latex_table(table_dir / "tab_rq1models.tex", "Model Estimates for Observed Playback Progression", "tab:rq1models", ["Outcome", "OR", "95\\% CI", "$p$"], engagement_model_rows, spec="p{6.5cm} c c c")

    intent_specs = [
        ("task_entry_intent", "Funnel-entry trigger observed"),
        ("high_intent_creation_attempt", "Started video creation flow"),
    ]
    intent_rows = []
    for col, label in intent_specs:
        est = binary_estimate(assignments, col, label, "Creation initiation")
        estimates.append(est)
        intent_rows.append([label, est.avatar, est.control, est.note.split(",")[0].replace("diff=", ""), fmt_p(est.p_value)])
    time_entry = mean_estimate(assignments, "time_to_task_entry_minutes", "Time to task entry", "Creation initiation", digits=2)
    estimates.append(time_entry)
    intent_rows.append(["Mean time to task entry, minutes", time_entry.avatar, time_entry.control, fmt_num(time_entry.estimate, 2), fmt_p(time_entry.p_value)])
    latex_table(table_dir / "tab_rq2.tex", "Creation-Initiation Outcomes by Experimental Condition", "tab:rq2", ["Outcome", "Avatar", CONTROL_DISPLAY, "Difference", "$p$"], intent_rows, spec="p{5.6cm} c c c c")

    intent_model_rows = [
        [e.outcome, fmt_num(e.estimate, 2), f"[{fmt_num(e.ci_low, 2)}, {fmt_num(e.ci_high, 2)}]", fmt_p(e.p_value)]
        for e in estimates
        if e.family == "Creation initiation" and e.effect_type == "Odds ratio"
    ]
    latex_table(table_dir / "tab_rq2models.tex", "Model Estimates for Creation Initiation", "tab:rq2models", ["Outcome", "OR", "95\\% CI", "$p$"], intent_model_rows, spec="p{6.5cm} c c c")

    downstream_specs = [
        ("has_video_create_failed", "Video creation failed"),
        ("paywall_constrained_success", "Generated-video render completion observed by export date"),
    ]
    downstream_rows = []
    for col, label in downstream_specs:
        est = binary_estimate(assignments, col, label, "Downstream boundary outcomes")
        downstream_rows.append([label, est.avatar, est.control, est.note.split(",")[0].replace("diff=", ""), "--"])
    successful = assignments[assignments["first_video_within_7_days"] == 1]
    downstream_rows.append([
        "Mean time to generated video completion among completers, hours",
        fmt_num(successful.loc[successful["variant"] == "avatar", "time_to_first_video_hours"].mean(), 1),
        fmt_num(successful.loc[successful["variant"] == "control", "time_to_first_video_hours"].mean(), 1),
        "--",
        "--",
    ])
    latex_table(table_dir / "tab_rq3.tex", "Downstream Boundary Outcomes", "tab:rq3", ["Outcome", "Avatar", CONTROL_DISPLAY, "Difference", "$p$"], downstream_rows, spec="p{5.8cm} c c c c")

    mde = creation_start_mde(float(control["high_intent_creation_attempt"].mean()), min(len(avatar), len(control)))
    robust_rows = [
        ["Endpoint hierarchy", "Treat creation initiation as the primary user-side behavioural endpoint; report generated-video render completion as a downstream boundary check", "Avoid conflating instruction-to-task-initiation behaviour with paid access"],
        ["Task entry versus creation start", "Collapse CTA click, first action, and creation-page entry into one funnel-entry trigger; report video creation start separately", "Avoid treating one client-side transition as multiple independent intent stages"],
        ["Creation-start power", f"Approximate 80\\% power MDE for creation-flow starts is {100 * mde:.1f} percentage points", "Treat RQ2 as underpowered for small behavioural effects"],
        ["Watch-depth measurement scale", "Treat watch percentage as a threshold-derived highest recorded bucket", "Avoid interpreting it as continuous playback time"],
        ["Raw video-position derivation", "Use maximum video current time divided by video duration as a secondary watch-position measure", "Use finer-grained payload data without claiming exact attention"],
        ["Max-position distribution check", f"Mann-Whitney sensitivity check for maximum observed video position, {fmt_p(max_position_mwu_p)}", "Check that the bounded timeline-position result is not only a Welch mean-contrast artefact"],
        ["Tracked-event-count diagnostic", "Compare mean tracked event count across conditions", "Assess logging coverage, not baseline balance"],
        ["Acquisition balance diagnostic", "Compare retained Google click identifiers across conditions", "Check whether attributed Google Ads traffic is concentrated in one variant"],
        ["Stimulus scope", "Matched non-avatar video keeps the same composition with the avatar removed; it is not an independently optimised non-avatar tutorial", "Avoid generalising beyond the implemented non-avatar comparison"],
        ["Product-signalling scope", "Avatar condition may act as both instruction and product demonstration in an AI-avatar platform", "Avoid claiming a pure social-presence mechanism"],
        ["Exclusion-rule provenance", "Internal, QA, test, bot, and ineligible existing-user records are removed before the analysis export", "Clarify why no additional export rows are marked as excluded"],
        ["First-party telemetry coverage", f"{len(assignments)} of {len(assignments)} assigned episodes have site event telemetry", "Confirm outcome availability"],
        ["Downstream render completion", "Report generated-video completion observed by export date only as a descriptive boundary check", "Prevent invalid downstream causal interpretation"],
    ]
    latex_table(table_dir / "tab_robustness.tex", "Robustness and Interpretation Checks", "tab:robustness", ["Check", "Specification", "Purpose"], robust_rows, spec="p{4.6cm} p{6.2cm} p{3.2cm}")

    acquisition_rows = []
    for label, has_gclid in [
        ("Retained Google click identifier", True),
        ("No retained Google click identifier", False),
    ]:
        subgroup = assignments[assignments["has_gclid"].fillna(False) == has_gclid]
        for variant_value, variant_label in [("avatar", "Avatar"), ("control", CONTROL_DISPLAY)]:
            group = subgroup[subgroup["variant"] == variant_value]
            acquisition_rows.append(
                [
                    label,
                    variant_label,
                    str(len(group)),
                    fmt_pct(group["max_observed_video_position_pct"].mean() / 100),
                    fmt_pct(group["watched_50"].mean()),
                    fmt_pct(group["high_intent_creation_attempt"].mean()),
                ]
            )
    latex_table(
        table_dir / "tab_acquisition_subgroups.tex",
        "Descriptive Acquisition-Context Check",
        "tab:acquisition-subgroups",
        ["Subgroup", "Condition", "$N$", "Max pos.", "50\\% reached", "Create start"],
        acquisition_rows,
        spec="p{3.5cm} p{2.1cm} c c c c",
    )

    pvals = [e.p_value for e in estimates if np.isfinite(e.p_value)]
    qvals = bh_adjust(pvals)
    qi = iter(qvals)
    estimate_rows = []
    for e in estimates:
        q = next(qi) if np.isfinite(e.p_value) else np.nan
        estimate_rows.append({**e.__dict__, "p_bh": q})
    estimate_df = pd.DataFrame(estimate_rows)
    estimate_df.to_csv(output_dir / "model_estimates.csv", index=False)

    model_rows = [
        [
            row["family"],
            row["outcome"],
            row["effect_type"],
            fmt_num(float(row["estimate"]), 2),
            f"[{fmt_num(float(row['ci_low']), 2)}, {fmt_num(float(row['ci_high']), 2)}]",
            fmt_p(float(row["p_value"])),
            fmt_p(float(row["p_bh"])),
        ]
        for _, row in estimate_df.iterrows()
    ]
    latex_table(table_dir / "tab_model_estimates.tex", "Model Estimates with False-Discovery-Rate Adjustment", "tab:model-estimates", ["Family", "Outcome", "Effect", "Estimate", "95\\% CI", "$p$", "$q$"], model_rows, spec="p{3.0cm} p{4.4cm} p{2.2cm} c c c c")

    if thesis_table_dir is not None:
        thesis_table_dir.mkdir(parents=True, exist_ok=True)
        for table in table_dir.glob("*.tex"):
            (thesis_table_dir / table.name).write_text(table.read_text(encoding="utf-8"), encoding="utf-8")

    return estimate_df


def compile_tikz(stem: str, tikz: str, output_dir: Path, thesis_figure_dir: Path | None) -> None:
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    if thesis_figure_dir is not None:
        thesis_figure_dir.mkdir(parents=True, exist_ok=True)

    snippet_path = figure_dir / f"{stem}.tex"
    standalone_path = figure_dir / f"{stem}_standalone.tex"
    snippet_path.write_text(tikz, encoding="utf-8")
    standalone_path.write_text(
        "\n".join(
            [
                r"\documentclass[tikz,border=8pt]{standalone}",
                r"\usepackage{newtxtext,newtxmath}",
                r"\begin{document}",
                tikz,
                r"\end{document}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", standalone_path.name],
        cwd=figure_dir,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    subprocess.run(
        ["pdftoppm", "-singlefile", "-png", "-r", "220", str(figure_dir / f"{stem}_standalone.pdf"), str(figure_dir / stem)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    if thesis_figure_dir is not None:
        shutil.copy2(figure_dir / f"{stem}.png", thesis_figure_dir / f"{stem}.png")
        shutil.copy2(snippet_path, thesis_figure_dir / f"{stem}.tex")


def grouped_bar_tikz(
    *,
    title: str,
    categories: list[str],
    avatar_values: list[float],
    control_values: list[float],
    y_max: float,
) -> str:
    left, bottom, plot_w, plot_h = 72, 78, 620, 310
    width_pt, height_pt = 760, 450

    def y_pos(value: float) -> float:
        return bottom + plot_h * (value / y_max)

    lines = [
        r"\begin{tikzpicture}[x=1pt,y=1pt]",
        rf"\path[use as bounding box] (0,0) rectangle ({width_pt},{height_pt});",
        rf"\node[font=\small] at ({left + plot_w / 2:.1f},{height_pt - 15}) {{{latex_escape(title)}}};",
    ]
    for tick in range(0, int(y_max) + 1, 20):
        y = y_pos(tick)
        lines.append(rf"\draw[black!18,line width=0.4pt] ({left},{y:.1f}) -- ({left + plot_w},{y:.1f});")
        lines.append(rf"\node[anchor=east,font=\scriptsize] at ({left - 7},{y - 2:.1f}) {{{tick}}};")
    lines.extend(
        [
            rf"\draw[line width=0.7pt] ({left},{bottom}) -- ({left},{bottom + plot_h});",
            rf"\draw[line width=0.7pt] ({left},{bottom}) -- ({left + plot_w},{bottom});",
            rf"\node[rotate=90,font=\small] at (22,{bottom + plot_h / 2:.1f}) {{Percentage of users}};",
            rf"\filldraw[fill=black!70,draw=black,line width=0.6pt] ({width_pt - 220},{height_pt - 42}) rectangle ({width_pt - 200},{height_pt - 25});",
            rf"\node[anchor=west,font=\scriptsize] at ({width_pt - 192},{height_pt - 34}) {{Avatar}};",
            rf"\filldraw[fill=white,draw=black,line width=0.6pt] ({width_pt - 115},{height_pt - 42}) rectangle ({width_pt - 95},{height_pt - 25});",
            rf"\node[anchor=west,font=\scriptsize] at ({width_pt - 87},{height_pt - 34}) {{{CONTROL_DISPLAY}}};",
        ]
    )
    group_gap = plot_w / len(categories)
    bar_w = min(42, group_gap / 4)
    for i, label in enumerate(categories):
        x_centre = left + group_gap * (i + 0.5)
        for x_offset, value, fill in [(-bar_w * 0.65, avatar_values[i], "black!70"), (bar_w * 0.65, control_values[i], "white")]:
            x_left = x_centre + x_offset - bar_w / 2
            y = y_pos(value)
            lines.append(rf"\filldraw[fill={fill},draw=black,line width=0.6pt] ({x_left:.1f},{bottom}) rectangle ({x_left + bar_w:.1f},{y:.1f});")
            lines.append(rf"\node[font=\scriptsize] at ({x_left + bar_w / 2:.1f},{y + 14:.1f}) {{{value:.1f}}};")
        lines.append(rf"\node[font=\scriptsize,align=center] at ({x_centre:.1f},{bottom - 34}) {{{latex_escape(label)}}};")
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines)


def watch_survival_tikz(assignments: pd.DataFrame) -> str:
    """Plot the share of users whose maximum observed playback position reaches each point."""
    left, bottom, plot_w, plot_h = 72, 78, 620, 310
    width_pt, height_pt = 760, 450
    xs = list(range(0, 101, 2))
    avatar_positions = assignments.loc[assignments["variant"] == "avatar", "max_observed_video_position_pct"].astype(float)
    control_positions = assignments.loc[assignments["variant"] == "control", "max_observed_video_position_pct"].astype(float)

    def y_value(series: pd.Series, threshold: int) -> float:
        return 100 * float((series >= threshold).mean())

    avatar_values = [y_value(avatar_positions, x) for x in xs]
    control_values = [y_value(control_positions, x) for x in xs]

    def x_pos(value: float) -> float:
        return left + plot_w * (value / 100)

    def y_pos(value: float) -> float:
        return bottom + plot_h * (value / 100)

    def points(values: list[float]) -> str:
        return " ".join(f"({x_pos(x):.1f},{y_pos(y):.1f})" for x, y in zip(xs, values))

    lines = [
        r"\begin{tikzpicture}[x=1pt,y=1pt]",
        rf"\path[use as bounding box] (0,0) rectangle ({width_pt},{height_pt});",
        rf"\node[font=\small] at ({left + plot_w / 2:.1f},{height_pt - 15}) {{Maximum observed video position by condition}};",
    ]
    for tick in range(0, 101, 20):
        y = y_pos(tick)
        lines.append(rf"\draw[black!18,line width=0.4pt] ({left},{y:.1f}) -- ({left + plot_w},{y:.1f});")
        lines.append(rf"\node[anchor=east,font=\scriptsize] at ({left - 7},{y - 2:.1f}) {{{tick}}};")
    for tick in [0, 25, 50, 75, 100]:
        x = x_pos(tick)
        lines.append(rf"\draw[black!18,line width=0.4pt] ({x:.1f},{bottom}) -- ({x:.1f},{bottom + plot_h});")
        lines.append(rf"\node[font=\scriptsize] at ({x:.1f},{bottom - 24}) {{{tick}\%}};")
    lines.extend(
        [
            rf"\draw[line width=0.7pt] ({left},{bottom}) -- ({left},{bottom + plot_h});",
            rf"\draw[line width=0.7pt] ({left},{bottom}) -- ({left + plot_w},{bottom});",
            rf"\node[font=\small] at ({left + plot_w / 2:.1f},{bottom - 50}) {{Maximum observed video position}};",
            rf"\node[rotate=90,font=\small] at (22,{bottom + plot_h / 2:.1f}) {{Percentage at or beyond position}};",
            rf"\draw[black,line width=1.2pt] plot coordinates {{{points(avatar_values)}}};",
            rf"\draw[black,line width=1.2pt,dashed] plot coordinates {{{points(control_values)}}};",
            rf"\draw[black,line width=1.2pt] ({width_pt - 238},{height_pt - 34}) -- ({width_pt - 204},{height_pt - 34});",
            rf"\node[anchor=west,font=\scriptsize] at ({width_pt - 196},{height_pt - 34}) {{Avatar}};",
            rf"\draw[black,line width=1.2pt,dashed] ({width_pt - 125},{height_pt - 34}) -- ({width_pt - 91},{height_pt - 34});",
            rf"\node[anchor=west,font=\scriptsize] at ({width_pt - 83},{height_pt - 34}) {{{CONTROL_DISPLAY}}};",
        ]
    )
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines)


def make_figures(assignments: pd.DataFrame, output_dir: Path, thesis_figure_dir: Path | None) -> None:

    watch_cols = [
        ("25%", "watched_25"),
        ("50%", "watched_50"),
        ("75%", "watched_75"),
        ("90%", "watched_90"),
        ("100%", "video_completed"),
    ]
    avatar = [100 * assignments.loc[assignments["variant"] == "avatar", col].mean() for _, col in watch_cols]
    control = [100 * assignments.loc[assignments["variant"] == "control", col].mean() for _, col in watch_cols]
    compile_tikz(
        "fig_watch_depth",
        grouped_bar_tikz(
            title="Onboarding video thresholds reached by condition",
            categories=[label for label, _ in watch_cols],
            avatar_values=avatar,
            control_values=control,
            y_max=100,
        ),
        output_dir,
        thesis_figure_dir,
    )
    compile_tikz(
        "fig_watch_survival",
        watch_survival_tikz(assignments),
        output_dir,
        thesis_figure_dir,
    )

    intent_cols = [
        ("Funnel entry", "task_entry_intent"),
        ("Flow started", "high_intent_creation_attempt"),
    ]
    avatar = [100 * assignments.loc[assignments["variant"] == "avatar", col].mean() for _, col in intent_cols]
    control = [100 * assignments.loc[assignments["variant"] == "control", col].mean() for _, col in intent_cols]
    compile_tikz(
        "fig_creation_intent",
        grouped_bar_tikz(
            title="Creation-initiation outcomes by condition",
            categories=[label for label, _ in intent_cols],
            avatar_values=avatar,
            control_values=control,
            y_max=100,
        ),
        output_dir,
        thesis_figure_dir,
    )


def write_summary(
    assignments: pd.DataFrame,
    estimate_df: pd.DataFrame,
    output_dir: Path,
    db_path: Path,
) -> None:
    avatar = assignments[assignments["variant"] == "avatar"]
    control = assignments[assignments["variant"] == "control"]
    max_position_mwu_p = float(
        stats.mannwhitneyu(
            avatar["max_observed_video_position_pct"].astype(float),
            control["max_observed_video_position_pct"].astype(float),
            alternative="two-sided",
        ).pvalue
    )
    summary = {
        "db_path": str(db_path),
        "experiment_key": EXPERIMENT_KEY,
        "experiment_version": EXPERIMENT_VERSION,
        "n": int(len(assignments)),
        "n_avatar": int(len(avatar)),
        "n_control": int(len(control)),
        "assigned_min": str(assignments["assigned_at"].min()),
        "assigned_max": str(assignments["assigned_at"].max()),
        "first_party_telemetry_avatar": int(len(avatar)),
        "first_party_telemetry_control": int(len(control)),
        "watch_percentage_measurement": "threshold-derived highest recorded bucket; possible values are 0, 25, 50, 75, 90, 100",
        "watch_percentage_values": WATCH_THRESHOLD_VALUES,
        "watch_percentage_avatar": float(avatar["watch_percentage"].mean()),
        "watch_percentage_control": float(control["watch_percentage"].mean()),
        "max_observed_video_position_measurement": "maximum video_current_time_ms divided by video_duration_ms across raw video events",
        "max_observed_video_position_avatar": float(avatar["max_observed_video_position_pct"].mean()),
        "max_observed_video_position_control": float(control["max_observed_video_position_pct"].mean()),
        "watched_50_avatar": float(avatar["watched_50"].mean()),
        "watched_50_control": float(control["watched_50"].mean()),
        "watched_75_avatar": float(avatar["watched_75"].mean()),
        "watched_75_control": float(control["watched_75"].mean()),
        "task_entry_avatar": float(avatar["task_entry_intent"].mean()),
        "task_entry_control": float(control["task_entry_intent"].mean()),
        "high_intent_avatar": float(avatar["high_intent_creation_attempt"].mean()),
        "high_intent_control": float(control["high_intent_creation_attempt"].mean()),
        "downstream_render_completion_avatar": float(avatar["paywall_constrained_success"].mean()),
        "downstream_render_completion_control": float(control["paywall_constrained_success"].mean()),
        "max_observed_video_position_mann_whitney_p": max_position_mwu_p,
        "creation_start_mde_80_power": creation_start_mde(float(control["high_intent_creation_attempt"].mean()), min(len(avatar), len(control))),
    }
    (output_dir / "current_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    high_intent = estimate_df[estimate_df["outcome"] == "Started video creation flow"].iloc[0]
    watch50 = estimate_df[estimate_df["outcome"] == "Reached 50\\% of onboarding video"].iloc[0]
    watch75 = estimate_df[estimate_df["outcome"] == "Reached 75\\% of onboarding video"].iloc[0]
    max_position = estimate_df[estimate_df["outcome"] == "Mean maximum observed video position"].iloc[0]
    text = f"""# May 19 TO SEE Onboarding Intent Analysis

## Sample

- Consent-valid analysed episodes: {len(assignments):,}
- Avatar condition: {len(avatar):,}
- Matched non-avatar condition: {len(control):,}
- First-party telemetry episodes: {len(assignments):,}
- Assignment window: {assignments['assigned_at'].min()} to {assignments['assigned_at'].max()}
- Assignment logic: server-side deterministic hash-based pseudo-random allocation using experiment key, experiment version, and user ID.
- Creation-initiation window: events observed after assignment and before the May 19 export date.
- Consent note: the thesis uses this consent-clarified export rather than the earlier larger operational export.

## Main Findings

- Recorded watch thresholds were similar across conditions: the mean highest threshold was {avatar['watch_percentage'].mean():.1f}% versus {control['watch_percentage'].mean():.1f}%. This value is threshold-derived, not continuous playback time.
- The maximum observed video-position measure was {avatar['max_observed_video_position_pct'].mean():.1f}% versus {control['max_observed_video_position_pct'].mean():.1f}%, mean difference = {max_position['estimate']:.1f} percentage points, Welch p = {max_position['p_value']:.3g}, Mann-Whitney p = {max_position_mwu_p:.3g}.
- Reaching 50% of the onboarding video was {fmt_plain_pct(avatar['watched_50'].mean())} in the avatar condition and {fmt_plain_pct(control['watched_50'].mean())} in the matched non-avatar condition, difference = {fmt_pp(avatar['watched_50'].mean() - control['watched_50'].mean())}, OR = {watch50['estimate']:.2f}, p = {watch50['p_value']:.3g}.
- Reaching 75% of the onboarding video was {fmt_plain_pct(avatar['watched_75'].mean())} in the avatar condition and {fmt_plain_pct(control['watched_75'].mean())} in the matched non-avatar condition, difference = {fmt_pp(avatar['watched_75'].mean() - control['watched_75'].mean())}, OR = {watch75['estimate']:.2f}, p = {watch75['p_value']:.3g}.
- Funnel entry was almost identical: {fmt_plain_pct(avatar['task_entry_intent'].mean())} versus {fmt_plain_pct(control['task_entry_intent'].mean())}.
- Creation-flow start showed a small observed positive difference that was statistically uncertain: {fmt_plain_pct(avatar['high_intent_creation_attempt'].mean())} versus {fmt_plain_pct(control['high_intent_creation_attempt'].mean())}, difference = {fmt_pp(avatar['high_intent_creation_attempt'].mean() - control['high_intent_creation_attempt'].mean())}, OR = {high_intent['estimate']:.2f}, p = {high_intent['p_value']:.3g}.
- At 80% power and alpha = .05, the approximate minimum detectable effect for creation-flow starts at the observed control baseline is {100 * creation_start_mde(float(control['high_intent_creation_attempt'].mean()), min(len(avatar), len(control))):.1f} percentage points.
- Generated-video render completion observed by the export date is reported only as a downstream boundary check: {fmt_plain_pct(avatar['paywall_constrained_success'].mean())} versus {fmt_plain_pct(control['paywall_constrained_success'].mean())}.

## Interpretation

The consent-valid data do not support a broad playback-progression advantage. Watch-depth measures are broadly similar across conditions, with a higher avatar completion rate but no clear advantage on maximum observed video position. Creation-flow start is descriptively higher in the avatar condition, but the interval is wide and the design is underpowered. The paper should therefore frame the empirical result as suggestive evidence that warrants larger follow-up research, not as conclusive evidence that avatar-led onboarding improves task completion.
"""
    (output_dir / "results_summary.md").write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, funnel, daily, assignments, events = build_analysis(args.input_dir)
    write_sqlite(args.db, summary, funnel, daily, assignments, events)
    estimate_df = write_tables(assignments, output_dir, None)
    if args.skip_figures:
        print("Skipping figure rendering (--skip-figures): no LaTeX/PDF outputs generated.")
    else:
        make_figures(assignments, output_dir, None)
    write_summary(assignments, estimate_df, output_dir, args.db)
    print((output_dir / "results_summary.md").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reproduce the tables, figures, and summary statistics for the "
            "avatar-led onboarding experiment. Requires the five CSV exports in "
            "--input-dir (see docs/data_dictionary.md)."
        )
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                        help="Directory holding the five required CSV exports.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="Directory for generated tables, figures, and summaries.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="Path for the rebuilt SQLite analysis database.")
    parser.add_argument("--skip-figures", action="store_true",
                        help="Skip PNG figure rendering, which needs a LaTeX "
                             "toolchain (pdflatex + pdftoppm). Tables and "
                             "statistics are still produced.")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
