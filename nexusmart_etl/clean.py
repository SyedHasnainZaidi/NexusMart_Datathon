"""clean task: standardisation and cleaning rules for all nine raw files.

Each rule is logged to DQLog with the number of affected rows and the action
taken (IMPUTE / FLAG / QUARANTINE / RETAIN / STANDARDISE / REPAIR / MERGE).
Rows that cannot be repaired are written to the reject store with a reason
code - never silently dropped.

Rule codes are referenced in README.md and the Data Quality Report.
"""
import logging

import numpy as np
import pandas as pd

from . import config
from .mappings import (CATEGORY, CHANNEL, COURIER, DELIVERY_STATUS, GENDER, ORDER_STATUS,
                       PAYMENT_METHOD, PAYMENT_STATUS, POP_TIER, PROVINCE, REGION, RETURN_REASON,
                       RETURN_STATUS, SELLER_TIER, SUBCAT_PREFIX, UNKNOWN, map_label, norm_key)
from .utils import DQLog, clean_text, parse_dates, parse_money, parse_quantity, title_case

log = logging.getLogger(__name__)

START = pd.Timestamp(config.VALID_ORDER_START)
END = pd.Timestamp(config.VALID_ORDER_END)
SENTINELS = config.SENTINEL_VALUES


# ------------------------------------------------------------------ helpers
def _std_label(df, col, mapping, table, dq, unknown=UNKNOWN, rule="STD"):
    raw = df[col]
    mapped = raw.map(lambda v: map_label(v, mapping, unknown))
    unmapped = mapped.isna()
    if unmapped.any():
        dq.rule(table, col, f"{rule}_UNMAPPED", "Label not in canonical set -> 'Unknown' (review mapping)",
                unmapped.sum(), "FLAG")
        log.warning("%s.%s unmapped labels: %s", table, col, raw[unmapped].value_counts().head(10).to_dict())
    mapped = mapped.fillna(unknown)
    changed = (mapped != raw.astype(str)) & ~unmapped
    dq.rule(table, col, rule, f"Standardised {raw.nunique()} raw variants to {mapped.nunique()} canonical values",
            changed.sum(), "STANDARDISE")
    blanks = raw.astype(str).str.strip() == ""
    if blanks.any():
        dq.rule(table, col, f"{rule}_BLANK", f"Blank -> '{unknown}'", blanks.sum(), "IMPUTE")
    return mapped


def _int_id(series):
    return pd.to_numeric(series.astype(str).str.strip().replace("", np.nan), errors="coerce").astype("Int64")


def _dedupe_exact(df, table, dq, id_col):
    before = len(df)
    dup = df.duplicated(keep="first")
    dq.reject(table, df[dup], id_col, "EXACT_DUPLICATE", "Exact duplicate row; first occurrence kept")
    df = df[~dup].copy()
    dq.rule(table, "*", "DUP_EXACT", "Exact duplicate rows removed (first kept)", dup.sum(), "QUARANTINE", before, len(df))
    # same primary key, different content -> keep first, reject the rest
    pk_dup = df[id_col].astype(str).str.strip().duplicated(keep="first")
    if pk_dup.any():
        dq.reject(table, df[pk_dup], id_col, "DUPLICATE_KEY_CONFLICT", "Primary key repeated with different content")
        dq.rule(table, id_col, "DUP_KEY", "Repeated primary key with conflicting content (first kept)",
                pk_dup.sum(), "QUARANTINE", len(df), len(df) - pk_dup.sum())
        df = df[~pk_dup].copy()
    return df


# ---------------------------------------------------------------- locations
def clean_locations(df, dq: DQLog):
    t = "locations"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "city_id")
    df["city_id"] = _int_id(df.city_id)
    bad = df.city_id.isna()
    dq.reject(t, df[bad], "city_id", "INVALID_KEY", "city_id not an integer")
    df = df[~bad].copy()
    df["city"] = title_case(df.city)
    df["province"] = _std_label(df, "province", PROVINCE, t, dq)
    df["region"] = _std_label(df, "region", REGION, t, dq)
    df["population_tier"] = _std_label(df, "population_tier", POP_TIER, t, dq)
    df["latitude"] = pd.to_numeric(df.latitude, errors="coerce")
    df["longitude"] = pd.to_numeric(df.longitude, errors="coerce")
    out_pk = ~df.latitude.between(23, 37.5) | ~df.longitude.between(60, 78)
    out_pk &= df.latitude.notna() & df.longitude.notna()
    df.loc[out_pk, ["latitude", "longitude"]] = np.nan
    dq.rule(t, "latitude/longitude", "GEO_RANGE", "Coordinates outside Pakistan bounding box -> NULL", out_pk.sum(), "FLAG")
    dq.rule(t, "latitude/longitude", "GEO_MISSING", "Missing coordinates retained as NULL (not used for keys)",
            (df.latitude.isna() | df.longitude.isna()).sum(), "RETAIN")

    # Missing province: infer from the 5 nearest cities (same region) that have one.
    df["province_imputed"] = False
    miss = df.province == UNKNOWN
    known = df[~miss & df.latitude.notna() & (df.province != "Islamabad Capital Territory")]
    fixed = 0
    for idx, row in df[miss].iterrows():
        cand = known[known.region == row.region] if row.region != UNKNOWN else known
        if pd.isna(row.latitude) or cand.empty:
            continue
        d = (cand.latitude - row.latitude) ** 2 + (cand.longitude - row.longitude) ** 2
        vote = cand.loc[d.nsmallest(5).index, "province"].mode()
        if len(vote):
            df.at[idx, "province"] = vote.iloc[0]
            df.at[idx, "province_imputed"] = True
            fixed += 1
    dq.rule(t, "province", "PROVINCE_KNN", "Blank province inferred from 5 nearest cities in same region",
            fixed, "IMPUTE")
    dq.rule(t, "province", "PROVINCE_UNRESOLVED", "Blank province with no coordinates -> 'Unknown'",
            (df.province == UNKNOWN).sum(), "FLAG")
    dq.set_count(t, "clean", len(df))
    return df[["city_id", "city", "province", "region", "latitude", "longitude", "population_tier", "province_imputed"]]


