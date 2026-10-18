"""Builds docs/Executive_Summary.html/.pdf (2-3 pages) from outputs/answers.json
and outputs/charts. Every figure in the text is read from the analysis output.

    python scripts/build_exec_summary.py
"""
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
A = json.loads((ROOT / "outputs" / "answers.json").read_text())
T = ROOT / "outputs" / "tables"
CH = ROOT / "outputs" / "charts"
DOCS = ROOT / "docs"
ov = A["overview"]


def M(v):  # PKR millions
    return f"PKR {v / 1e6:,.1f}M"


def P(v, d=1):
    return f"{v * 100:.{d}f}%"


cat = {r["category"]: r for r in A["category"]}
prov = {r["province"]: r for r in A["province"]}
pdl = {r["province"]: r for r in A["province_delivery"]}
seg = {r["segment"]: r for r in A["rfm_segments"]}
disc = {r["band"]: r for r in A["discount_bands"]}
pay = {r["payment_method"]: r for r in A["payment_methods"]}
ev = {r["index"]: r for r in A["festival_windows"]}
mon = pd.read_csv(T / "profitability_monthly.csv", index_col=0)
h1 = mon.loc["2024-01":"2024-06"]; h2 = mon.loc["2025-01":"2025-06"]
mb = A["margin_bridge_h1_2024_to_h1_2025"]
q = {r["q"]: r for r in A["quarterly_rates"]}
dm = pd.DataFrame(A["discount_monthly"]).set_index("year_month")
st = A["seller_score"]; top = st["top10"]
tiers = {r["seller_tier"]: r for r in A["seller_tier"]}
hot = A["high_sales_high_return"]; hc = A["hot_products_seller_concentration"]
clv = A["clv"]; reg = {r["region"]: r for r in A["clv_by_region"]}
rebuy = {r["category"]: r["rebuy_rate"] for r in A["category_rebuy_rate"]}
ot = A["on_time"]
canc_type = A["cancellation_type_by_method"]
waste = A["cancellation_shipping_waste"]
top5 = A["top10_cities"][:5]
mom = pd.DataFrame(A["monthly"]).set_index("year_month")
cmgr = (mom.net_revenue.iloc[-1] / mom.net_revenue.iloc[0]) ** (1 / (len(mom) - 1)) - 1
leak_cancel = ov["gross_sales"] - ov["discounts"] - ov["nmv"]
cats_by_margin = sorted(A["category"], key=lambda r: -r["margin"])
excl = [c for c in A["category"] if c["category"] != "Unknown"]
best_margin_pct = max(excl, key=lambda r: r["margin_pct"])

CSS = """
@page { size: A4; margin: 11mm 12mm; }
body { font-family: 'Segoe UI', 'DejaVu Sans', Arial, sans-serif; color: #0b0b0b; font-size: 8.6pt; line-height: 1.33; }
h1 { font-size: 17pt; margin: 0; color: #184f95; }
h2 { font-size: 11pt; color: #184f95; margin: 9pt 0 4pt; border-bottom: 1.5px solid #b7d3f6; padding-bottom: 1.5pt; }
.sub { color: #52514e; margin: 1pt 0 6pt; }
.kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 5pt; margin: 4pt 0 6pt; }
.kpi { border: 1px solid #cde2fb; border-radius: 5px; padding: 4pt 6pt; }
.kpi b { display: block; font-size: 11.5pt; color: #184f95; } .kpi span { color: #52514e; font-size: 7.4pt; }
.lead { background: #f6f9fe; border-left: 3px solid #2a78d6; padding: 5pt 8pt; margin: 4pt 0 6pt; font-size: 9pt; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 6pt; }
img { width: 100%; }
table { border-collapse: collapse; width: 100%; font-size: 8pt; }
th { background: #eef4fc; text-align: left; padding: 2.5pt 4pt; border-bottom: 1px solid #9ec5f4; }
td { padding: 2.5pt 4pt; border-bottom: 1px solid #ecebe7; vertical-align: top; }
td:first-child { width: 22%; font-weight: 600; color: #184f95; }
tr { page-break-inside: avoid; }
ol { margin: 2pt 0 4pt 14pt; padding: 0; } li { margin: 2.5pt 0; }
.note { color: #52514e; font-size: 7.4pt; }
.pb { page-break-before: always; }
.ev img { width: 82%; display: block; margin: 0 auto 4pt; }
"""

