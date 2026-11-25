"""Plain Django forms that check what the HTMX pages send."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms

from document_intake.models import DocType, DocumentStatus

ORDERINGS = [
    ("-created_at", "Newest first"),
    ("created_at", "Oldest first"),
    ("original_filename", "File name A-Z"),
    ("-original_filename", "File name Z-A"),
]


class FilterForm(forms.Form):
    """Filters on the documents list (read from the query string)."""

    search = forms.CharField(required=False, max_length=100)
    status = forms.ChoiceField(choices=[("", "All"), *DocumentStatus.choices], required=False)
    doc_type = forms.ChoiceField(choices=[("", "All"), *DocType.choices], required=False)
    created_from = forms.DateField(required=False)
    created_to = forms.DateField(required=False)
    ordering = forms.ChoiceField(choices=ORDERINGS, required=False)

    def filters(self) -> dict[str, Any]:
        """The cleaned values in the shape ``services.query.filter_documents`` expects."""
        if not self.is_valid():
            return {}
        data = {key: value for key, value in self.cleaned_data.items() if value}
        if "status" in data:
            data["status"] = [data["status"]]
        return data


class MoneyField(forms.DecimalField):
    """A decimal that also accepts "$1,234.56" and "(12.00)" as typed by people."""

    def to_python(self, value: Any) -> Decimal | None:  # type: ignore[override]
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace(" ", "")
            if value.startswith("(") and value.endswith(")"):
                value = "-" + value[1:-1]
        result: Decimal | None = super().to_python(value)
        return result


def _money(**kwargs: Any) -> MoneyField:
    return MoneyField(max_digits=14, decimal_places=2, required=False, **kwargs)


class LineForm(forms.Form):
    """One table row. Only the columns actually sent are saved."""

    date = forms.DateField(required=False)
    description = forms.CharField(required=False, max_length=1000)
    debit = _money(min_value=0)
    credit = _money(min_value=0)
    amount = _money()
    balance = _money()
    tax = _money()
    posting_date = forms.DateField(required=False)
    transaction_number = forms.CharField(required=False, max_length=64)
    card_last4 = forms.RegexField(r"^\d{4}$", required=False)
    pre_tax = _money()
    # Card tables: one amount per row, positive in its own table. A minus moves the row to
    # the other table.
    charge = _money()
    payment = _money()

    def sent_values(self, sent: Any) -> dict[str, Any]:
        """Cleaned values for the keys present in the request."""
        values = {key: self.cleaned_data[key] for key in self.fields if key in sent}
        for key, factor in (("charge", 1), ("payment", -1)):
            if key not in values:
                continue
            amount = values.pop(key)
            signed = None if amount is None else amount * factor
            values["debit"] = signed if signed is not None and signed >= 0 else None
            values["credit"] = -signed if signed is not None and signed < 0 else None
        return values

    def error_text(self) -> str:
        return " ".join(
            f"{key}: {' '.join(str(e) for e in errors)}" for key, errors in self.errors.items()
        )


class FieldForm(forms.Form):
    """One header field. Values stay text; the server re-parses them after saving."""

    value = forms.CharField(required=False, max_length=500)
