import math
from typing import Optional


def fmt_price(price: Optional[float]) -> str:
    """Human-readable price across magnitudes.

    Two decimals at/above $1, and enough significant decimals for sub-dollar coins
    so a micro-priced coin (e.g. SATS at ~$0.0000003) is shown as 0.0000003 rather
    than rounding to $0.000000.
    """
    if price is None:
        return "—"
    if price <= 0:
        return "0"
    if price >= 1:
        return f"{price:,.2f}"
    decimals = 3 - int(math.floor(math.log10(price)))  # ~4 significant figures
    return f"{price:.{decimals}f}".rstrip("0").rstrip(".")
