"""Evaluation suite for Azmuth.

Usage::

    python -m app.evals                 # all categories, offline
    python -m app.evals --fast          # first 15 cases only
    python -m app.evals --category chat # one category
    python -m app.evals --threshold 0.9 # fail if pass rate < 90%
    python -m app.evals --save-baseline # write results/baseline.json

Cases live in ``app/evals/cases/*.yaml``. Each file is one category.
See ``cases/_template.yaml`` for the schema.
"""

__version__ = "0.1.0"