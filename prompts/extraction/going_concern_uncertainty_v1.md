You read one page of a Polish company's filed financial statement: usually a page of the notes
(*informacja dodatkowa*), in Polish. Names of people on the page are replaced by `[osoba]`, PESEL numbers
by `[pesel]`, e-mail addresses by `[email]` and phone numbers by `[telefon]`.

Decide one thing: does this page state `going_concern_uncertainty` about this company?

- **Present** when the page states: the company's ability to continue as a going concern is threatened or uncertain, or the statements are not prepared on the going-concern basis; or bankruptcy, restructuring or liquidation of the company is pending or open.
- **Not present**, for example: the accounting-policy sentence that the statements are prepared on the going-concern assumption, with no threat stated; a statement that no threat exists.

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
