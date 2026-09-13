# 0002 — Build the verification harness before the pipeline

- **Status:** accepted
- **Date:** 2026-09-13

## Context

Most bugs in this project won't crash. The code runs cleanly and is still wrong, for example:

- A point-in-time join uses fiscal year-end instead of the RDF submission date.
- An XML element is mapped to the wrong canonical line item.
- Censored companies are counted as survivors.

Each of these still produces tables, passes naive tests, and yields a model with a good-looking AUC. A coding agent stops when the work *looks* done; without something runnable that proves correctness, the human reviewer is the only check across twelve domain-heavy stages.

## Decision

The first milestone is the verification harness, not pipeline code:

1. **One-command tooling** the agent can run: lint, type check, tests, CI (`make check`).
2. **A small fixture dataset:** real RDF XML files (simplified and full structures), an auditor report, a KRS extract, and recorded API responses. Personal data sanitised; no dependency on live portals.
3. **Golden files** with expected outputs for each stage.
4. **Leakage tests and accounting identity checks** that exist before the code they check.
5. **Tamper protection:** any change to tests, golden files, thresholds, or label definitions requires explicit human review.

## Consequences

- The agent can do the work, run the checks, read the result, and iterate on its own.
- Deciding what counts as correct — point-in-time correctness, bitemporality, censoring, out-of-time validation — stays a human responsibility; the harness encodes those judgements, it doesn't replace them.
