# Prompt changelog

What changed between prompt versions in `prompts/extraction/` and why. Newest first.

## post_balance_sheet_event v2 — 2026-10-06 (plan 0013 decision 9)

From the review of the first model run. Two changes, in `extractor_v4`:
- **The kind of event:** a present answer also gives `adverse`, `favourable` or `neutral`, the most adverse
  when a page states several. v1 counted a COVID subsidy, new contracts and the 2019–2020 COVID paragraph
  the same as a petition, which is noisy for a distress feature; the kind keeps them apart, and a wrong kind
  is scored as an error.
- **The balance-sheet date:** the request carries the statement's balance-sheet date, and the prompt says that
  text referring to an earlier year's statement is carried forward, not this year's event. v1's one real
  error was a FY2023 statement repeating a 2020 COVID paragraph (`2fa1cf17360c2674`).
- A section heading alone is no longer accepted as evidence.

The definition of "present" is v1's, from the labelling guide.

## v1 — 2026-09-30 (plan 0013 step F)

First prompts, one per `signal_type` the model reads: `going_concern_uncertainty`, `emphasis_of_matter`,
`covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event`, `loss_coverage_resolution`,
`continued_existence_vote`. `opinion_type` has no prompt: its wording is standard and a lemma rule reads it
(`config/extraction/rules_v1.yaml`, decision 2). Each prompt carries its signal's definition from
`evals/text_signals/labelling_guide.md` (accepted 2026-09-30), so the model and the labeller answer the same
question. Not yet scored: no call has been made (decision 2: the owner confirms the provider's terms first).