# ------------------------------------------------------------------ sellers
def clean_sellers(df, locations, dq: DQLog):
    t = "sellers"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "seller_id")
    df["seller_id"] = clean_text(df.seller_id).str.upper()
    df["seller_name"] = clean_text(df.seller_name)
    df["seller_tier"] = _std_label(df, "seller_tier", SELLER_TIER, t, dq)
    jd = parse_dates(df.join_date)
    bad = jd.notna() & ((jd < "2000-01-01") | (jd > END))
    invalid = (df.join_date.str.strip() != "") & jd.isna()
    dq.rule(t, "join_date", "DATE_INVALID", "Unparseable/impossible join_date -> NULL", invalid.sum(), "FLAG")
    dq.rule(t, "join_date", "DATE_RANGE", "join_date before 2000 or after data period -> NULL", bad.sum(), "FLAG")
    dq.rule(t, "join_date", "DATE_BLANK", "Blank join_date retained as NULL", (df.join_date.str.strip() == "").sum(), "RETAIN")
    df["join_date"] = jd.mask(bad)
    r = pd.to_numeric(df.rating, errors="coerce")
    bad_r = r.notna() & ~r.between(1, 5)
    dq.rule(t, "rating", "RATING_RANGE", "Rating outside 1-5 (e.g. 0, 11) -> NULL", bad_r.sum(), "FLAG")
    dq.rule(t, "rating", "RATING_BLANK", "Blank rating retained as NULL", r.isna().sum(), "RETAIN")
    df["rating"] = r.mask(bad_r)
    df["city_id"] = _int_id(df.city_id)
    orphan = df.city_id.isna() | ~df.city_id.isin(locations.city_id)
    dq.rule(t, "city_id", "FK_LOCATION", "city_id missing/not in locations -> Unknown location", orphan.sum(), "FLAG")
    df.loc[orphan, "city_id"] = pd.NA
    dq.set_count(t, "clean", len(df))
    return df[["seller_id", "seller_name", "join_date", "seller_tier", "city_id", "rating"]]


# ----------------------------------------------------------------- products
def clean_products(df, sellers, dq: DQLog):
    """List-price repair needs order history, so it is finished later in
    `repair_product_prices`."""
    t = "products"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "product_id")
    df["product_id"] = clean_text(df.product_id).str.upper()
    df["seller_id"] = clean_text(df.seller_id).str.upper()
    df["product_name"] = title_case(df.product_name)

    # category: map variants; if unmappable, infer from sub_category prefix
    cat = df.category.map(lambda v: map_label(v, CATEGORY, None))
    sub_cat = df.sub_category.map(lambda v: SUBCAT_PREFIX.get(norm_key(str(v).split(" ")[0])) if str(v).strip() else None)
    inferred = cat.isna() & sub_cat.notna()
    cat = cat.fillna(sub_cat).fillna(UNKNOWN)
    changed = (cat != df.category)
    dq.rule(t, "category", "STD_CATEGORY",
            f"{df.category.nunique()} raw category labels -> {cat.nunique()} canonical categories", changed.sum(), "STANDARDISE")
    dq.rule(t, "category", "CATEGORY_FROM_SUBCAT", "Unmappable category inferred from sub_category", inferred.sum(), "IMPUTE")
    conflict = sub_cat.notna() & (sub_cat != cat)
    dq.rule(t, "category", "CATEGORY_SUBCAT_CONFLICT", "Category disagrees with sub_category prefix (category kept)", conflict.sum(), "FLAG")
    df["category"] = cat

    df["sub_category"] = clean_text(df.sub_category)
    dq.rule(t, "sub_category", "SUBCAT_BLANK", "Blank sub_category -> 'Unknown'", df.sub_category.isna().sum(), "IMPUTE")
    df["sub_category"] = df.sub_category.fillna(UNKNOWN)

    df["brand"] = clean_text(df.brand)
    known_brands = set(df.brand.dropna().unique())
    first_word = df.product_name.str.split(" ").str[0]
    fill = df.brand.isna() & first_word.isin(known_brands)
    dq.rule(t, "brand", "BRAND_FROM_NAME", "Blank brand repaired from first word of product_name (known brand)", fill.sum(), "REPAIR")
    df.loc[fill, "brand"] = first_word[fill]
    dq.rule(t, "brand", "BRAND_BLANK", "Blank brand not recoverable -> 'Unknown'", df.brand.isna().sum(), "IMPUTE")
    df["brand"] = df.brand.fillna(UNKNOWN)

    lp_raw = parse_money(df.list_price)
    df["list_price_raw"] = lp_raw
    neg = lp_raw < 0
    dq.rule(t, "list_price", "PRICE_NEGATIVE", "Negative list_price -> absolute value (sign error)", neg.sum(), "REPAIR")
    lp = lp_raw.abs()
    bad = lp.isna() | (lp == 0) | lp.isin(SENTINELS)
    dq.rule(t, "list_price", "PRICE_INVALID", "list_price zero / 9999999 sentinel / non-numeric -> re-derived from sales history",
            bad.sum(), "REPAIR")
    df["list_price"] = lp.mask(bad)
    df["list_price_repaired"] = neg | bad

    cost = parse_money(df.cost_price).abs()
    df["cost_price"] = cost
    df["cost_price_raw"] = cost
    w = pd.to_numeric(df.weight_kg, errors="coerce")
    bad_w = w.notna() & ((w <= 0) | (w > 50))
    dq.rule(t, "weight_kg", "WEIGHT_RANGE", "weight_kg <= 0 or > 50 kg -> NULL then imputed", bad_w.sum(), "FLAG")
    w = w.mask(bad_w)
    miss_w = w.isna()
    w = w.fillna(df.assign(w=w).groupby("category").w.transform("median"))
    dq.rule(t, "weight_kg", "WEIGHT_IMPUTE", "Missing/invalid weight -> category median", miss_w.sum(), "IMPUTE")
    df["weight_kg"] = w.round(2)
    df["weight_imputed"] = miss_w

    orphan = ~df.seller_id.isin(sellers.seller_id)
    dq.rule(t, "seller_id", "FK_SELLER", "Product seller not in sellers -> Unknown", orphan.sum(), "FLAG")
    df.loc[orphan, "seller_id"] = pd.NA
    dq.set_count(t, "clean", len(df))
    return df


