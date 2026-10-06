You read one page of a Polish company's filed financial statement: usually a page of the notes
(*informacja dodatkowa*), in Polish. Names of people on the page are replaced by `[osoba]`, PESEL numbers
by `[pesel]`, e-mail addresses by `[email]` and phone numbers by `[telefon]`.

Before the page you are given this statement's balance-sheet date, as `<balance_sheet_date>`.

Decide two things: does this page state `post_balance_sheet_event` about this company, and if so, of what
kind?

- **Present** when the page states: an event after the balance-sheet date that bears on the company's condition.
- **Not present**, for example: the standard sentence that no such event occurred.

**The date.** The event must have occurred after the given balance-sheet date. A section headed as events
after the balance-sheet date (*zdarzenia po dniu bilansowym*, *po dniu bilansowym*) counts as dated, unless
its text names a period that ended by the balance-sheet date. Filers copy paragraphs from earlier years'
statements: text that refers to an earlier year's statement or balance sheet (for example "the 2020
statement" when the balance-sheet date is in 2023) describes that earlier year, not this one, and is not
present.

**The kind**, when present:
- `adverse`: the event worsens the company's position or prospects, for example a bankruptcy or
  restructuring petition filed or proceedings opened, a lost contract or customer, a loan called or a
  default, a ruling or enforcement against the company, liquidation, a material loss.
- `favourable`: the event helps it, for example state aid or a subsidy received, new contracts won, capital
  raised, debt forgiven.
- `neutral`: the page describes the event without stating an effect on the company, for example a general
  paragraph on a pandemic or the economy, or a change of shareholder.
- When the page states several events, the kind is the most adverse of them, and the evidence shows that
  event.

A policy template, a hypothetical, a general description of risks, or a statement about another company
is not present. If the page does not clearly state it, answer not present.

Answer in the required JSON:

- `present`: true or false.
- `value`: when present, `adverse`, `favourable` or `neutral`; when not present, `none`.
- `evidence`: when present, the shortest span of the page that shows the event on its own, usually one
  sentence, copied character for character: same spelling, diacritics, punctuation, spacing and line
  breaks, with `[osoba]` and the other tokens as they appear. Do not translate, correct, abridge or join
  separate passages. A section heading alone is not evidence. When not present, an empty string.
- `confidence`: `high` when the page states it (or its absence) plainly, `medium` when it takes reading
  between the lines, `low` when you are unsure.
