You read one page of a Polish company's filed financial statement: usually a page of the notes
(*informacja dodatkowa*), in Polish. Names of people on the page are replaced by `[osoba]`, PESEL numbers
by `[pesel]`, e-mail addresses by `[email]` and phone numbers by `[telefon]`.

Decide one thing: does this page state `litigation` about this company?

- **Present** when the page states: a court, arbitration or enforcement case involving the company, pending or decided, with a claim.
- **Not present**, for example: routine collection of receivables with no case.

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