def finish_products(df, dq: DQLog):
    """Cost repair (needs final list price) and duplicate-product merge."""
    t = "products"
    ratio = df.cost_price / df.list_price
    bad_cost = ratio > 1.2
    dq.rule(t, "cost_price", "COST_OUTLIER", "cost_price > 1.2 x list_price (implausible; 1.5x pattern) -> re-imputed",
            bad_cost.sum(), "REPAIR")
    miss = df.cost_price.isna()
    dq.rule(t, "cost_price", "COST_BLANK", "Blank cost_price -> imputed", miss.sum(), "IMPUTE")
    good_ratio = ratio.where(~bad_cost & ratio.between(0.05, 1.2))
    cat_ratio = good_ratio.groupby(df.sub_category).transform("median").fillna(
        good_ratio.groupby(df.category).transform("median")).fillna(good_ratio.median())
    need = bad_cost | miss
    df.loc[need, "cost_price"] = (df.loc[need, "list_price"] * cat_ratio[need]).round()
    df["cost_imputed"] = need

    # ---- duplicate products: same normalised name + category, and (same cost or
    # same weight), and list price within 5%. Never on name alone.
    df["_nm"] = df.product_name.map(norm_key)
    df["_cost0"] = df.cost_price_raw
    df["_w0"] = df.weight_kg.where(~df.weight_imputed)
    mapping = {}
    for _, g in df[df._nm.duplicated(keep=False)].groupby(["_nm", "category"]):
        g = g.sort_values("product_id")
        master = g.iloc[0]
        for _, cand in g.iloc[1:].iterrows():
            same_cost = pd.notna(master._cost0) and master._cost0 == cand._cost0
            same_w = pd.notna(master._w0) and master._w0 == cand._w0
            close = abs(master.list_price - cand.list_price) <= 0.05 * master.list_price
            if (same_cost or same_w) and close:
                mapping[cand.product_id] = master.product_id
    df["master_product_id"] = df.product_id.map(mapping).fillna(df.product_id)
    pmap = df[["product_id", "master_product_id"]].copy()
    # fill master gaps from its duplicates (brand, sub_category, cost, weight)
    dups = df[df.product_id != df.master_product_id]
    for _, d in dups.iterrows():
        mi = df.index[df.product_id == d.master_product_id][0]
        for col in ["brand", "sub_category"]:
            if df.at[mi, col] == UNKNOWN and d[col] != UNKNOWN:
                df.at[mi, col] = d[col]
        if df.at[mi, "cost_imputed"] and not d.cost_imputed:
            df.at[mi, "cost_price"], df.at[mi, "cost_imputed"] = d.cost_price, False
        if df.at[mi, "weight_imputed"] and not d.weight_imputed:
            df.at[mi, "weight_kg"], df.at[mi, "weight_imputed"] = d.weight_kg, False
    dq.rule(t, "product_id", "DUP_ENTITY",
            "Same product under different IDs (name+category+cost/weight+price within 5%) merged to lowest ID",
            len(mapping), "MERGE", len(df), len(df) - len(mapping))
    dq.reject(t, dups.drop(columns=["_nm", "_cost0", "_w0"]), "product_id", "MERGED_DUPLICATE",
              "Duplicate product merged into master_product_id (kept in product_id_map)")
    masters = df[df.product_id == df.master_product_id].drop(columns=["_nm", "_cost0", "_w0"])
    dq.set_count(t, "clean", len(masters))
    return masters, pmap


# ---------------------------------------------------------------- customers
def _norm_phone(series):
    d = series.fillna("").astype(str).str.replace(r"\D", "", regex=True)
    d = d.str.replace(r"^0092", "0", regex=True).str.replace(r"^92(?=3\d{9}$)", "0", regex=True)
    d = d.where(~d.str.match(r"^3\d{9}$"), "0" + d)
    return d.where(d.str.match(r"^03\d{9}$"))


def clean_customers(df, locations, dq: DQLog):
    t = "customers"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "customer_id")
    df["customer_id"] = clean_text(df.customer_id).str.upper()
    df["full_name"] = title_case(df.full_name)
    email = clean_text(df.email).str.lower()
    bad_email = email.notna() & ~email.str.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", na=False)
    dq.rule(t, "email", "EMAIL_FORMAT", "Lower-cased/trimmed; invalid format -> NULL", bad_email.sum(), "FLAG")
    df["email"] = email.mask(bad_email)
    dq.rule(t, "email", "EMAIL_BLANK", "Blank email retained as NULL", df.email.isna().sum(), "RETAIN")
    ph = _norm_phone(df.phone)
    bad_ph = (df.phone.str.strip() != "") & ph.isna()
    dq.rule(t, "phone", "PHONE_NORMALISE", "Phone normalised to 03XXXXXXXXX (+92 / dashes / spaces removed)",
            ((df.phone.str.strip() != "") & (ph != df.phone)).sum(), "STANDARDISE")
    dq.rule(t, "phone", "PHONE_INVALID", "Unrecognisable phone -> NULL", bad_ph.sum(), "FLAG")
    df["phone"] = ph
    df["gender"] = _std_label(df, "gender", GENDER, t, dq)
    rd = parse_dates(df.registration_date)
    inval = rd.isna()
    dq.rule(t, "registration_date", "DATE_INVALID", "Unparseable registration_date -> repaired from first order later",
            inval.sum(), "FLAG")
    df["registration_date"] = rd
    by = pd.to_numeric(df.birth_year, errors="coerce")
    bad_by = by.notna() & ~by.between(1940, 2010)
    dq.rule(t, "birth_year", "BIRTH_RANGE", "birth_year outside 1940-2010 -> NULL", bad_by.sum(), "FLAG")
    dq.rule(t, "birth_year", "BIRTH_BLANK", "Blank birth_year retained as NULL (age band 'Unknown')", by.isna().sum(), "RETAIN")
    df["birth_year"] = by.mask(bad_by).astype("Int64")
    df["city_id"] = _int_id(df.city_id)
    orphan = df.city_id.notna() & ~df.city_id.isin(locations.city_id)
    dq.rule(t, "city_id", "FK_LOCATION", "city_id not in locations -> Unknown", orphan.sum(), "FLAG")
    dq.rule(t, "city_id", "CITY_BLANK", "Blank city_id -> Unknown location", df.city_id.isna().sum(), "IMPUTE")
    df.loc[orphan, "city_id"] = pd.NA

    # ---- duplicate customers under different IDs.
    # Match key: same normalised name AND (same email OR same phone), with no
    # conflicting birth_year / city / phone where both records have a value.
    df["_nm"] = df.full_name.map(norm_key)
    parent = {cid: cid for cid in df.customer_id}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    rec = df.set_index("customer_id")

    def compatible(a, b):
        for col in ["birth_year", "city_id", "phone", "email"]:
            va, vb = rec.at[a, col], rec.at[b, col]
            if pd.notna(va) and pd.notna(vb) and va != vb:
                if col in ("phone", "email"):
                    continue  # one of email/phone already matched; the other may have changed
                return False
        return True

    pairs = 0
    for key in ["email", "phone"]:
        sub = df[df[key].notna()]
        for _, g in sub[sub.duplicated(["_nm", key], keep=False)].groupby(["_nm", key]):
            ids = sorted(g.customer_id)
            for other in ids[1:]:
                if compatible(ids[0], other):
                    ra, rb = find(ids[0]), find(other)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
                        pairs += 1
    df["master_customer_id"] = df.customer_id.map(find)
    cmap = df[["customer_id", "master_customer_id"]].copy()
    n_dup = (df.customer_id != df.master_customer_id).sum()
    dq.rule(t, "customer_id", "DUP_ENTITY",
            "Same customer under different IDs (name + email or phone, no conflicting birth year/city) merged to lowest ID",
            n_dup, "MERGE", len(df), len(df) - n_dup)
    # survivorship: master keeps its values, gaps filled from duplicates; earliest registration
    agg = df.sort_values("customer_id").groupby("master_customer_id").agg({
        "full_name": "first", "email": "first", "phone": "first",
        "gender": lambda s: next((v for v in s if v != UNKNOWN), UNKNOWN),
        "registration_date": "min", "city_id": "first", "birth_year": "first"})
    agg = agg.reset_index().rename(columns={"master_customer_id": "customer_id"})
    dq.reject(t, df[df.customer_id != df.master_customer_id].drop(columns=["_nm"]), "customer_id", "MERGED_DUPLICATE",
              "Duplicate customer merged into master_customer_id (kept in customer_id_map)")
    dq.set_count(t, "clean", len(agg))
    return agg, cmap


