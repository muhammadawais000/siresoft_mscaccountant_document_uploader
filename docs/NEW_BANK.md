# A statement from a new bank

Credit card statements are read in two layers: the model copies each page, and code reads
everything it can from the PDF text and checks the result (closing balance, purchases and
payments totals, row count, totals per card). A new bank usually works without changes. When
a check fails on a correct statement, find out what the code could not read:

```bash
docker compose exec web python manage.py inspect_statement /path/to/statement.pdf
```

It shows, without calling the model and without storing anything:

- the route (PDF text or vision for scans) and readable characters per page;
- how many transaction lines were found, and which pages go to the model;
- card sections and printed totals per card;
- every summary value found next to its label, and what is "not found".

## Fixing it

| What `inspect_statement` shows | What to do |
|---|---|
| A summary value "not found", but the PDF prints it | Add the bank's wording to `DOCUMENT_INTAKE["CARD_SUMMARY_LABELS"]` in `config/settings/base.py` (lower case; spaces optional; `&` also matches "and" and "/"), then restart |
| Transaction lines "not found" | The row layout is new: send the PDF to the developer |
| Route VISION for a digital PDF | Its text layer is masked or empty: it is treated as a scan (no row count, always needs review) |

Example: a bank that prints "Balance brought forward $10.00":

```python
from document_intake.conf import DEFAULTS

LABELS = DEFAULTS["CARD_SUMMARY_LABELS"]
DOCUMENT_INTAKE = {
    # ... existing settings ...
    "CARD_SUMMARY_LABELS": {
        **LABELS,
        "previous_balance": [*LABELS["previous_balance"], "balance brought forward"],
    },
}
```

Then reprocess the statement. Every bank layout that needed a fix should also get a test with
invented names (see `tests/card_fixtures.py`), so later changes never break it.

Layouts covered by tests: BMO (cards with sections and tax columns), TD, CIBC (payments under
their own heading, without a minus), Scotia (reference number before the dates, "500.00-").