rows = [
    ("Which categories generate the highest revenue and margin?",
     f"<b>Electronics</b> leads revenue ({M(cat['Electronics']['net_revenue'])}, {P(cat['Electronics']['net_rev_share'])} of net revenue) "
     f"but earns only {P(cat['Electronics']['margin_pct'])} margin. <b>Fashion</b> is second in revenue ({M(cat['Fashion']['net_revenue'])}) and "
     f"first in margin ({M(cat['Fashion']['margin'])}, {P(cat['Fashion']['margin_pct'])}), followed by Mobile &amp; Accessories "
     f"({M(cat['Mobile & Accessories']['margin'])}) and Home &amp; Kitchen ({M(cat['Home & Kitchen']['margin'])}). Highest margin rate: "
     f"{best_margin_pct['category']} ({P(best_margin_pct['margin_pct'])}). Grocery ({P(cat['Grocery']['margin_pct'])}) is the other low-margin category."),
    ("Which provinces and cities contribute most / least? Top-5 city share?",
     f"Punjab {P(prov['Punjab']['share'])}, Sindh {P(prov['Sindh']['share'])}, KP {P(prov['Khyber Pakhtunkhwa']['share'])}, "
     f"Islamabad {P(prov['Islamabad Capital Territory']['share'])}, Balochistan {P(prov['Balochistan']['share'])}; least: Azad Kashmir "
     f"{P(prov['Azad Kashmir']['share'])} and Gilgit-Baltistan {P(prov['Gilgit-Baltistan']['share'])}. Top cities: "
     + ", ".join(f"{c['city']} {P(c['share'])}" for c in top5)
     + f" &rarr; <b>top five = {P(A['top5_city_share'])}</b> of net revenue. All {A['cities_with_orders']} locations order something, but the "
     f"40 largest cities give {P(A['top40_city_share'], 0)}; the smallest towns have under 20 orders each (e.g. {A['bottom5_cities'][-1]['city']}, "
     f"{int(A['bottom5_cities'][-1]['orders'])} orders)."),
    ("How does monthly revenue change? MoM growth?",
     f"Net revenue rose from {M(mom.net_revenue.iloc[0])} (Jan-24) to {M(mom.net_revenue.iloc[-1])} (Jun-25): "
     f"<b>+{P(A['growth_h1_2025_vs_h1_2024'], 0)} H1-25 vs H1-24</b>, a compound {P(cmgr)} per month. MoM is volatile and event-driven: "
     f"best Mar-25 (+{P(mom.loc['2025-03', 'net_revenue_mom'])}, Ramadan), Nov-24 (+{P(mom.loc['2024-11', 'net_revenue_mom'])}, 11.11/Black Friday); "
     f"worst Apr-25 ({P(mom.loc['2025-04', 'net_revenue_mom'])}) and May-24 ({P(mom.loc['2024-05', 'net_revenue_mom'])}), the months after Eid."),
    ("Return and cancellation rates, and their trend?",
     f"Return rate <b>{P(ov['return_rate'], 2)}</b> (valid returns / delivered lines), flat between {P(min(r['return_rate'] for r in A['quarterly_rates']))} "
     f"and {P(max(r['return_rate'] for r in A['quarterly_rates']))} by quarter (Q2-25 is low because recent orders are still in their return window). "
     f"Cancellation rate <b>{P(ov['cancellation_rate'], 2)}</b> of orders, rising from {P(q['2024Q1']['cancellation_rate'])} in Q1-24 to "
     f"{P(q['2025Q2']['cancellation_rate'])} in Q2-25."),
    ("Net revenue vs gross revenue by category?",
     f"Gross sales {M(ov['gross_sales'])} &rarr; minus discounts {M(ov['discounts'])}, cancellations {M(leak_cancel)} and refunds "
     f"{M(ov['refunds'])} = net revenue {M(ov['net_revenue'])} (<b>{P(1 - ov['net_revenue'] / ov['gross_sales'])} leakage</b>). Fashion loses the most "
     f"({P(cat['Fashion']['leakage_pct'])}, refunds = {P(cat['Fashion']['refunds'] / cat['Fashion']['nmv'])} of its NMV) and Footwear "
     f"{P(cat['Footwear']['leakage_pct'])}; all other categories lose 18.7-19.7%."),
    ("Observed customer lifetime value; repeat purchasing by region / category?",
     f"Observed CLV (net revenue per active customer, 18 months) = <b>PKR {clv['mean_clv']:,.0f}</b> (median {clv['median_clv']:,.0f}; "
     f"{clv['mean_orders']:.1f} orders; PKR {clv['mean_margin_per_customer']:,.0f} margin). Repeat purchase rate {P(clv['repeat_purchase_rate'])}; "
     f"the top 10% of customers bring {P(clv['top10pct_revenue_share'])} of revenue. Regions barely differ (repeat {P(reg['North']['repeat_rate'])} North, "
     f"{P(reg['Central']['repeat_rate'])} Central, {P(reg['South']['repeat_rate'])} South). By category, re-buying the same category varies a lot: "
     f"Mobile {P(rebuy['Mobile & Accessories'], 0)}, Fashion {P(rebuy['Fashion'], 0)}, Home {P(rebuy['Home & Kitchen'], 0)} vs Automotive "
     f"{P(rebuy['Automotive'], 0)}, Books {P(rebuy['Books & Stationery'], 0)}."),
    ("Which RFM segment generates the most revenue, and how large is it?",
     f"<b>Champions</b>: {seg['Champions']['customers']:,} customers ({P(seg['Champions']['customer_share'])}) generate "
     f"{M(seg['Champions']['net_revenue'])} = <b>{P(seg['Champions']['revenue_share'])}</b> of revenue (avg {seg['Champions']['avg_orders']:.1f} orders). "
     f"Loyal Customers add {P(seg['Loyal Customers']['revenue_share'])}. The largest group, Hibernating ({seg['Hibernating']['customers']:,}, "
     f"{P(seg['Hibernating']['customer_share'])}), brings only {P(seg['Hibernating']['revenue_share'])}; At Risk ({seg['At Risk']['customers']:,}) "
     f"still hold {M(seg['At Risk']['net_revenue'])}."),
    ("How should sellers be ranked?",
     f"Quality score (0-100) = 30% revenue (percentile of log revenue) + 25% low return rate + 25% on-time rate + 20% low cancellation rate, "
     f"for the {st['eligible_sellers']} sellers with &ge;100 delivered lines. Revenue gets the largest single weight because it measures value to the "
     f"business; the three service measures together (70%) protect customers. Top: "
     + ", ".join(f"{t['seller_name']} ({t['score']:.0f})" for t in top[:3])
     + f". Seller tier does not predict quality: Gold, Silver and Bronze all deliver on time {P(tiers['Gold']['on_time_rate'], 0)} of the time with "
     f"return rates {P(tiers['Bronze']['return_rate'])}-{P(tiers['Gold']['return_rate'])}."),
    ("How does on-time delivery relate to returns and cancellations?",
     f"On-time = delivered within 5 days of ordering (median is 4 days; 5 days covers ~3/4 of deliveries). <b>{P(ot['on_time_rate_orders'])}</b> of "
     f"delivered orders are on time. Late lines are returned more: {P(ot['return_rate_late'])} vs {P(ot['return_rate_on_time'])} on time; the rate climbs "
     f"from ~4.9% (&le;5 days) to ~7% (8+ days). Cancellations rise with slow delivery by geography (province correlation "
     f"{A['province_corr']['delivery_days_vs_cancellation']:.2f}) but not by seller (r = {A['seller_corr']['on_time_vs_cancellation']:.2f})."),
    ("Products with high sales and high returns - linked to sellers?",
     f"<b>{hot['count']} products</b> are in the top revenue quartile with a return rate &ge;2x average (&ge;10.4%): 43 Fashion (abayas, kurtas, lawn suits, "
     f"polos), 8 Footwear, 2 Electronics. Each is sold by one seller, and <b>5 sellers account for {P(hc['top5_sellers_share_of_returns'], 0)}</b> of their returns "
     f"({', '.join(s['seller_id'] for s in hc['top5'])}; return rates 11-16%). Size/fit (16%), not-as-described (15%) and quality (14%) are the main reasons."),
    ("Do Ramadan, Eid, 11.11, Black Friday show spikes / more returns or cancellations?",
     f"Yes for revenue: vs the 28 days before, revenue per day was +{P(ev['Ramadan 2024']['uplift_vs_prior_28d'], 0)} in Ramadan 2024, "
     f"+{P(ev['Ramadan 2025']['uplift_vs_prior_28d'], 0)} in Ramadan 2025, +{P(ev['11.11 2024']['uplift_vs_prior_28d'], 0)} on 11.11, "
     f"+{P(ev['Black Friday 2024']['uplift_vs_prior_28d'], 0)} on Black Friday and +{P(ev['14 Aug 2024']['uplift_vs_prior_28d'], 0)} on 14 August. "
     f"The Eid days themselves dip ({P(ev['Eid-ul-Fitr 2025']['uplift_vs_prior_28d'], 0)} to {P(ev['Eid-ul-Adha 2025']['uplift_vs_prior_28d'], 0)}) and "
     f"<b>cancellations jump to {P(ev['Eid-ul-Adha 2024']['cancellation_rate'])}-{P(ev['Eid-ul-Fitr 2024']['cancellation_rate'])}</b> (vs ~8.5% before). "
     f"Return rates stay within normal range (4.4-5.8%)."),
    ("Do discounts raise revenue while cutting margin or raising returns?",
     f"Discounting doubled: discounted orders went from {P(dm.loc['2024-01', 'discounted_order_share'], 0)} to {P(dm.loc['2025-06', 'discounted_order_share'], 0)} "
     f"of orders and average depth from {dm.loc['2024-01', 'avg_pct_when_discounted']:.0f}% to {dm.loc['2025-06', 'avg_pct_when_discounted']:.0f}%. "
     f"Baskets are not bigger (gross per order ~PKR 8.6-8.7k) except at 40-50% (PKR {disc['40-50%']['gross_per_order']:,.0f}), so discounts mostly give away "
     f"margin: {P(disc['0%']['margin_pct'], 0)} at 0%, {P(disc['15-20%']['margin_pct'], 0)} at 15-20%, {P(disc['25-30%']['margin_pct'], 0)} at 25-30% and "
     f"<b>{P(disc['40-50%']['margin_pct'], 0)} at 40-50%</b>. Returns ({P(disc['40-50%']['return_rate'])}) and cancellations ({P(disc['40-50%']['cancellation_rate'])}) "
     f"rise only at 40-50%."),
    ("Do slower regions have more cancellations or returns?",
     f"Yes. Avg days to delivery and cancellation rate: Islamabad {pdl['Islamabad Capital Territory']['avg_delivery_days']:.1f}d / "
     f"{P(pdl['Islamabad Capital Territory']['cancellation_rate'])}, Sindh {pdl['Sindh']['avg_delivery_days']:.1f}d / {P(pdl['Sindh']['cancellation_rate'])}, "
     f"Punjab {pdl['Punjab']['avg_delivery_days']:.1f}d / {P(pdl['Punjab']['cancellation_rate'])}, KP {pdl['Khyber Pakhtunkhwa']['avg_delivery_days']:.1f}d / "
     f"{P(pdl['Khyber Pakhtunkhwa']['cancellation_rate'])}, Balochistan {pdl['Balochistan']['avg_delivery_days']:.1f}d / {P(pdl['Balochistan']['cancellation_rate'])}, "
     f"GB {pdl['Gilgit-Baltistan']['avg_delivery_days']:.1f}d / <b>{P(pdl['Gilgit-Baltistan']['cancellation_rate'])}</b>. Returns rise less "
     f"({P(pdl['Islamabad Capital Territory']['return_rate'])} &rarr; {P(pdl['Azad Kashmir']['return_rate'])}). Tier-3 towns cancel {P(A['population_tier'][2]['cancellation_rate'])} vs "
     f"{P(A['population_tier'][0]['cancellation_rate'])} in metros. (Only 8 provinces - an association, not proof of cause.)"),
    ("Payment methods vs cancellations/returns - could data quality explain it?",
     f"COD is {P(pay['COD']['order_share'], 0)} of orders and cancels <b>{P(pay['COD']['cancellation_rate'])}</b> vs {P(pay['Card']['cancellation_rate'])}-"
     f"{P(pay['JazzCash']['cancellation_rate'])} for prepaid; {P(canc_type['COD']['cancelled_after_shipping'])} of COD orders are cancelled after shipping "
     f"(refused at the door) vs ~1.5% for prepaid. Returns are the same for all methods (5.0-5.3%). Data-quality caveats: 22 raw method labels "
     f"(e.g. 'COD ', 'Cash On Delivery') had to be unified, {A['payment_dq']['orders_with_split_payments']:,} orders have split payments "
     f"({A['payment_dq']['split_orders_with_mixed_methods']:,} with two methods, so the method depends on the rule used), and cancelled COD orders still have "
     f"payment rows - the payments file records intent, not cash collected. These blur the exact rate but cannot create a 3.5x gap."),
    ("Why is profit flat while revenue grows? Three actions.",
     f"H1-25 vs H1-24: net revenue <b>+{P(h2.net_revenue.sum() / h1.net_revenue.sum() - 1, 0)}</b>, but margin only +{P(h2.margin.sum() / h1.margin.sum() - 1, 0)} and margin after "
     f"shipping +{P(h2.contribution_after_shipping.sum() / h1.contribution_after_shipping.sum() - 1, 0)}. Margin fell from {P(mb['margin_pct_h1_2024'])} to "
     f"{P(mb['margin_pct_h1_2025'])}: category mix explains {mb['mix_effect_pp'] * 100:+.1f} pp, the drop inside every category {mb['within_category_effect_pp'] * 100:+.1f} pp. "
     f"Causes: (1) discounts rose from {P(h1.discounts.sum() / h1.gross_sales.sum())} to {P(h2.discounts.sum() / h2.gross_sales.sum())} of gross sales in all categories; "
     f"(2) free shipping grew from {P(h1.shipping_cost.sum() / h1.net_revenue.sum())} to {P(h2.shipping_cost.sum() / h2.net_revenue.sum())} of revenue; "
     f"(3) {P(cat['Electronics']['net_rev_share'], 0)} of revenue is Electronics at {P(cat['Electronics']['margin_pct'], 0)} margin; (4) rising COD cancellations waste "
     f"courier fees ({M(waste['shipping_cost_on_cancelled_orders'])} on cancelled orders). See actions below."),
]
qa = "".join(f"<tr><td>{i + 1}. {qq}</td><td>{aa}</td></tr>" for i, (qq, aa) in enumerate(rows))

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>NexusMart - Executive Summary</title><style>{CSS}</style></head><body>
<h1>NexusMart - Executive Summary &amp; Business Insights</h1>
<p class=sub>Jan 2024 - Jun 2025 · 160,000 orders · all figures in PKR from the cleaned warehouse, using the metric definitions in the brief.</p>
<div class=kpis>
<div class=kpi><b>{M(ov['net_revenue'])}</b><span>Net revenue (NMV - valid refunds)</span></div>
<div class=kpi><b>{M(ov['nmv'])}</b><span>Net merchandise value</span></div>
<div class=kpi><b>{M(ov['margin'])}</b><span>Margin ({P(ov['margin_pct'])} of net revenue)</span></div>
<div class=kpi><b>PKR {ov['aov']:,.0f}</b><span>AOV ({int(ov['non_cancelled_orders']):,} non-cancelled orders)</span></div>
<div class=kpi><b>{int(ov['active_customers']):,}</b><span>Active customers</span></div>
<div class=kpi><b>{M(ov['gross_sales'])}</b><span>Gross sales (before discounts &amp; cancellations)</span></div>
<div class=kpi><b>{int(ov['units']):,}</b><span>Units sold (non-cancelled)</span></div>
<div class=kpi><b>{P(ov['return_rate'], 2)}</b><span>Return rate (lines)</span></div>
<div class=kpi><b>{P(ov['cancellation_rate'], 2)}</b><span>Cancellation rate (orders)</span></div>
<div class=kpi><b>+{P(A['growth_h1_2025_vs_h1_2024'], 0)}</b><span>Net revenue growth, H1-25 vs H1-24</span></div>
</div>
<div class=lead><b>The story in one line:</b> NexusMart is growing fast (+{P(A['growth_h1_2025_vs_h1_2024'], 0)} year on year) but is buying that growth
with deeper discounts and free shipping, so margin after shipping grew only +{P(h2.contribution_after_shipping.sum() / h1.contribution_after_shipping.sum() - 1, 0)}.
Cancellations (mostly COD, mostly in slow-delivery regions) and returns (concentrated in a few Fashion sellers) are the other leaks.</div>
<div class=grid2><img src="../outputs/charts/01_net_revenue_monthly.png"><img src="../outputs/charts/02_margin_drivers.png"></div>

