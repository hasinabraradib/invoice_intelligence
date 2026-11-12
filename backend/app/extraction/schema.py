"""What the LLM must return. Every value is the raw printed TEXT (e.g. "1,320.00");
turning it into Decimal/date is done later by tested code (CLAUDE.md rules 1-2)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["found", "missing", "uncertain"]


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate extra keys from the model

    value: str | None = Field(
        default=None, description="Exact text as printed in the document. Null if missing."
    )
    status: Status = Field(
        default="missing",
        description="found = printed and clear; missing = not printed; uncertain = unclear.",
    )
    evidence: str | None = Field(
        default=None, description="Exact quote from the document that contains the value."
    )
    page: int | None = Field(default=None, description="Page number from '=== PAGE n ==='.")


def _missing() -> ExtractedField:
    return ExtractedField()


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: ExtractedField = Field(default_factory=_missing)
    quantity: ExtractedField = Field(default_factory=_missing)
    unit_price: ExtractedField = Field(default_factory=_missing)
    line_total: ExtractedField = Field(default_factory=_missing)


class InvoiceExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_number: ExtractedField = Field(default_factory=_missing)
    invoice_date: ExtractedField = Field(default_factory=_missing)
    due_date: ExtractedField = Field(default_factory=_missing)
    supplier_name: ExtractedField = Field(default_factory=_missing)
    customer_name: ExtractedField = Field(default_factory=_missing)
    currency: ExtractedField = Field(default_factory=_missing)
    subtotal: ExtractedField = Field(default_factory=_missing)
    tax: ExtractedField = Field(default_factory=_missing)
    total: ExtractedField = Field(default_factory=_missing)
    line_items: list[LineItem] = Field(default_factory=list)


HEADER_FIELDS = ("invoice_number", "invoice_date", "due_date", "supplier_name", "customer_name")
AMOUNT_FIELDS = ("subtotal", "tax", "total")
ALL_FIELDS = (*HEADER_FIELDS, "currency", *AMOUNT_FIELDS)
LINE_ITEM_FIELDS = ("description", "quantity", "unit_price", "line_total")


def llm_json_schema() -> dict[str, Any]:
    """JSON schema for the providers' structured-output mode.

    Pydantic's schema uses "$ref" links and titles; providers handle a plain, fully inlined
    schema best, so we inline the links, drop titles/defaults, and make every key required
    with no extra keys allowed (the model must always say "missing" explicitly).
    """
    schema = InvoiceExtraction.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return clean(defs[node["$ref"].split("/")[-1]])
            out = {k: clean(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return clean(schema)