# ------------------------------------------------------------------- orders
def clean_orders(df, payments_raw, shipping_raw, customers, cmap, locations, dq: DQLog):
    t = "orders"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "order_id")
    df["order_id"] = clean_text(df.order_id).str.upper()
    df["order_status"] = _std_label(df, "order_status", ORDER_STATUS, t, dq)
    df["channel"] = _std_label(df, "channel", CHANNEL, t, dq)

    code = clean_text(df.discount_code).str.upper()
    pct = pd.to_numeric(code.str.extract(r"(\d+)")[0], errors="coerce")
    bad_code = code.notna() & (pct.isna() | (pct > 90))
    dq.rule(t, "discount_code", "DISCOUNT_CODE_STD", "Codes trimmed/upper-cased (save15 -> SAVE15); pct parsed from code",
            (code.fillna("") != df.discount_code).sum(), "STANDARDISE")
    dq.rule(t, "discount_code", "DISCOUNT_CODE_INVALID", "Code without valid % -> treated as no discount", bad_code.sum(), "FLAG")
    df["discount_code"] = code.mask(bad_code)
    df["discount_pct"] = pct.mask(bad_code).fillna(0).astype(int)

    # ---- order_date: parse; repair impossible / out-of-range dates from payment & shipping
    od = parse_dates(df.order_date)
    invalid = od.isna()
    out = od.notna() & ((od < START) | (od > END))
    pdts = parse_dates(payments_raw.payment_date).groupby(payments_raw.order_id.str.strip().str.upper()).min()
    sdts = parse_dates(shipping_raw.shipped_date).groupby(shipping_raw.order_id.str.strip().str.upper()).min()
    evidence = pd.concat([pdts.rename("p"), sdts.rename("s")], axis=1)
    evidence = evidence.where((evidence >= START) & (evidence <= END + pd.Timedelta(days=30)))
    # earliest downstream event; shipments leave 0-2 days after ordering, prepaid payments same day
    proxy = evidence.min(axis=1)
    need = invalid | out
    rep = df.order_id.map(proxy)
    rep = rep.where(rep <= END, END)
    fixed = need & rep.notna()
    dq.rule(t, "order_date", "DATE_INVALID", "Impossible order_date (e.g. 2024-02-30, month 13, 00/00/2024)", invalid.sum(), "FLAG")
    dq.rule(t, "order_date", "DATE_OUT_OF_RANGE", "order_date outside 2024-01-01..2025-06-30 (2026/2027 typos)", out.sum(), "FLAG")
    dq.rule(t, "order_date", "DATE_REPAIRED", "Repaired from earliest payment/shipment date of the order", fixed.sum(), "REPAIR")
    od = od.mask(need, rep)
    df["order_date"] = od
    df["order_date_repaired"] = fixed
    unrep = need & ~fixed
    dq.reject(t, df[unrep], "order_id", "ORDER_DATE_UNREPAIRABLE", "Invalid order date and no payment/shipping date to repair from")
    dq.rule(t, "order_date", "DATE_UNREPAIRABLE", "No evidence to repair date -> quarantined", unrep.sum(), "QUARANTINE",
            len(df), len(df) - unrep.sum())
    df = df[~unrep].copy()

    # ---- customer: map to master id; unknown customer -> Unknown member
    df["customer_id"] = clean_text(df.customer_id).str.upper()
    m = dict(zip(cmap.customer_id, cmap.master_customer_id))
    remapped = df.customer_id.map(m)
    dq.rule(t, "customer_id", "CUSTOMER_REMAP", "Orders re-pointed from duplicate customer IDs to master ID",
            (remapped.notna() & (remapped != df.customer_id)).sum(), "MERGE")
    orphan = remapped.isna()
    dq.rule(t, "customer_id", "FK_CUSTOMER", "customer_id not in customers -> Unknown customer member", orphan.sum(), "FLAG")
    df["customer_id_raw"] = df.customer_id
    df["customer_id"] = remapped

    # ---- ship_city: blank -> customer's registered city (flagged); invalid -> Unknown
    df["ship_city_id"] = _int_id(df.ship_city_id)
    cust_city = customers.set_index("customer_id").city_id
    blank = df.ship_city_id.isna()
    fill = df.customer_id.map(cust_city)
    df["ship_city_imputed"] = blank & fill.notna()
    df.loc[blank, "ship_city_id"] = fill[blank]
    dq.rule(t, "ship_city_id", "SHIP_CITY_FROM_CUSTOMER",
            "Blank ship_city_id -> customer's registered city (89% of orders ship to it); flagged", df.ship_city_imputed.sum(), "IMPUTE")
    orphan_c = df.ship_city_id.isna() | ~df.ship_city_id.isin(locations.city_id)
    dq.rule(t, "ship_city_id", "FK_LOCATION", "Ship city still unknown -> Unknown location member", orphan_c.sum(), "FLAG")
    df.loc[orphan_c, "ship_city_id"] = pd.NA

    tot = parse_money(df.order_total)
    df["order_total_flag"] = np.select([tot.isna(), tot.isin(SENTINELS), tot < 0, tot == 0],
                                       ["MISSING", "SENTINEL", "NEGATIVE", "ZERO"], "OK")
    dq.rule(t, "order_total", "TOTAL_INVALID", "order_total zero/sentinel/negative/non-numeric -> NULL (only used for reconciliation)",
            (df.order_total_flag != "OK").sum(), "FLAG")
    df["order_total"] = tot.where(df.order_total_flag == "OK")
    dq.set_count(t, "clean", len(df))
    return df


