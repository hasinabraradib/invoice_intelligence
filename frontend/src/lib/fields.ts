// The invoice fields shown on the review screen, with how each one is edited.
export type FieldKind = "text" | "money" | "date" | "currency";

export const HEADER_FIELDS: { name: string; label: string; kind: FieldKind }[] = [
  { name: "invoice_number", label: "Invoice number", kind: "text" },
  { name: "invoice_date", label: "Invoice date", kind: "date" },
  { name: "due_date", label: "Due date", kind: "date" },
  { name: "supplier_name", label: "Supplier", kind: "text" },
  { name: "customer_name", label: "Customer", kind: "text" },
  { name: "currency", label: "Currency", kind: "currency" },
  { name: "subtotal", label: "Subtotal", kind: "money" },
  { name: "tax", label: "Tax", kind: "money" },
  { name: "total", label: "Total", kind: "money" },
];

export const CURRENCIES = ["BDT", "USD", "EUR", "GBP", "INR", "JPY", "CNY", "AUD", "CAD", "CHF", "SGD", "AED", "SAR", "PKR"];

/** DOM id for a field, so issue links can jump to it. "line_items[2].line_total" -> "field-line-2-line_total". */
export function fieldAnchor(field: string | null | undefined): string {
  if (!field) return "fields";
  const line = /^line_items\[(\d+)\](?:\.(\w+))?$/.exec(field);
  if (line) return `field-line-${line[1]}${line[2] ? `-${line[2]}` : ""}`;
  return `field-${field.replace(/[^\w-]/g, "_")}`;
}
