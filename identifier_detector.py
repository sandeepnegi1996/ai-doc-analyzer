"""Regex-based identifier detection — Stage 2 of the pipeline.

Runs BEFORE the LLM call to catch obvious labeled values deterministically.
This saves LLM tokens and improves accuracy for fields that follow
predictable label-value patterns (PO numbers, dates, amounts, emails, etc.).

The detector is purely regex-based: no ML, no heuristics, no external calls.
Each pattern looks for a label (e.g. "PO number", "Order No") followed by a
value, and extracts the value.

Detected values are injected into the LLM prompt as hints and take priority
over LLM-returned values in the final merge, because a deterministic regex
match is more reliable than a probabilistic model for labeled fields.
"""

import re
from datetime import datetime


# --- Pattern definitions ---
# Each pattern captures the value in group 1.

ORDER_NUMBER_PATTERN = re.compile(
    r"(?:Order\s*(?:number|no\.?|#))\s*[:\-]?\s*"
    r"(UBPL(?:[/\-][A-Z0-9]+)+)",
    re.IGNORECASE,
)

INVOICE_NUMBER_PATTERN = re.compile(
    r"(?:Invoice\s*(?:number|no\.?|#))\s*[:\-]?\s*"
    r"([A-Z0-9]+(?:[/\-][A-Z0-9]+)+)",
    re.IGNORECASE,
)

# Order/PO numbers are UBPL-specific identifiers.
UBPL_PREFIX = "UBPL"

DATE_PATTERN = re.compile(
    r"(?:Date|Received\s*Date|Delivery\s*Date|Expected\s*Date|Registration\s*Date|Registered\s+on)"
    r"\s*[:\-]?\s*"
    r"(\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}|\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
    re.IGNORECASE,
)