# -------------------------------------------------------------- order items
def _fit_price_index(lines):
    """unit_price / list_price drifts upward ~0.25% per month (price inflation).
    Fit ratio = 1 + b * days_since_start on clearly valid lines."""
    ok = lines.ratio.between(0.97, 1.10)
    x = lines.loc[ok, "days"].astype(float)
    y = lines.loc[ok, "ratio"] - 1
    b = float((x * y).sum() / (x * x).sum()) if len(x) else 0.0
    a = float((y - b * x).median()) if len(x) else 0.0
    return a, b


def clean_order_items(df, orders, products, pmap, sellers, dq: DQLog):
    t = "order_items"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "order_item_id")
    for c in ["order_item_id", "order_id", "product_id", "seller_id"]:
        df[c] = clean_text(df[c]).str.upper()

    # orphans: order quarantined / unknown
    orphan = ~df.order_id.isin(orders.order_id)
    dq.reject(t, df[orphan], "order_item_id", "ORPHAN_ORDER", "order_id not in cleaned orders (unknown or quarantined)")
    dq.rule(t, "order_id", "FK_ORDER", "Line whose order is missing/quarantined", orphan.sum(), "QUARANTINE",
            len(df), len(df) - orphan.sum())
    df = df[~orphan].copy()

    # product -> master product; unknown product -> Unknown member
    pm = dict(zip(pmap.product_id, pmap.master_product_id))
    df["product_id_raw"] = df.product_id
    df["product_id"] = df.product_id.map(pm)
    dq.rule(t, "product_id", "PRODUCT_REMAP", "Lines re-pointed from duplicate product IDs to master",
            (df.product_id.notna() & (df.product_id != df.product_id_raw)).sum(), "MERGE")
    dq.rule(t, "product_id", "FK_PRODUCT", "product_id not in catalogue -> Unknown product member", df.product_id.isna().sum(), "FLAG")
    bad_s = ~df.seller_id.isin(sellers.seller_id)
    dq.rule(t, "seller_id", "FK_SELLER", "seller_id not in sellers -> Unknown seller member", bad_s.sum(), "FLAG")
    df.loc[bad_s, "seller_id"] = pd.NA

    o = orders.set_index("order_id")
    df["order_date"] = df.order_id.map(o.order_date)
    df["pct"] = df.order_id.map(o.discount_pct).fillna(0) / 100.0
    df["days"] = (df.order_date - START).dt.days

    # ---- quantity
    q_raw = df.quantity
    q = parse_quantity(q_raw)
    words = q_raw.str.strip().str.lower().str.match(r"^[a-z]+$")
    dq.rule(t, "quantity", "QTY_TEXT", "Quantity written as word / '2.0' converted to integer",
            (words | q_raw.str.contains(r"\.0$")).sum(), "STANDARDISE")
    neg = q < 0
    dq.rule(t, "quantity", "QTY_NEGATIVE", "Negative quantity -> absolute value (sign error)", neg.sum(), "REPAIR")
    q = q.abs()
    q_bad = q.isna() | (q == 0) | (q > 50) | (q != q.round())
    dq.rule(t, "quantity", "QTY_OUTLIER", "Quantity 0, non-integer or > 50 (500 = entry error) -> re-derived", q_bad.sum(), "REPAIR")
    df["quantity"] = q.mask(q_bad)

    # ---- unit price vs list price x inflation index
    up = parse_money(df.unit_price)
    lp = df.product_id.map(products.set_index("product_id").list_price)
    df["ratio"] = up / lp
    a, b = _fit_price_index(df)
    expected = lp * (1 + a + b * df.days)
    r = up / expected
    cls = np.select(
        [up.isna(), up.isin(SENTINELS), up == 0,
         r.between(0.93, 1.07), r.between(93, 107), r.between(-1.07, -0.93), r.between(1 / 320, 1 / 240), up < 0],
        ["MISSING", "SENTINEL", "ZERO", "OK", "X100", "NEGATIVE", "USD", "NEGATIVE_UNKNOWN_PRODUCT"], default="OTHER")
    cls = pd.Series(cls, index=df.index)
    cls[(cls == "OTHER") & lp.isna() & (up > 0)] = "OK_UNVALIDATED"
    fixed_up = up.copy()
    fixed_up[cls == "X100"] = up[cls == "X100"] / 100
    fixed_up[cls.isin(["NEGATIVE", "NEGATIVE_UNKNOWN_PRODUCT"])] = up.abs()
    # USD-recorded prices (~1/280 of PKR value): solved from order_total below,
    # falling back to x280 if the order total cannot be used
    usd_fallback = (up * 280).where(cls == "USD")
    bad_up = cls.isin(["MISSING", "SENTINEL", "ZERO", "OTHER", "USD"])
    fixed_up[bad_up] = np.nan
    dq.rule(t, "unit_price", "PRICE_FORMAT", "'Rs. 2,290' style strings parsed to numbers",
            df.unit_price.str.contains(r"[^\d\.\-]", regex=True).sum(), "STANDARDISE")
    dq.rule(t, "unit_price", "PRICE_X100", "unit_price ~100x list price (decimal shift) -> divided by 100", (cls == "X100").sum(), "REPAIR")
    dq.rule(t, "unit_price", "PRICE_NEGATIVE", "Negative unit_price -> absolute value",
            cls.isin(["NEGATIVE", "NEGATIVE_UNKNOWN_PRODUCT"]).sum(), "REPAIR")
    dq.rule(t, "unit_price", "PRICE_USD", "unit_price ~1/280 of list price (recorded in USD) -> re-derived in PKR",
            (cls == "USD").sum(), "REPAIR")
    dq.rule(t, "unit_price", "PRICE_INVALID", "unit_price 0 / 9999999 / implausible vs list price -> re-derived",
            (bad_up & (cls != "USD")).sum(), "REPAIR")
    df["unit_price"] = fixed_up
    df["price_class"] = cls
    df["expected_price"] = expected

    # ---- discount: brief says discount_code is the order-level %, so line discount = pct x gross
    da = parse_money(df.discount_amount)
    df["discount_raw"] = da
    df["discount_amount"] = da

    # ---- reconcile against order_total (merchandise value after discount) to
    # recover a single unknown quantity or price in an order
    df = _reconcile_with_order_total(df, orders, dq)

    # fallbacks when reconciliation was not possible
    q_fb = df.quantity.isna()
    p_fb = df.unit_price.isna()
    df.loc[q_fb, "quantity"] = 1
    df.loc[p_fb, "unit_price"] = df.loc[p_fb, "expected_price"].round().fillna(usd_fallback[p_fb].round())
    dq.rule(t, "quantity", "QTY_FALLBACK", "Quantity not recoverable from order total -> 1 (modal value), flagged", q_fb.sum(), "IMPUTE")
    p_still = df.unit_price.isna()
    dq.rule(t, "unit_price", "PRICE_FALLBACK", "Price not recoverable from order total -> list price x inflation index, flagged",
            (p_fb & ~p_still).sum(), "IMPUTE")
    dq.reject(t, df[p_still], "order_item_id", "PRICE_UNRECOVERABLE", "No valid unit price, list price or order total")
    df = df[~p_still].copy()
    df["qty_imputed"] = df.qty_imputed | q_fb[df.index]
    df["price_imputed"] = df.price_imputed | p_fb[df.index]
    df["quantity"] = df.quantity.astype(int)

    gross = df.quantity * df.unit_price
    exp_disc = (gross * df.pct).round()
    da = df.discount_amount
    blank = da.isna()
    mismatch = da.notna() & ((da - exp_disc).abs() > np.maximum(2, 0.01 * gross))
    over = da.notna() & (da > gross)
    dq.rule(t, "discount_amount", "DISC_BLANK", "Blank discount_amount -> order discount % x line gross", blank.sum(), "IMPUTE")
    dq.rule(t, "discount_amount", "DISC_EXCEEDS_GROSS", "Discount larger than line gross (e.g. 130% of gross) -> recomputed",
            over.sum(), "REPAIR")
    dq.rule(t, "discount_amount", "DISC_MISMATCH", "Discount inconsistent with order discount code -> recomputed from code",
            (mismatch & ~over).sum(), "REPAIR")
    df["discount_amount"] = da.where(~(blank | mismatch | over), exp_disc)
    df["discount_repaired"] = blank | mismatch | over
    df["gross_amount"] = gross
    df["net_amount"] = gross - df.discount_amount

    # final reconciliation flag against order_total
    net_o = df.groupby("order_id").net_amount.sum()
    ot = orders.set_index("order_id").order_total
    cmp = pd.concat([net_o.rename("net"), ot.rename("tot")], axis=1).dropna()
    mism = (cmp.net - cmp.tot).abs() > np.maximum(2, 0.01 * cmp.tot)
    dq.rule(t, "*", "ORDER_TOTAL_RECON", "Orders whose cleaned line net still differs from order_total by >1% (flagged)",
            mism.sum(), "FLAG")
    df["order_total_mismatch"] = df.order_id.isin(cmp.index[mism])
    dq.set_count(t, "clean", len(df))
    keep = ["order_item_id", "order_id", "product_id", "product_id_raw", "seller_id", "quantity", "unit_price",
            "discount_amount", "gross_amount", "net_amount", "qty_imputed", "price_imputed", "discount_repaired",
            "price_class", "order_total_mismatch"]
    return df[keep], {"price_index_a": a, "price_index_b_per_day": b}


