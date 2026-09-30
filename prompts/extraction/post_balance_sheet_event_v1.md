You read one page of a Polish company's filed financial statement: usually a page of the notes
(*informacja dodatkowa*), in Polish. Names of people on the page are replaced by `[osoba]`, PESEL numbers
by `[pesel]`, e-mail addresses by `[email]` and phone numbers by `[telefon]`.

Decide one thing: does this page state `post_balance_sheet_event` about this company?

- **Present** when the page states: an event after the balance-sheet date that bears on the company's condition.
- **Not present**, for example: the standard sentence that no such event occurred.

A policy template, a hypothetical, a general description of risks, or a statement about another company
is not present. If the page does not clearly state it, answer not present.

Answer in the required JSON:

- `present`: true or false.
- `evidence`: when present, the shortest span of the page that shows it on its own, usually one sentence,
  copied character for character: same spelling, diacritics, punctuation, spacing and line breaks, with
  `[osoba]` and the other tokens as they appear. Do not translate, correct, abridge or join separate
  passages. When not present, an empty string.
- `confidence`: `high` when the page states it (or its absence) plainly, `medium` when it takes reading
  between the lines, `low` when you are unsure.
