"""Canonical label sets and variant -> canonical mappings.

Every label is first normalised with `norm_key()` (lower-case, alphanumerics
only) so that case, spacing and punctuation variants collapse automatically.
Only genuinely different spellings/synonyms need an explicit entry.
Anything that still does not match is mapped to 'Unknown' and logged by the
cleaning step, so a new variant in a later data drop is visible, not silent.
"""
import re

UNKNOWN = "Unknown"


def norm_key(value) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _build(canon_to_variants: dict) -> dict:
    out = {}
    for canon, variants in canon_to_variants.items():
        out[norm_key(canon)] = canon
        for v in variants:
            out[norm_key(v)] = canon
    return out


CATEGORY = _build({
    "Fashion": ["Clothing", "Fashion & Apparel", "Fashion and Apparel", "Apparel"],
    "Mobile & Accessories": ["Mobile and Accessories", "Mobile Acc.", "Mobiles", "Mobile", "Mobile Accessories"],
    "Home & Kitchen": ["Home and Kitchen", "Home&Kitchen", "Home", "Kitchen"],
    "Electronics": ["Elec.", "Electronic Items", "Electronic"],
    "Beauty & Personal Care": ["Beauty and Personal Care", "Beauty", "Personal Care"],
    "Footwear": ["Shoes", "Foot wear"],
    "Grocery": ["Groceries", "Food & Grocery", "Food and Grocery", "Food"],
    "Sports & Outdoors": ["Sports and Outdoors", "Sports", "Outdoors"],
    "Health & Wellness": ["Health and Wellness", "Health", "Wellness"],
    "Toys & Games": ["Toys and Games", "Toys", "Games"],
    "Books & Stationery": ["Books and Stationery", "Books", "Stationery", "Stationary", "Books & Stationary"],
    "Automotive": ["Auto", "Car Accessories", "Automobile"],
})
CANONICAL_CATEGORIES = sorted(set(CATEGORY.values()))

# sub_category / brand prefixes -> category (used to repair or cross-check category)
SUBCAT_PREFIX = {
    "fashion": "Fashion", "mobile": "Mobile & Accessories", "home": "Home & Kitchen",
    "electronics": "Electronics", "beauty": "Beauty & Personal Care", "footwear": "Footwear",
    "grocery": "Grocery", "sports": "Sports & Outdoors", "health": "Health & Wellness",
    "toys": "Toys & Games", "books": "Books & Stationery", "automotive": "Automotive",
}

PROVINCE = _build({
    "Punjab": ["Panjab"],
    "Sindh": ["Sind"],
    "Khyber Pakhtunkhwa": ["KP", "KPK", "NWFP"],
    "Balochistan": ["Baluchistan"],
    "Gilgit-Baltistan": ["GB", "Gilgit Baltistan"],
    "Azad Kashmir": ["AJK", "Azad Jammu & Kashmir", "Azad Jammu and Kashmir"],
    "Islamabad Capital Territory": ["Islamabad", "ICT"],
})

REGION = _build({"North": [], "Central": [], "South": []})
POP_TIER = _build({"Metro": [], "Tier-2": ["Tier 2"], "Tier-3": ["Tier 3"]})

GENDER = _build({"M": ["Male", "Man"], "F": ["Female", "Woman"]})

SELLER_TIER = _build({"Gold": [], "Silver": [], "Bronze": []})

ORDER_STATUS = _build({
    "Delivered": ["Deliverd", "Complete", "Completed"],
    "Cancelled": ["Canceled", "Cancel"],
    "Returned": ["Return", "Returned to Seller", "RTO"],
    "In Transit": ["In-Transit", "Shipped", "Out for Delivery", "Dispatched"],
})

DELIVERY_STATUS = _build({
    "Delivered": ["Deliverd", "Complete", "Completed"],
    "Returned to Seller": ["Return to Seller", "RTO", "Returned"],
    "In Transit": ["In-Transit", "Shipped", "Out for Delivery", "Dispatched"],
})

CHANNEL = _build({
    "App": ["Mobile App", "Android", "iOS"],
    "Web": ["Website", "Desktop", "Desktop Web"],
    "Mobile Web": ["MWeb", "M-Web", "Mobile Site"],
})

PAYMENT_METHOD = _build({
    "COD": ["Cash on Delivery", "Cash"],
    "Card": ["Credit/Debit Card", "Debit Card", "Credit Card", "Visa", "Mastercard"],
    "JazzCash": ["Jazz Cash"],
    "EasyPaisa": ["Easy Paisa"],
    "Bank Transfer": ["Bank Trf", "IBFT", "Bank"],
})

PAYMENT_STATUS = _build({
    "Paid": ["Complete", "Completed", "Success", "Successful"],
    "Cancelled": ["Canceled", "Failed"],
    "Refunded": ["Refund"],
    "Pending": [],
})

COURIER = _build({
    "TCS": ["TCS Express", "T.C.S"],
    "Leopards": ["Leopard Courier", "Leopard", "Leopards Courier"],
    "M&P": ["M & P", "MnP", "M and P"],
    "Trax": [],
    "PostEx": ["Post Ex"],
    "BlueEx": ["Blue EX", "Blue-Ex"],
})

RETURN_STATUS = _build({
    "Refunded": ["Refund"],
    "Approved": [],
    "Rejected": ["Declined", "Denied"],
})

RETURN_REASON = _build({
    "Not as described": ["different from picture", "Not as Described"],
    "Size/fit issue": ["size issue", "Doesn't fit", "size/fit", "Wrong size"],
    "Quality issue": ["Poor quality", "bad quality", "quality"],
    "Damaged product": ["damaged", "Item damaged"],
    "Changed mind": ["Change of mind", "changed my mind", "no longer needed"],
    "Wrong item delivered": ["Wrong Item", "received wrong product"],
    "Colour mismatch": ["color mismatch", "colour different", "Wrong colour", "Wrong color"],
    "Late delivery": ["Delivered late", "too late", "late"],
    "Missing parts": ["Parts missing", "missing part", "incomplete"],
})

NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def map_label(value, mapping: dict, unknown: str = UNKNOWN):
    """Return canonical label, `unknown` for blanks, and None for unmapped values
    (caller logs those and decides)."""
    k = norm_key(value)
    if k == "":
        return unknown
    return mapping.get(k)