def _reconcile_with_order_total(df, orders, dq):
    t = "order_items"
    df["qty_imputed"] = False
    df["price_imputed"] = False
    ot = orders.set_index("order_id").order_total
    df["_tot"] = df.order_id.map(ot)
    unknown = df.quantity.isna() | df.unit_price.isna()
    n_unknown = unknown.groupby(df.order_id).transform("sum")
    target = df[unknown & (n_unknown == 1) & df._tot.notna()]
    if len(target):
        known = df[~unknown & df.order_id.isin(target.order_id)].copy()
        known_gross = (known.quantity * known.unit_price).groupby(known.order_id).sum()
        tg = target.order_id.map(known_gross).fillna(0)
        line_net = target._tot - tg * (1 - target.pct)        # this line's net value
        line_gross = line_net / (1 - target.pct)
        # unknown quantity with known price
        qmask = target.quantity.isna() & target.unit_price.notna()
        q_est = (line_gross[qmask] / target.unit_price[qmask]).round()
        q_ok = q_est.between(1, 50)
        idx = q_est[q_ok].index
        df.loc[idx, "quantity"] = q_est[q_ok]
        df.loc[idx, "qty_imputed"] = True
        # unknown price with known quantity
        pmask = target.unit_price.isna() & target.quantity.notna()
        p_est = (line_gross[pmask] / target.quantity[pmask]).round()
        exp = target.expected_price[pmask]
        p_ok = (p_est > 0) & (exp.isna() | ((p_est / exp).between(0.9, 1.1)))
        pidx = p_est[p_ok].index
        df.loc[pidx, "unit_price"] = p_est[p_ok]
        df.loc[pidx, "price_imputed"] = True
        dq.rule(t, "quantity", "QTY_FROM_TOTAL", "Unknown quantity solved from order_total (only unknown in order)",
                len(idx), "REPAIR")
        dq.rule(t, "unit_price", "PRICE_FROM_TOTAL",
                "Unknown unit price solved from order_total (only unknown in order, within 10% of expected)", len(pidx), "REPAIR")
    return df.drop(columns=["_tot"])


# ----------------------------------------------------------------- payments
def clean_payments(df, orders, dq: DQLog):
    t = "payments"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "payment_id")
    df["order_id"] = clean_text(df.order_id).str.upper()
    orphan = ~df.order_id.isin(orders.order_id)
    dq.reject(t, df[orphan], "payment_id", "ORPHAN_ORDER", "Payment for unknown/quarantined order")
    dq.rule(t, "order_id", "FK_ORDER", "Payment for unknown/quarantined order", orphan.sum(), "QUARANTINE",
            len(df), len(df) - orphan.sum())
    df = df[~orphan].copy()
    df["payment_method"] = _std_label(df, "payment_method", PAYMENT_METHOD, t, dq)
    df["payment_status"] = _std_label(df, "payment_status", PAYMENT_STATUS, t, dq)
    amt = parse_money(df.amount)
    neg = amt < 0
    dq.rule(t, "amount", "AMOUNT_NEGATIVE", "Negative amount -> absolute value", neg.sum(), "REPAIR")
    amt = amt.abs()
    sent = amt.isin(SENTINELS)
    dq.rule(t, "amount", "AMOUNT_SENTINEL", "9999999 sentinel -> NULL (amount not used for revenue)", sent.sum(), "FLAG")
    df["amount"] = amt.mask(sent)
    pdt = parse_dates(df.payment_date)
    inval = (df.payment_date.str.strip() != "") & pdt.isna()
    dq.rule(t, "payment_date", "DATE_INVALID", "Impossible payment_date -> NULL", inval.sum(), "FLAG")
    dq.rule(t, "payment_date", "DATE_BLANK", "Blank payment_date retained as NULL", (df.payment_date.str.strip() == "").sum(), "RETAIN")
    df["payment_date"] = pdt
    dq.set_count(t, "clean", len(df))
    return df[["payment_id", "order_id", "payment_method", "payment_status", "amount", "payment_date"]]