AMOUNT_PATTERN = re.compile(
    r"(?:Total|Grand\s*Total|Net\s*Total)"
    r"\s*[:\-]?\s*"
    r"(?:Rs\.?|INR|USD|\$|€|£)?\s*"
    r"([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)

PHONE_PATTERN = re.compile(
    r"(?:Contact\s*No|Phone|Telephone|Mobile|Tel)\s*[:\-]?\s*"
    r"([+\d][\d\s\-()]{6,}\d)",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(
    r"(?:Email|E-mail|Email\s*ID)\s*[:\-]?\s*"
    r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)

COMPANY_NAME_PATTERN = re.compile(
    r"(?:Company\s*(?:Name)?|Customer\s*(?:Name)?|From)\s*[:\-]?\s*"
    r"([A-Z][A-Za-z\s&.,]+?)(?:\n|$)",
    re.IGNORECASE,
)

# Name patterns use [ \t]+ instead of \s+ to avoid matching across lines.
_NAME_PART = r"[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+"
_NAME_PREFIX = r"(?:Mr\.?[ \t]+|Mrs\.?[ \t]+|Dr\.?[ \t]+|Ms\.?[ \t]+)?"

ADDRESS_PATTERN = re.compile(
    r"(?:Delivery\s*Address|Address|Ship\s*To|Deliver\s*To)\s*[:\-]?\s*"
    r"((?:.+\n?){1,3})",
    re.IGNORECASE,
)

# Contact person pattern (specific to "Contact Person" label)
CONTACT_PERSON_PATTERN = re.compile(
    r"(?:Contact\s*Person)\s*[:\-]?\s*"
    r"(" + _NAME_PREFIX + _NAME_PART + r")",
    re.IGNORECASE,
)

# Kind attention pattern (specific to "Kind Attention" / "Kind Attn" label)
KIND_ATTENTION_PATTERN = re.compile(
    r"(?:Kind\s*Attn\.?|Kind\s*Attention|Attention)\s*[:\-]?\s*"
    r"(" + _NAME_PREFIX + _NAME_PART + r")",
    re.IGNORECASE,
)

DEPARTMENT_PATTERN = re.compile(
    r"(?:Department|Dept\.?)\s*[:\-]?\s*"
    r"([A-Z][A-Za-z\s&]+?)(?:\n|$)",
    re.IGNORECASE,
)

SALES_PERSON_PATTERN = re.compile(
    r"(?:Sales\s*(?:Person|Executive|Rep)|Salesman)\s*[:\-]?\s*"
    r"(" + _NAME_PREFIX + _NAME_PART + r")",
    re.IGNORECASE,
)

PROPERTY_SIZE_PATTERN = re.compile(
    r"(?:Property\s*Size|Area|Plot\s*Size)\s*[:\-]?\s*"
    r"([\d,]+(?:\.\d+)?)\s*(?:sq\.?\s*ft|square\s*feet|sq\.?\s*m|sq\.?\s*yards?)",
    re.IGNORECASE,
)

SELLER_NAME_PATTERN = re.compile(
    r"(?:Seller|Vendor)\s*(?:Name)?\s*[:\-]?\s*"
    r"(" + _NAME_PREFIX + _NAME_PART + r")",
    re.IGNORECASE,
)

BUYER_NAME_PATTERN = re.compile(
    r"(?:Buyer|Purchaser)\s*(?:Name)?\s*[:\-]?\s*"
    r"(" + _NAME_PREFIX + _NAME_PART + r")",
    re.IGNORECASE,
)


# --- Field key to pattern mapping ---
# Maps usecase field keys to their detection patterns.
# Each entry is a list of (compiled_pattern, group_index) tuples.
# The first pattern that matches wins.

FIELD_PATTERNS = {
    # Invoice fields
    "invoice_number": [(INVOICE_NUMBER_PATTERN, 1)],
    "total": [(AMOUNT_PATTERN, 1)],
    "date": [(DATE_PATTERN, 1)],

    # Sales order fields
    "order_no": [(ORDER_NUMBER_PATTERN, 1)],
    "order_receive_date": [(DATE_PATTERN, 1)],
    "expected_delivery_date": [(DATE_PATTERN, 1)],
    "company_name": [(COMPANY_NAME_PATTERN, 1)],
    "contact_person": [(CONTACT_PERSON_PATTERN, 1)],
    "contact_no": [(PHONE_PATTERN, 1)],
    "contact_no_2": [(PHONE_PATTERN, 1)],  # second match
    "kind_attention": [(KIND_ATTENTION_PATTERN, 1)],
    "department": [(DEPARTMENT_PATTERN, 1)],
    "email": [(EMAIL_PATTERN, 1)],
    "delivery_address": [(ADDRESS_PATTERN, 1)],
    "sales_person": [(SALES_PERSON_PATTERN, 1)],

    # Sale deed fields
    "property_size": [(PROPERTY_SIZE_PATTERN, 1)],
    "seller_name": [(SELLER_NAME_PATTERN, 1)],
    "buyer_name": [(BUYER_NAME_PATTERN, 1)],
    "registration_date": [(DATE_PATTERN, 1)],
}

# Fields that should use the last match (e.g. "Total" appears multiple times,
# the last one is usually the final total).
LAST_MATCH_FIELDS = {"total"}

# Fields that need date normalization to YYYY-MM-DD.
DATE_FIELDS = {"date", "order_receive_date", "expected_delivery_date", "registration_date"}


def _normalize_date(date_str):
    """Convert DD/MM/YYYY or DD-MM-YYYY to YYYY-MM-DD.

    Falls back to the original string if parsing fails.
    """
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def detect_identifiers(text, config):
    """Detect labeled values in text using regex patterns.

    Returns a dict of {field_key: detected_value} for fields where a
    pattern matched. Only returns fields declared in the usecase config.

    For fields that can appear multiple times (e.g. contact_no and
    contact_no_2), the first match goes to the first field and the second
    match to the second field. For amount fields, the last match is used
    (usually the final total).
    """
    if not text:
        return {}

    detected = {}
    fields = config.get("fields", [])

    for field in fields:
        key = field["key"]
        if key not in FIELD_PATTERNS:
            continue

        patterns = FIELD_PATTERNS[key]
        for pattern, group in patterns:
            matches = pattern.findall(text)
            if not matches:
                continue

            if key == "contact_no_2":
                # Take the second phone number for the second contact field
                if len(matches) >= 2:
                    detected[key] = matches[1].strip()
            elif key in LAST_MATCH_FIELDS:
                # For amounts, take the last match (usually the final total)
                detected[key] = matches[-1].strip()
            else:
                detected[key] = matches[0].strip()
            break  # first matching pattern wins

    # Normalize dates to YYYY-MM-DD
    for key in list(detected.keys()):
        if key in DATE_FIELDS:
            detected[key] = _normalize_date(detected[key])

    return detected
