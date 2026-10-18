"""Business-question analysis on the WAREHOUSE (not the raw CSVs).

Reads dw.fact_sales + dimensions, applies the brief's metric definitions and
writes every answer to outputs/ (CSV tables, answers.json, charts/*.png).

    python scripts/analysis.py
"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nexusmart_etl.db import Database  # noqa: E402

OUT = ROOT / "outputs"
TAB = OUT / "tables"
TAB.mkdir(parents=True, exist_ok=True)
db = Database()


def q(sql):
    return db.query(sql)


def bools(df):
    for c in df.columns:
        if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and df[c].notna().any() and set(df[c].dropna().astype(str).str.lower().unique()) <= {"t", "f", "true", "false"}:
            df[c] = df[c].astype(str).str.lower().isin(["t", "true"])
    return df


f = bools(q("""
SELECT f.*, d.full_date, d.year_month, d.event_name, d.is_festival_period,
       p.product_id, p.product_name, p.category, s.seller_id, s.seller_name, s.seller_tier,
       l.city, l.province, l.region, l.population_tier,
       c.customer_id, c.customer_segment, c.region AS cust_region, c.order_count AS cust_orders
FROM dw.fact_sales f
JOIN dw.dim_date d ON d.date_key = f.date_key
JOIN dw.dim_product p ON p.product_key = f.product_key
JOIN dw.dim_seller s ON s.seller_key = f.seller_key
JOIN dw.dim_location l ON l.location_key = f.location_key
JOIN dw.dim_customer c ON c.customer_key = f.customer_key"""))
for c in ["gross_amount", "discount_amount", "net_amount", "cost_amount", "shipping_fee", "returned_amount", "unit_price"]:
    f[c] = f[c].astype(float)
f["full_date"] = pd.to_datetime(f.full_date)
f["nmv"] = f.net_amount.where(~f.is_cancelled, 0)
f["net_rev"] = f.nmv - f.returned_amount
f["cogs_kept"] = f.cost_amount.where(f.is_kept_line, 0)
f["margin"] = f.net_rev - f.cogs_kept

A = {}


def metrics(g):
    nmv = g.nmv.sum()
    nr = g.net_rev.sum()
    deliv = g.is_delivered_line.sum()
    orders = g.order_id.nunique()
    canc = g.loc[g.is_cancelled, "order_id"].nunique()
    nc_orders = g.loc[~g.is_cancelled, "order_id"].nunique()
    return pd.Series({
        "gross_sales": g.gross_amount.sum(), "discounts": g.discount_amount.sum(), "nmv": nmv,
        "refunds": g.returned_amount.sum(), "net_revenue": nr, "cogs_kept": g.cogs_kept.sum(),
        "margin": g.margin.sum(), "margin_pct": g.margin.sum() / nr if nr else np.nan,
        "shipping_cost": g.shipping_fee.sum(),
        "orders": orders, "non_cancelled_orders": nc_orders, "aov": nmv / nc_orders if nc_orders else np.nan,
        "units": g.quantity.where(~g.is_cancelled, 0).sum(),
        "lines": len(g), "delivered_lines": deliv, "returned_lines": g.is_returned.sum(),
        "return_rate": g.is_returned.sum() / deliv if deliv else np.nan,
        "cancellation_rate": canc / orders if orders else np.nan,
        "active_customers": g.loc[~g.is_cancelled & (g.customer_key != -1), "customer_key"].nunique(),
    })


def save(df, name):
    df.to_csv(TAB / f"{name}.csv")
    return df


# ---------------------------------------------------------------- overview
ov = metrics(f)
A["overview"] = ov.round(4).to_dict()
monthly = save(f.groupby("year_month").apply(metrics, include_groups=False), "monthly")
monthly["net_revenue_mom"] = monthly.net_revenue.pct_change()
monthly["nmv_mom"] = monthly.nmv.pct_change()
monthly["net_revenue_3m_ma"] = monthly.net_revenue.rolling(3).mean()
save(monthly, "monthly")
A["monthly"] = monthly[["gross_sales", "nmv", "net_revenue", "net_revenue_mom", "margin_pct", "orders",
                        "return_rate", "cancellation_rate", "active_customers"]].round(4).reset_index().to_dict(orient="records")
h1 = monthly.loc["2024-01":"2024-06"].net_revenue.sum(); h2 = monthly.loc["2025-01":"2025-06"].net_revenue.sum()
A["growth_h1_2025_vs_h1_2024"] = h2 / h1 - 1

# ------------------------------------------------------- Q1 category
cat = save(f.groupby("category").apply(metrics, include_groups=False).sort_values("net_revenue", ascending=False), "category")
cat["net_rev_share"] = cat.net_revenue / cat.net_revenue.sum()
cat["leakage_pct"] = 1 - cat.net_revenue / cat.gross_sales
cat["discount_pct_of_gross"] = cat.discounts / cat.gross_sales
cat["cancel_loss"] = cat.gross_sales - cat.discounts - cat.nmv
save(cat, "category")
A["category"] = cat[["gross_sales", "discounts", "cancel_loss", "nmv", "refunds", "net_revenue", "net_rev_share",
                     "margin", "margin_pct", "leakage_pct", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q2 geography
prov = save(f.groupby("province").apply(metrics, include_groups=False).sort_values("net_revenue", ascending=False), "province")
prov["share"] = prov.net_revenue / prov.net_revenue.sum()
city = f.groupby(["city", "province"]).apply(metrics, include_groups=False).sort_values("net_revenue", ascending=False)
city["share"] = city.net_revenue / city.net_revenue.sum()
save(city, "city")
region = f.groupby("region").apply(metrics, include_groups=False)
region["share"] = region.net_revenue / region.net_revenue.sum()
save(region, "region")
tier = save(f.groupby("population_tier").apply(metrics, include_groups=False), "population_tier")
tier["share"] = tier.net_revenue / tier.net_revenue.sum()
named = city.drop(index=[("Unknown", "Unknown")], errors="ignore")
A["province"] = prov[["net_revenue", "share", "orders", "margin_pct", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
A["top10_cities"] = named.head(10)[["net_revenue", "share", "orders"]].round(4).reset_index().to_dict(orient="records")
A["bottom5_cities"] = named[named.orders > 0].tail(5)[["net_revenue", "share", "orders"]].round(4).reset_index().to_dict(orient="records")
A["top5_city_share"] = float(named.head(5).net_revenue.sum() / city.net_revenue.sum())
A["cities_with_orders"] = int((named.orders > 0).sum())
A["top40_city_share"] = float(named.head(40).net_revenue.sum() / city.net_revenue.sum())
A["region"] = region[["net_revenue", "share", "orders", "margin_pct"]].round(4).reset_index().to_dict(orient="records")
A["population_tier"] = tier[["net_revenue", "share", "aov", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
A["unknown_location_net_rev_share"] = float(city.loc[("Unknown", "Unknown")].net_revenue / city.net_revenue.sum()) if ("Unknown", "Unknown") in city.index else 0
imp = f[f.dq_flags.fillna("").str.contains("SHIP_CITY_FROM_CUSTOMER")]
A["ship_city_imputed_net_rev_share"] = float(imp.net_rev.sum() / f.net_rev.sum())

# ------------------------------------------------------- Q4 returns/cancellations over time (in monthly)
q_rr = f.assign(q=f.full_date.dt.to_period("Q").astype(str)).groupby("q").apply(metrics, include_groups=False)
save(q_rr, "quarterly")
A["quarterly_rates"] = q_rr[["return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q6 customer value
cust = f[(f.customer_key != -1)]
cust_nc = cust[~cust.is_cancelled]
per = cust_nc.groupby("customer_key").agg(net_rev=("net_rev", "sum"), margin=("margin", "sum"), orders=("order_id", "nunique"),
                                          first=("full_date", "min"), last=("full_date", "max"),
                                          region=("cust_region", "first"), segment=("customer_segment", "first"))
A["clv"] = {
    "definition": "Observed CLV = net revenue (NMV - valid refunds) per active customer over Jan-2024..Jun-2025",
    "active_customers": int(len(per)), "mean_clv": float(per.net_rev.mean()), "median_clv": float(per.net_rev.median()),
    "mean_margin_per_customer": float(per.margin.mean()), "mean_orders": float(per.orders.mean()),
    "repeat_purchase_rate": float((per.orders >= 2).mean()),
    "top10pct_revenue_share": float(per.net_rev.nlargest(int(len(per) * 0.1)).sum() / per.net_rev.sum()),
    "avg_lifespan_days_repeaters": float((per[per.orders >= 2]["last"] - per[per.orders >= 2]["first"]).dt.days.mean()),
}
reg = per.groupby("region").agg(customers=("net_rev", "size"), clv=("net_rev", "mean"),
                                repeat_rate=("orders", lambda s: (s >= 2).mean()), orders_per_cust=("orders", "mean"))
A["clv_by_region"] = save(reg, "clv_by_region").round(4).reset_index().to_dict(orient="records")
# acquisition category = category of the largest line in the customer's first non-cancelled order
fo = cust_nc[cust_nc.is_new_customer_order].sort_values("net_amount", ascending=False).drop_duplicates("customer_key")
acq = fo.set_index("customer_key").category
per["acq_category"] = acq
cat_rep = per.groupby("acq_category").agg(customers=("net_rev", "size"), repeat_rate=("orders", lambda s: (s >= 2).mean()),
                                          clv=("net_rev", "mean")).sort_values("repeat_rate", ascending=False)
A["repeat_by_acquisition_category"] = save(cat_rep, "repeat_by_category").round(4).reset_index().to_dict(orient="records")
# also: share of category buyers who bought that category again in a later order
cb = cust_nc.groupby(["category", "customer_key"]).order_id.nunique()
cat_again = (cb >= 2).groupby(level=0).mean().sort_values(ascending=False)
A["category_rebuy_rate"] = save(cat_again.to_frame("rebuy_rate"), "category_rebuy").round(4).reset_index().to_dict(orient="records")

# cohorts: % of each first-order month cohort ordering again within 3 months
per["cohort"] = per["first"].dt.to_period("M")
orders_c = cust_nc.groupby(["customer_key", "order_id"]).full_date.min().reset_index()
orders_c["cohort"] = orders_c.customer_key.map(per.cohort)
orders_c["age_m"] = (orders_c.full_date.dt.to_period("M") - orders_c.cohort).apply(lambda x: x.n)
coh = orders_c.groupby(["cohort", "age_m"]).customer_key.nunique().unstack(fill_value=0)
coh_pct = coh.div(coh[0], axis=0)
save(coh_pct.round(4), "cohort_retention")
A["cohort_m1_retention_avg"] = float(coh_pct[1].iloc[:-1].mean())
A["cohort_m3_retention_avg"] = float(coh_pct[3].iloc[:-3].mean())

# new vs returning (orders)
ordr = f[~f.is_cancelled].groupby("order_id").agg(new=("is_new_customer_order", "max"), ym=("year_month", "first"),
                                                 nr=("net_rev", "sum"), ck=("customer_key", "first"))
nvr = ordr[ordr.ck != -1].groupby(["ym", "new"]).nr.sum().unstack()
nvr.columns = ["returning", "new"] if list(nvr.columns) == [False, True] else nvr.columns
save(nvr, "new_vs_returning_monthly")
A["new_vs_returning_share"] = {"new": float(ordr[ordr.new].nr.sum() / ordr.nr.sum()),
                               "returning": float(ordr[~ordr.new & (ordr.ck != -1)].nr.sum() / ordr.nr.sum())}

# ------------------------------------------------------- Q7 RFM
seg = per.groupby("segment").agg(customers=("net_rev", "size"), net_revenue=("net_rev", "sum"), avg_clv=("net_rev", "mean"),
                                 avg_orders=("orders", "mean"))
seg["customer_share"] = seg.customers / seg.customers.sum()
seg["revenue_share"] = seg.net_revenue / seg.net_revenue.sum()
seg = save(seg.sort_values("net_revenue", ascending=False), "rfm_segments")
A["rfm_segments"] = seg.round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q8 seller quality score
sl = f[f.seller_key != -1]
sm = sl.groupby(["seller_id", "seller_name", "seller_tier"]).apply(metrics, include_groups=False)
ot = sl[sl.is_on_time.notna() & sl.is_delivered_line].groupby(["seller_id", "seller_name", "seller_tier"]).is_on_time.mean()
sm["on_time_rate"] = ot
sm["avg_delivery_days"] = sl.groupby(["seller_id", "seller_name", "seller_tier"]).order_to_delivery_days.mean()
elig = sm[sm.delivered_lines >= 100].copy()
pr = lambda s, asc=True: s.rank(pct=True, ascending=asc)  # noqa: E731
# score is computed once in the warehouse (sql/07_seller_score.sql) so the dashboard and this report agree:
# 30% revenue (percentile of log revenue) + 25% low return rate + 25% on-time rate + 20% low cancellation rate
wh = q("SELECT seller_id, quality_score FROM dw.dim_seller WHERE quality_score IS NOT NULL")
elig["score"] = elig.index.get_level_values("seller_id").map(dict(zip(wh.seller_id, wh.quality_score.astype(float))))
elig = elig.sort_values("score", ascending=False)
elig["rank"] = np.arange(1, len(elig) + 1)
save(elig, "seller_scores")
A["seller_score"] = {
    "weights": {"net_revenue (percentile of log)": 0.30, "return_rate (lower better)": 0.25,
                "on_time_rate": 0.25, "cancellation_rate (lower better)": 0.20},
    "eligible_sellers": int(len(elig)), "min_delivered_lines": 100,
    "top10": elig.head(10)[["net_revenue", "return_rate", "on_time_rate", "cancellation_rate", "score"]].round(4).reset_index().to_dict(orient="records"),
    "bottom10": elig.tail(10)[["net_revenue", "return_rate", "on_time_rate", "cancellation_rate", "score"]].round(4).reset_index().to_dict(orient="records"),
}
tier_s = sl.groupby("seller_tier").apply(metrics, include_groups=False)
tier_s["on_time_rate"] = sl[sl.is_delivered_line].groupby("seller_tier").is_on_time.mean()
tier_s["avg_delivery_days"] = sl.groupby("seller_tier").order_to_delivery_days.mean()
tier_s["score_mean"] = elig.groupby(level="seller_tier").score.mean()
A["seller_tier"] = save(tier_s, "seller_tier")[["net_revenue", "return_rate", "cancellation_rate", "on_time_rate", "avg_delivery_days", "score_mean"]].round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q9 delivery vs returns/cancellations
dl = f[f.is_delivered_line & f.is_on_time.notna()]
A["on_time"] = {
    "definition": "Delivered within 5 days of order date (median 4 days; 5 days covers ~3/4 of deliveries)",
    "on_time_rate_orders": float(f[f.is_delivered_line & f.is_on_time.notna()].groupby("order_id").is_on_time.min().mean()),
    "return_rate_on_time": float(dl[dl.is_on_time].is_returned.mean()),
    "return_rate_late": float(dl[~dl.is_on_time.astype(bool)].is_returned.mean()),
}
dd = dl.copy(); dd["days"] = dd.order_to_delivery_days.astype(float).clip(upper=12)
A["return_rate_by_delivery_days"] = dd.groupby("days").is_returned.agg(["mean", "size"]).round(4).reset_index().to_dict(orient="records")
lr = f[f.is_returned].return_reason.value_counts(normalize=True)
A["return_reasons"] = save(lr.to_frame("share"), "return_reasons").round(4).reset_index().to_dict(orient="records")
late_reason = dl[dl.is_returned].groupby(dl.is_on_time.astype(bool)).return_reason.apply(lambda s: (s == "Late delivery").mean())
A["late_delivery_reason_share"] = {"on_time": float(late_reason.get(True, np.nan)), "late": float(late_reason.get(False, np.nan))}
# seller-level correlation between on-time and cancellation / return
A["seller_corr"] = {"on_time_vs_cancellation": float(elig.on_time_rate.corr(elig.cancellation_rate)),
                    "on_time_vs_return": float(elig.on_time_rate.corr(elig.return_rate))}
# courier
cour = f[f.courier.notna()].groupby("courier").apply(metrics, include_groups=False)
cour["on_time_rate"] = dl.groupby("courier").is_on_time.mean()
cour["avg_delivery_days"] = f.groupby("courier").order_to_delivery_days.mean()
A["courier"] = save(cour, "courier")[["orders", "on_time_rate", "avg_delivery_days", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q10 product quality
pdm = f[f.product_key != -1].groupby(["product_id", "product_name", "category"]).apply(metrics, include_groups=False)
pdm = pdm[pdm.delivered_lines >= 30]
rr_all = ov.return_rate
hot = pdm[(pdm.net_revenue >= pdm.net_revenue.quantile(0.75)) & (pdm.return_rate >= 2 * rr_all)].sort_values("net_revenue", ascending=False)
save(pdm.sort_values("net_revenue", ascending=False), "products")
save(hot, "high_sales_high_return_products")
A["high_sales_high_return"] = {
    "rule": "top-quartile net revenue AND return rate >= 2x overall, min 30 delivered lines",
    "count": int(len(hot)), "products": hot.head(15)[["net_revenue", "delivered_lines", "return_rate"]].round(4).reset_index().to_dict(orient="records")}
# seller association: within these products, return rate by seller vs same products' other sellers
hp = f[f.product_id.isin(hot.index.get_level_values(0)) & f.is_delivered_line]
ps = hp.groupby(["product_id", "seller_id"]).agg(lines=("is_returned", "size"), rr=("is_returned", "mean"))
seller_conc = hp.groupby("seller_id").agg(lines=("is_returned", "size"), returns=("is_returned", "sum"))
seller_conc["rr"] = seller_conc.returns / seller_conc.lines
seller_conc["share_of_returns"] = seller_conc.returns / seller_conc.returns.sum()
seller_conc = seller_conc.sort_values("returns", ascending=False)
save(seller_conc, "hot_product_seller_concentration")
A["hot_products_seller_concentration"] = {
    "sellers_involved": int(len(seller_conc)),
    "top5_sellers_share_of_returns": float(seller_conc.share_of_returns.head(5).sum()),
    "top5": seller_conc.head(5).round(4).reset_index().to_dict(orient="records"),
    "sellers_per_product_median": float(ps.groupby(level=0).size().median()),
}
# all sellers: share of returns from worst-decile sellers
sr = sm[sm.delivered_lines >= 100].sort_values("return_rate", ascending=False)
A["seller_return_rate_spread"] = {"p10": float(sr.return_rate.quantile(0.1)), "median": float(sr.return_rate.median()),
                                  "p90": float(sr.return_rate.quantile(0.9)), "max": float(sr.return_rate.max())}

# ------------------------------------------------------- Q11 seasonality
daily = f.groupby(["full_date", "event_name"]).apply(metrics, include_groups=False).reset_index()
ev = daily.groupby("event_name").agg(days=("full_date", "nunique"), net_rev_per_day=("net_revenue", "mean"),
                                     orders_per_day=("orders", "mean"))
evm = f.groupby("event_name").apply(metrics, include_groups=False)
ev = ev.join(evm[["return_rate", "cancellation_rate", "aov"]])
ev["uplift_vs_normal"] = ev.net_rev_per_day / ev.loc["Normal day", "net_rev_per_day"] - 1
# compare festivals against the 28 days before them (same season, controls for growth)
def window(start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    cur = f[f.full_date.between(s, e)]
    pre = f[f.full_date.between(s - pd.Timedelta(days=28), s - pd.Timedelta(days=1))]
    mc, mp = metrics(cur), metrics(pre)
    nd = (e - s).days + 1
    return {"days": nd, "net_rev_per_day": mc.net_revenue / nd, "pre28_net_rev_per_day": mp.net_revenue / 28,
            "uplift_vs_prior_28d": (mc.net_revenue / nd) / (mp.net_revenue / 28) - 1,
            "return_rate": mc.return_rate, "pre_return_rate": mp.return_rate,
            "cancellation_rate": mc.cancellation_rate, "pre_cancellation_rate": mp.cancellation_rate,
            "aov": mc.aov, "pre_aov": mp.aov}
events = {
    "Ramadan 2024": ("2024-03-11", "2024-04-09"), "Eid-ul-Fitr 2024": ("2024-04-10", "2024-04-12"),
    "Eid-ul-Adha 2024": ("2024-06-17", "2024-06-19"), "14 Aug 2024": ("2024-08-14", "2024-08-14"),
    "11.11 2024": ("2024-11-11", "2024-11-11"), "Black Friday 2024": ("2024-11-29", "2024-11-29"),
    "Ramadan 2025": ("2025-03-01", "2025-03-30"), "Eid-ul-Fitr 2025": ("2025-03-31", "2025-04-02"),
    "Eid-ul-Adha 2025": ("2025-06-07", "2025-06-09"),
}
evw = pd.DataFrame({k: window(*v) for k, v in events.items()}).T
save(evw, "festival_windows")
save(ev, "event_summary")
A["event_summary"] = ev.round(4).reset_index().to_dict(orient="records")
A["festival_windows"] = evw.astype(float).round(4).reset_index().to_dict(orient="records")
# day-of-week
dow = daily.assign(dow=daily.full_date.dt.day_name()).groupby("dow").net_revenue.mean()
A["avg_net_rev_by_weekday"] = dow.round(0).to_dict()

# ------------------------------------------------------- Q12 discounts
o = f.groupby("order_id").agg(pct=("discount_pct", "first"), canc=("is_cancelled", "max"), nmv=("nmv", "sum"),
                              gross=("gross_amount", "sum"), nr=("net_rev", "sum"), margin=("margin", "sum"),
                              ym=("year_month", "first"), cat=("category", "first"),
                              lines=("order_item_id", "size"))
o["band"] = pd.cut(o.pct, [-1, 0, 10, 20, 30, 50], labels=["0%", "5-10%", "15-20%", "25-30%", "40-50%"])
dsc = f.assign(band=f.order_id.map(o.band)).groupby("band", observed=True).apply(metrics, include_groups=False)
dsc["order_share"] = dsc.orders / dsc.orders.sum()
dsc["gross_per_order"] = dsc.gross_sales / dsc.orders
dsc["discount_rate"] = dsc.discounts / dsc.gross_sales
save(dsc, "discount_bands")
A["discount_bands"] = dsc[["orders", "order_share", "gross_per_order", "aov", "discount_rate", "net_revenue", "margin_pct",
                           "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
mdisc = f.groupby("year_month").apply(lambda g: pd.Series({
    "discount_rate": g.discounts.sum() / g.gross_amount.sum() if False else g.discount_amount.sum() / g.gross_amount.sum(),
    "discounted_order_share": g.loc[g.discount_pct > 0, "order_id"].nunique() / g.order_id.nunique(),
    "avg_pct_when_discounted": g.loc[g.discount_pct > 0].groupby("order_id").discount_pct.first().mean()}), include_groups=False)
save(mdisc, "discount_monthly")
A["discount_monthly"] = mdisc.round(4).reset_index().to_dict(orient="records")

# ------------------------------------------------------- Q13 delivery times by region / province
gp = f.groupby("province").apply(metrics, include_groups=False)
gp["avg_delivery_days"] = f.groupby("province").order_to_delivery_days.mean()
gp["on_time_rate"] = dl.groupby("province").is_on_time.mean()
gp = save(gp.sort_values("avg_delivery_days"), "province_delivery")
A["province_delivery"] = gp[["orders", "avg_delivery_days", "on_time_rate", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
gpv = gp[gp.orders >= 1000]
A["province_corr"] = {"delivery_days_vs_cancellation": float(gpv.avg_delivery_days.corr(gpv.cancellation_rate)),
                      "delivery_days_vs_return": float(gpv.avg_delivery_days.corr(gpv.return_rate))}
rg = f.groupby("region").apply(metrics, include_groups=False)
rg["avg_delivery_days"] = f.groupby("region").order_to_delivery_days.mean()
rg["on_time_rate"] = dl.groupby("region").is_on_time.mean()
A["region_delivery"] = save(rg, "region_delivery")[["orders", "avg_delivery_days", "on_time_rate", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
ct = f.groupby("city").apply(metrics, include_groups=False)
ct["avg_delivery_days"] = f.groupby("city").order_to_delivery_days.mean()
ctv = ct[ct.orders >= 500]
A["city_corr"] = {"n_cities": int(len(ctv)), "delivery_days_vs_cancellation": float(ctv.avg_delivery_days.corr(ctv.cancellation_rate)),
                  "delivery_days_vs_return": float(ctv.avg_delivery_days.corr(ctv.return_rate))}

# ------------------------------------------------------- Q14 payment methods
pm = f.groupby("payment_method").apply(metrics, include_groups=False)
pm["order_share"] = pm.orders / pm.orders.sum()
A["payment_methods"] = save(pm, "payment_methods")[["orders", "order_share", "aov", "return_rate", "cancellation_rate"]].round(4).reset_index().to_dict(orient="records")
pay = bools(q("SELECT order_id, payment_method, payment_status, amount, payment_date FROM staging.payments"))
po = q("SELECT order_id, order_status, payment_method AS primary_method, payment_count FROM staging.orders")
mult = po[po.payment_count.astype(int) > 1]
A["payment_dq"] = {
    "orders_with_split_payments": int(len(mult)),
    "split_orders_with_mixed_methods": int(pay[pay.order_id.isin(mult.order_id)].groupby("order_id").payment_method.nunique().gt(1).sum()),
    "payment_status_by_order_status": pd.crosstab(pay.order_id.map(po.set_index("order_id").order_status), pay.payment_status).to_dict(),
    "payments_missing_date_share_by_method": pay.groupby("payment_method").payment_date.apply(lambda s: s.isna().mean()).round(4).to_dict(),
}
# COD vs prepaid cancellations split by whether the order was shipped
shp = q("SELECT order_id, delivery_status FROM staging.shipping")
oc = po.merge(shp, on="order_id", how="left")
oc["prepaid"] = oc.primary_method != "COD"
oc["cancelled"] = oc.order_status == "Cancelled"
oc["shipped_then_cancelled"] = oc.cancelled & oc.delivery_status.eq("Returned to Seller")
A["cancellation_type_by_method"] = oc.groupby("primary_method").agg(
    cancel_rate=("cancelled", "mean"), cancelled_after_shipping=("shipped_then_cancelled", "mean")).round(4).to_dict(orient="index")
A["cancellation_shipping_waste"] = {
    "cancelled_orders_shipped_RTO": int(oc.shipped_then_cancelled.sum()),
    "shipping_cost_on_cancelled_orders": float(f[f.is_cancelled].shipping_fee.sum()),
    "shipping_cost_total": float(f.shipping_fee.sum())}

# ------------------------------------------------------- Q15 profitability drivers
mm = monthly.copy()
mm["discount_rate"] = mm.discounts / mm.gross_sales
mm["refund_rate"] = mm.refunds / mm.nmv
mm["cogs_ratio"] = mm.cogs_kept / mm.net_revenue
mm["shipping_ratio"] = mm.shipping_cost / mm.net_revenue
mm["contribution_after_shipping"] = mm.margin - mm.shipping_cost
mm["contribution_pct"] = mm.contribution_after_shipping / mm.net_revenue
save(mm, "profitability_monthly")
A["profitability_monthly"] = mm[["net_revenue", "margin", "margin_pct", "discount_rate", "refund_rate", "cogs_ratio",
                                 "shipping_ratio", "contribution_after_shipping", "contribution_pct"]].round(4).reset_index().to_dict(orient="records")
# category mix vs within-category margin change: H1-2024 vs H1-2025
def half(df):
    return df.groupby("category").apply(lambda g: pd.Series({"nr": g.net_rev.sum(), "m": g.margin.sum(),
                                                            "disc": g.discount_amount.sum() / g.gross_amount.sum()}), include_groups=False)
a, b = half(f[f.full_date < "2024-07-01"]), half(f[f.full_date >= "2025-01-01"])
mix = pd.DataFrame({"share_h1_24": a.nr / a.nr.sum(), "share_h1_25": b.nr / b.nr.sum(),
                    "margin_pct_h1_24": a.m / a.nr, "margin_pct_h1_25": b.m / b.nr,
                    "disc_rate_h1_24": a.disc, "disc_rate_h1_25": b.disc})
save(mix, "category_mix_h1")
tot_a, tot_b = a.m.sum() / a.nr.sum(), b.m.sum() / b.nr.sum()
mix_effect = ((mix.share_h1_25 - mix.share_h1_24) * mix.margin_pct_h1_24).sum()
rate_effect = (mix.share_h1_25 * (mix.margin_pct_h1_25 - mix.margin_pct_h1_24)).sum()
A["margin_bridge_h1_2024_to_h1_2025"] = {"margin_pct_h1_2024": tot_a, "margin_pct_h1_2025": tot_b,
                                         "mix_effect_pp": mix_effect, "within_category_effect_pp": rate_effect}
A["category_mix_h1"] = mix.round(4).reset_index().to_dict(orient="records")

(OUT / "answers.json").write_text(json.dumps(A, indent=2, default=lambda x: float(x) if isinstance(x, (np.floating, np.integer)) else str(x)))
print("written", OUT / "answers.json")