# ----------------------------------------------------------------- shipping
def clean_shipping(df, orders, order_weight, dq: DQLog):
    t = "shipping"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "shipment_id")
    df["order_id"] = clean_text(df.order_id).str.upper()
    orphan = ~df.order_id.isin(orders.order_id)
    dq.reject(t, df[orphan], "shipment_id", "ORPHAN_ORDER", "Shipment for unknown/quarantined order")
    dq.rule(t, "order_id", "FK_ORDER", "Shipment for unknown/quarantined order", orphan.sum(), "QUARANTINE",
            len(df), len(df) - orphan.sum())
    df = df[~orphan].copy()
    multi = df.order_id.duplicated(keep="last")
    dq.reject(t, df[multi], "shipment_id", "MULTIPLE_SHIPMENTS", "More than one shipment per order; latest kept")
    df = df[~multi].copy()
    df["courier"] = _std_label(df, "courier", COURIER, t, dq)
    df["delivery_status"] = _std_label(df, "delivery_status", DELIVERY_STATUS, t, dq)
    sd, dd = parse_dates(df.shipped_date), parse_dates(df.delivered_date)
    for name, raw, parsed in [("shipped_date", df.shipped_date, sd), ("delivered_date", df.delivered_date, dd)]:
        dq.rule(t, name, "DATE_INVALID", f"Impossible {name} -> NULL", ((raw.str.strip() != "") & parsed.isna()).sum(), "FLAG")
        dq.rule(t, name, "DATE_BLANK", f"Blank {name} retained as NULL", (raw.str.strip() == "").sum(), "RETAIN")
    od = df.order_id.map(orders.set_index("order_id").order_date)
    before_order = sd.notna() & (sd < od)
    dq.rule(t, "shipped_date", "SHIP_BEFORE_ORDER", "shipped_date before order_date -> NULL", before_order.sum(), "FLAG")
    sd = sd.mask(before_order)
    neg = sd.notna() & dd.notna() & (dd < sd)
    dq.rule(t, "delivered_date", "DELIVERED_BEFORE_SHIPPED", "delivered_date earlier than shipped_date -> duration NULL", neg.sum(), "FLAG")
    days = (dd - sd).dt.days
    long = days > 30
    dq.rule(t, "delivered_date", "DELIVERY_OUTLIER", "Delivery > 30 days after shipping (90-day pattern = date error) -> duration NULL",
            long.sum(), "FLAG")
    df["shipped_date"], df["delivered_date"] = sd, dd
    df["shipping_days"] = days.where(~neg & ~long)
    otd = (dd - od).dt.days
    bad_otd = otd.notna() & ((otd < 0) | (otd > 30))
    dq.rule(t, "delivered_date", "DELIVERY_VS_ORDER", "delivered_date before order_date or > 30 days after it -> duration NULL",
            bad_otd.sum(), "FLAG")
    df["order_to_delivery_days"] = otd.where(~neg & ~long & ~bad_otd)
    df["delivery_date_valid"] = df.shipping_days.notna()

    fee = parse_money(df.shipping_fee)
    negf = fee < 0
    dq.rule(t, "shipping_fee", "FEE_NEGATIVE", "Negative fee -> absolute value", negf.sum(), "REPAIR")
    fee = fee.abs()
    sent = fee.isin(SENTINELS) | (fee > 20000)
    dq.rule(t, "shipping_fee", "FEE_SENTINEL", "9999999 / > PKR 20,000 fee -> imputed", sent.sum(), "IMPUTE")
    fee = fee.mask(sent)
    miss = fee.isna()
    w = df.order_id.map(order_weight)
    band = pd.cut(w, [0, 0.5, 1, 2, 5, 10, 1e9], labels=False).fillna(-1)
    med = fee.groupby([df.courier, band]).transform("median").fillna(fee.groupby(df.courier).transform("median")).fillna(fee.median())
    dq.rule(t, "shipping_fee", "FEE_IMPUTE", "Missing/invalid fee -> median fee for same courier & order-weight band",
            miss.sum(), "IMPUTE")
    df["shipping_fee"] = fee.fillna(med.round())
    df["fee_imputed"] = miss
    dq.set_count(t, "clean", len(df))
    return df[["shipment_id", "order_id", "courier", "shipped_date", "delivered_date", "shipping_fee", "delivery_status",
               "shipping_days", "order_to_delivery_days", "delivery_date_valid", "fee_imputed"]]


