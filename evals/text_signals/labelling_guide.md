# Labelling guide — text signals (plan 0013 step E)

**Status: draft, for the owner to confirm or change before the first page is labelled.** A change after labelling
has begun means relabelling what was done under the old wording, so the definitions are settled first.

Labels in this directory are the owner's (plan 0013 decision 5). A model may propose; the owner confirms or
corrects every proposal, and the row records who proposed (`proposed_by`) and who labelled (`labelled_by`).

## How a page is labelled

- **Every page, every signal.** Each queued page gets a label for each of the nine `signal_type`s, present or
  absent. The prefilter's yellow highlights are hints: a signal can be present on a page it did not select, and
  that is exactly what the rejected sample is there to find.
- **Present means the page states it, about this company.** Not a policy template, not a hypothetical, not
  another company. A standard sentence that the company is *not* threatened is absent.
- **Evidence is a verbatim span of the masked page**, the shortest that shows the signal on its own: usually one
  sentence. Copy it from the page; the tool refuses a span that is not on it. Tokens such as `[osoba]` stay in it.
- **When unsure, absent**, and note the page id: a doubtful positive teaches the extractor more noise than a
  missed one costs here.
- **Mask first, then label.** A person's name the masker missed (shown in red if the masker itself would still
  catch it, but look for others) is masked with "Mask on this page" before labelling, so evidence never holds it.
  A company name stays. Over-masking is accepted.

## The signals

| `signal_type` | Present when the page says | Not present |
|---|---|---|
| `going_concern_uncertainty` | the company's ability to continue is threatened or uncertain, or the statements are not prepared on the going-concern basis; bankruptcy, restructuring or liquidation of the company pending or open | the policy sentence "prepared on the going-concern assumption" with no threat stated; a statement that no threat exists |
| `opinion_type` | an auditor's opinion on these statements; value: `unqualified` (*bez zastrzeżeń*), `qualified` (*z zastrzeżeniem*), `adverse` (*negatywna*), `disclaimer` (*odmowa wyrażenia opinii*) | a mention that the statements are or will be audited |
| `emphasis_of_matter` | the auditor draws attention to a matter without modifying the opinion (*zwrócenie uwagi*, *objaśnienie*) | the opinion itself |
| `covenant_breach` | a loan or bond covenant or condition was breached, a lender terminated a facility or called it due | loan terms described without a breach |
| `key_customer_loss` | a major customer or contract was lost, withdrawn from or terminated (*odstąpienie od umowy* by the investor) | customer concentration described without a loss |
| `litigation` | a court, arbitration or enforcement case involving the company, pending or decided, with a claim | routine receivables collection with no case |
| `post_balance_sheet_event` | an event after the balance-sheet date that bears on the company's condition | the standard sentence that no such event occurred |
| `loss_coverage_resolution` | how a loss is (to be) covered: from future profits, reserve capital, shareholders' payments | profit distribution with no loss |
| `continued_existence_vote` | the shareholders' vote on whether the company continues (KSH art. 233), called or held | the loss exceeding half the capital, stated without the vote |

## Masking

`[osoba]` a person, `[pesel]` a PESEL, `[email]` an e-mail address, `[telefon]` a phone number. The export refuses
any page on which the masker would still replace something; mask it in the queue first.
