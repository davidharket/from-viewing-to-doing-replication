# From Viewing to Doing — Replication Code

Replication code for:

> Harket, D. (2026). *From Viewing to Doing? A Randomised Evaluation of
> Avatar-Led Onboarding in an AI Video Platform.* MSc thesis, Social Data
> Science, University of Copenhagen.

This repository contains the analysis code that produces the tables, figures, and
summary statistics reported in the thesis. It is the **code only**: the
underlying telemetry is pseudonymised personal data under the GDPR and is not
distributed here (see [Data and GDPR](#data-and-gdpr)).

A small, fully synthetic sample is bundled so reviewers can run the entire
pipeline end-to-end without access to the real data.

## What the study did

A live, randomised field experiment in the onboarding flow of an AI video
platform. New users were allocated server-side to one of two conditions:

- **Avatar** — onboarding video presented by a visible synthetic presenter.
- **Control (non-avatar)** — the *same* video with the avatar removed: identical
  script, audio, screen demonstration, runtime, call to action, and destination.

First-party telemetry measured viewing behaviour (watch-depth thresholds and the
maximum observed playback position) and **creation initiation** (starting the
video-creation flow). Generated-video completion is reported only as a downstream,
paywall-constrained boundary check. The headline result is a separation between
*viewing* and *doing*: avatar onboarding is associated with more viewing, but the
evidence for increased task initiation is weak and the design is underpowered.

### Study snapshot (real data)

| | |
| --- | --- |
| Consent-valid analysed episodes | 164 (80 avatar, 84 control) |
| Assignment window | 2026-05-06 to 2026-05-19 |
| Primary endpoint | creation-flow start (`video_create_started`) |
| Approx. 80%-power MDE for creation start | ~18.9 percentage points |

These numbers are reproduced by the code in this repository when run against the
real export; they are quoted here only for orientation.

## Repository layout

```
analysis/run_analysis.py            # the full analysis pipeline (one file)
data/README.md                      # how to supply real data; GDPR rationale
data/synthetic_sample/              # random, non-personal sample + its generator
docs/data_dictionary.md            # schema of the five required CSV exports
requirements.txt
LICENSE                             # MIT
```

Outputs are written to `outputs/` (git-ignored): LaTeX tables, PNG figures,
`model_estimates.csv`, `results_summary.md`, `current_results.json`, and a rebuilt
SQLite database.

## Quick start

```bash
git clone https://github.com/davidharket/from-viewing-to-doing-replication.git
cd from-viewing-to-doing-replication
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the whole pipeline on the bundled synthetic sample (no LaTeX needed):
python analysis/run_analysis.py --input-dir data/synthetic_sample --skip-figures
```

This prints a results summary and writes tables and statistics to `outputs/`.
**The synthetic numbers are meaningless** — they only demonstrate that the code
runs. To reproduce the thesis results, supply the real data (below).

### Reproducing the thesis results

1. Obtain the five CSV exports (see [Data and GDPR](#data-and-gdpr)) and place
   them in `data/`.
2. Run:

   ```bash
   python analysis/run_analysis.py            # add --skip-figures to skip PNGs
   ```

3. The generated `outputs/tables/*.tex` and `outputs/model_estimates.csv`
   correspond to the tables in the thesis.

### Figures (optional)

By default the script also renders PNG figures, which requires a system LaTeX
toolchain providing `pdflatex` and `pdftoppm` (e.g. TeX Live + Poppler). If you
do not have these, pass `--skip-figures`; all tables and statistics are still
produced.

## Data and GDPR

The real dataset is **not** included and will not be published. It is first-party
behavioural telemetry that, although pseudonymised, is personal data under the
GDPR; participants consented to the platform's privacy policy for service
operation, not to public release of event-level records. Releasing the analysis
code without the data is a deliberate balance between reproducibility and data
protection.

See [`data/README.md`](data/README.md) for the required filenames and
[`docs/data_dictionary.md`](docs/data_dictionary.md) for the full schema (column
names and types only). Access to the underlying data for verification can be
requested from the author under a controlled-access agreement.

## Environment

Tested with Python 3.9.6, pandas 2.3.3, numpy 2.0.2, scipy 1.13.1. See
`requirements.txt` for version ranges.

## How to cite

If you use this code, please cite the thesis and this repository:

> Harket, D. (2026). *Replication code for "From Viewing to Doing? A Randomised
> Evaluation of Avatar-Led Onboarding in an AI Video Platform"* (v1.0).
> GitHub. https://github.com/davidharket/from-viewing-to-doing-replication

See [`CITATION.cff`](CITATION.cff) for machine-readable metadata.

## License

MIT — see [`LICENSE`](LICENSE).