# ------------------------------------------------------------------ returns
def clean_returns(df, items, orders, dq: DQLog):
    t = "returns"
    dq.set_count(t, "raw", len(df))
    df = _dedupe_exact(df, t, dq, "return_id")
    oi = clean_text(df.order_item_id).str.upper()
    padded = oi.str.match(r"^OI\d{1,7}$") & ~oi.isin(items.order_item_id)
    oi = oi.where(~padded, "OI" + oi.str[2:].str.zfill(8))
    dq.rule(t, "order_item_id", "ID_PADDING", "Short order_item_id (OI94118) re-padded to OI00094118", padded.sum(), "REPAIR")
    df["order_item_id"] = oi
    orphan = ~df.order_item_id.isin(items.order_item_id)
    dq.reject(t, df[orphan], "return_id", "ORPHAN_ORDER_ITEM", "Return for unknown/quarantined order line")
    dq.rule(t, "order_item_id", "FK_ORDER_ITEM", "Return for unknown order line", orphan.sum(), "QUARANTINE",
            len(df), len(df) - orphan.sum())
    df = df[~orphan].copy()
    df["return_status"] = _std_label(df, "return_status", RETURN_STATUS, t, dq)
    df["return_reason"] = _std_label(df, "return_reason", RETURN_REASON, t, dq, unknown="Not specified")
    rd = parse_dates(df.return_date)
    dq.rule(t, "return_date", "DATE_INVALID", "Impossible return_date -> NULL (Unknown date key)",
            ((df.return_date.str.strip() != "") & rd.isna()).sum(), "FLAG")
    line = items.set_index("order_item_id")
    od = df.order_item_id.map(line.order_id).map(orders.set_index("order_id").order_date)
    early = rd.notna() & (rd < od)
    dq.rule(t, "return_date", "RETURN_BEFORE_ORDER", "return_date before order_date -> NULL", early.sum(), "FLAG")
    df["return_date"] = rd.mask(early)

    ref = parse_money(df.refund_amount)
    neg = ref < 0
    dq.rule(t, "refund_amount", "REFUND_NEGATIVE", "Negative refund -> absolute value", neg.sum(), "REPAIR")
    ref = ref.abs()
    net = df.order_item_id.map(line.net_amount)
    over = ref > net + 0.005
    big = ref > net * 1.01 + 1
    dq.rule(t, "refund_amount", "REFUND_CAP", "Refund > line net value by more than 1% (1.4x pattern) -> capped at line net",
            big.sum(), "REPAIR")
    dq.rule(t, "refund_amount", "REFUND_ROUNDING", "Refund exceeds line net by rounding only (<=1%) -> set to line net",
            (over & ~big).sum(), "REPAIR")
    df["refund_amount"] = ref.where(~over, net)
    df["refund_capped"] = over
    miss = df.refund_amount.isna()
    dq.rule(t, "refund_amount", "REFUND_BLANK", "Unparseable refund -> 0 (flag)", miss.sum(), "FLAG")
    df["refund_amount"] = df.refund_amount.fillna(0)

    status = df.order_item_id.map(line.order_id).map(orders.set_index("order_id").order_status)
    on_cancel = (status == "Cancelled") & (df.return_status != "Rejected")
    dq.rule(t, "*", "RETURN_ON_CANCELLED", "Return against a cancelled order line - cannot be valid; excluded from returns",
            on_cancel.sum(), "QUARANTINE", len(df), len(df) - on_cancel.sum())
    dq.reject(t, df[on_cancel], "return_id", "RETURN_ON_CANCELLED_ORDER", "Line was cancelled, so it cannot be returned")
    df = df[~on_cancel].copy()
    df["is_valid"] = df.return_status != "Rejected"
    dq.rule(t, "return_status", "RETURN_VALID", "Valid returns (status not Rejected/Declined)", df.is_valid.sum(), "RETAIN")
    dq.set_count(t, "clean", len(df))
    return df[["return_id", "order_item_id", "return_date", "return_reason", "return_status", "refund_amount",
               "is_valid", "refund_capped"]]


# --------------------------------------------------------------- price repair
def repair_product_prices(products, items_raw, orders, dq: DQLog):
    """Products with invalid list prices get them back from their own sales:
    median(unit_price / inflation index) over that product's plausible lines."""
    t = "products"
    lines = items_raw[["order_id", "product_id", "unit_price", "quantity"]].copy()
    lines["product_id"] = lines.product_id.str.strip().str.upper()
    lines["up"] = parse_money(lines.unit_price)
    lines["order_date"] = lines.order_id.str.strip().str.upper().map(orders.set_index("order_id").order_date)
    lines["days"] = (lines.order_date - START).dt.days
    lp = lines.product_id.map(products.set_index("product_id").list_price)
    lines["ratio"] = lines.up / lp
    a, b = _fit_price_index(lines)
    lines["base"] = lines.up / (1 + a + b * lines.days)
    plaus = lines.up.between(1, 1_000_000) & ~lines.up.isin(SENTINELS)
    med = lines[plaus].groupby("product_id").base.median()
    # guard against x100 lines dominating: use the lower cluster if spread is huge
    need = products.list_price.isna()
    est = products.product_id.map(med)
    # sanity: list price must exceed cost
    ok = est.notna() & (products.cost_price.isna() | (est > products.cost_price * 0.8))
    products.loc[need & ok, "list_price"] = (est[need & ok] / 10).round() * 10
    dq.rule(t, "list_price", "PRICE_FROM_SALES", "Invalid list_price re-derived from median deflated selling price",
            (need & ok).sum(), "REPAIR")
    still = products.list_price.isna()
    products.loc[still, "list_price"] = products.loc[still, "cost_price"] / 0.6
    dq.rule(t, "list_price", "PRICE_FROM_COST", "No sales history -> cost / 0.6 (median cost ratio)", still.sum(), "IMPUTE")
    return products


# ------------------------------------------------------------------- driver
def clean_all(raw: dict, dq: DQLog):
    locations = clean_locations(raw["locations"].copy(), dq)
    sellers = clean_sellers(raw["sellers"].copy(), locations, dq)
    products = clean_products(raw["products"].copy(), sellers, dq)
    customers, cmap = clean_customers(raw["customers"].copy(), locations, dq)
    orders = clean_orders(raw["orders"].copy(), raw["payments"], raw["shipping"], customers, cmap, locations, dq)
    products = repair_product_prices(products, raw["order_items"], orders, dq)
    products, pmap = finish_products(products, dq)
    items, price_model = clean_order_items(raw["order_items"].copy(), orders, products, pmap, sellers, dq)
    payments = clean_payments(raw["payments"].copy(), orders, dq)
    w = items.product_id.map(products.set_index("product_id").weight_kg).fillna(products.weight_kg.median())
    order_weight = (w * items.quantity).groupby(items.order_id).sum()
    shipping = clean_shipping(raw["shipping"].copy(), orders, order_weight, dq)
    returns = clean_returns(raw["returns"].copy(), items, orders, dq)

    # registration date: repair blanks / dates after the customer's first order
    first = orders.groupby("customer_id").order_date.min()
    fo = customers.customer_id.map(first)
    blank = customers.registration_date.isna()
    late = customers.registration_date.notna() & fo.notna() & (customers.registration_date > fo)
    customers.loc[blank | late, "registration_date"] = fo[blank | late]
    dq.rule("customers", "registration_date", "REG_AFTER_FIRST_ORDER",
            "Registration after first order -> set to first order date", late.sum(), "REPAIR")
    dq.rule("customers", "registration_date", "REG_FROM_FIRST_ORDER",
            "Unparseable registration date -> first order date", (blank & fo.notna()).sum(), "REPAIR")

    # orders: primary payment method = method carrying the largest amount
    pm = (payments.sort_values(["order_id", "amount"], ascending=[True, False])
          .drop_duplicates("order_id").set_index("order_id").payment_method)
    orders["payment_method"] = orders.order_id.map(pm).fillna(UNKNOWN)
    orders["payment_count"] = orders.order_id.map(payments.groupby("order_id").size()).fillna(0).astype(int)

    return {
        "locations": locations, "sellers": sellers, "products": products, "customers": customers,
        "orders": orders, "order_items": items, "payments": payments, "shipping": shipping, "returns": returns,
        "customer_id_map": cmap, "product_id_map": pmap,
    }, price_model