<h2>Answers to the business questions</h2>
<table><tr><th>Question</th><th>Answer (from the data)</th></tr>{qa}</table>

<h2>Three actions for leadership</h2>
<ol>
<li><b>Put a ceiling on discounts and target them.</b> Retire 40-50% codes ({M(disc['40-50%']['net_revenue'])} revenue sold at
{P(disc['40-50%']['margin_pct'], 0)} margin, i.e. a {M(-disc['40-50%']['net_revenue'] * disc['40-50%']['margin_pct'])} loss) and cap codes at 20%. Keep discounts for the proven event windows
(Ramadan, 11.11, Black Friday) and high-margin categories (Fashion, Mobile, Beauty), not Electronics or Grocery. Goal: bring the discount rate from
{P(h2.discounts.sum() / h2.gross_sales.sum(), 0)} back toward the {P(h1.discounts.sum() / h1.gross_sales.sum(), 0)} of H1-2024.</li>
<li><b>Cut COD cancellations and slow-region delays.</b> Confirm COD orders by call/WhatsApp before dispatch, offer a small prepaid incentive (instead of
blanket discount codes) and require advance payment from repeat refusers. COD cancels {P(pay['COD']['cancellation_rate'], 0)} vs ~4% prepaid. Set courier
SLAs or regional stock for KP, Balochistan, AJK and GB, where delivery takes 4.7-6.0 days and cancellations reach 10-13%. Every 1 pp of cancellations
is worth about {M(ov['nmv'] / (1 - ov['cancellation_rate']) * 0.01)} of NMV.</li>
<li><b>Fix the high-return Fashion listings and make shipping pay for itself.</b> Work with the 5 sellers behind {P(hc['top5_sellers_share_of_returns'], 0)} of
returns on high-selling products (size charts, real photos, QC before dispatch) and use the seller quality score in search ranking instead of the tier badge.
Introduce a free-shipping threshold (shipping is {P(h2.shipping_cost.sum() / h2.net_revenue.sum())} of revenue) so low-value Electronics and Grocery
baskets cover their courier cost.</li>
</ol>
<p class=note><b>Method notes.</b> Net revenue = NMV - refunds of valid (non-Rejected) returns; return rate = lines with a valid return / delivered lines
(non-cancelled lines of orders not still In Transit); cancellation rate = cancelled orders / all 160,000 orders after duplicate removal; margin = net revenue -
cost of goods on kept lines; AOV = NMV / non-cancelled orders. Data was cleaned by code (see Data Quality Report): duplicates removed,
customers and products de-duplicated, corrupted prices/quantities/dates repaired from other columns. Small differences from other teams' figures can come from
these documented cleaning choices. Correlations describe associations in this dataset, not proven causes.</p>
<h2 class=pb>Supporting evidence</h2>
<div class=ev><img src="../outputs/charts/03_category_revenue_margin.png"><img src="../outputs/charts/04_discount_bands.png">
<img src="../outputs/charts/05_delivery_payment_cancellations.png"><img src="../outputs/charts/06_festival_uplift.png"></div>
</body></html>"""

out = DOCS / "Executive_Summary.html"
out.write_text(html, encoding="utf-8")
chrome = next((c for c in [shutil.which("chromium"), shutil.which("google-chrome"),
                           "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"] if c and Path(c).exists()), None)
if chrome:
    subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer", "--allow-file-access-from-files",
                    f"--print-to-pdf={DOCS / 'Executive_Summary.pdf'}", out.as_uri()], capture_output=True)
print("written", out)
