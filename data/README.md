# Data

**The real experiment data is not in this repository, and will not be.**

The analysis runs on first-party behavioural telemetry from a live onboarding
experiment. Even though it is pseudonymised, it remains **personal data under the
GDPR** (it concerns identifiable natural persons via stable pseudonymous keys and
acquisition metadata). Participants consented to the platform's privacy policy
for the operation of the service, not to public redistribution of their
event-level records. Publishing it would also be commercially and contractually
inappropriate. So this repository releases the **analysis code only**.

## Running on the real data

Place the five CSV exports directly in this `data/` directory (they are
git-ignored so they can never be committed), then:

```bash
python analysis/run_analysis.py --skip-figures
```

Required filenames (schema in [`../docs/data_dictionary.md`](../docs/data_dictionary.md)):

- `tosee-onboarding-experiment-clarity-observable.csv` (primary per-episode table)
- `tosee-local-onboarding-experiment-events.csv` (event log)
- `tosee-onboarding-experiment-summary.csv`
- `tosee-local-onboarding-experiment-funnel-by-variant.csv`
- `tosee-local-onboarding-experiment-daily-rollup.csv`

Access to the underlying data for verification can be requested from the author
under a controlled-access agreement, subject to GDPR constraints.

## Running on the synthetic sample

`synthetic_sample/` contains a small, fully random, non-personal dataset with the
same schema, so you can exercise the whole pipeline immediately:

```bash
python analysis/run_analysis.py --input-dir data/synthetic_sample --skip-figures
```

Regenerate it at any time with:

```bash
python data/synthetic_sample/make_synthetic_sample.py
```

**The numbers produced from the synthetic sample are meaningless** — they exist
only to prove the code runs. They are not the results reported in the thesis.
