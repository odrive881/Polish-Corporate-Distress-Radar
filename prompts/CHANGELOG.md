# Prompt changelog

What changed between prompt versions in `prompts/extraction/` and why. Newest first.

## v1 — 2026-09-30 (plan 0013 step F)

First prompts, one per `signal_type` the model reads: `going_concern_uncertainty`, `emphasis_of_matter`,
`covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event`, `loss_coverage_resolution`,
`continued_existence_vote`. `opinion_type` has no prompt: its wording is standard and a lemma rule reads it
(`config/extraction/rules_v1.yaml`, decision 2). Each prompt carries its signal's definition from
`evals/text_signals/labelling_guide.md` (accepted 2026-09-30), so the model and the labeller answer the same
question. Not yet scored: no call has been made (decision 2: the owner confirms the provider's terms first).
