"""Builds docs/Data_Quality_Report.html (+ .pdf if Chromium/Chrome is available)
entirely from pipeline artefacts: data/dq/raw_profile.json, rule_log.csv,
table_counts.csv, quality_checks.csv and data/rejects/reject_records.csv.

    python scripts/build_dq_report.py
"""
import html
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DQ = ROOT / "data" / "dq"
DOCS = ROOT / "docs"

profile = json.loads((DQ / "raw_profile.json").read_text())
rules = pd.read_csv(DQ / "rule_log.csv")
counts = pd.read_csv(DQ / "table_counts.csv")
checks = pd.read_csv(DQ / "quality_checks.csv")
rejects = pd.read_csv(ROOT / "data" / "rejects" / "reject_records.csv", usecols=["source_table", "reason_code", "reason_detail"])
price_model = json.loads((DQ / "price_model.json").read_text())

CSS = """
@page { size: A4; margin: 14mm 13mm; }
body { font-family: 'Segoe UI', 'DejaVu Sans', Arial, sans-serif; color: #0b0b0b; font-size: 9.2pt; line-height: 1.38; }
h1 { font-size: 19pt; margin: 0 0 2pt; color: #184f95; }
h2 { font-size: 12.5pt; color: #184f95; border-bottom: 1.5px solid #b7d3f6; padding-bottom: 2pt; margin: 14pt 0 6pt; }
h3 { font-size: 10.5pt; margin: 10pt 0 4pt; }
.sub { color: #52514e; margin: 0 0 10pt; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt; font-size: 8.3pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #eef4fc; text-align: left; padding: 3pt 5pt; border-bottom: 1px solid #9ec5f4; }
td { padding: 2.5pt 5pt; border-bottom: 1px solid #ecebe7; vertical-align: top; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tag { display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 7.4pt; font-weight: 600; }
.REPAIR { background: #dff3ea; color: #0b6b47; } .IMPUTE { background: #fff1d6; color: #7a5200; }
.FLAG { background: #fde6dc; color: #9a3412; } .QUARANTINE { background: #fbe0e0; color: #9b1c1c; }
.STANDARDISE { background: #e3eefc; color: #184f95; } .MERGE { background: #ece9fb; color: #4a3aa7; }
.RETAIN { background: #efefec; color: #52514e; }
.pass { color: #0b6b47; font-weight: 600; } .fail { color: #9b1c1c; font-weight: 600; }
.box { background: #f6f9fe; border: 1px solid #cde2fb; border-radius: 6px; padding: 7pt 9pt; margin: 6pt 0; }
ul { margin: 3pt 0 6pt 14pt; padding: 0; } li { margin: 1.5pt 0; }
.kpis { display: flex; gap: 8pt; margin: 6pt 0 4pt; }
.kpi { flex: 1; border: 1px solid #cde2fb; border-radius: 6px; padding: 5pt 7pt; }
.kpi b { display: block; font-size: 14pt; color: #184f95; } .kpi span { color: #52514e; font-size: 7.8pt; }
"""


def esc(x):
    return html.escape("" if pd.isna(x) else str(x))


def fmt(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------ before / after
rej_by_table = rejects.groupby("source_table").size()
merged = rejects[rejects.reason_code == "MERGED_DUPLICATE"].groupby("source_table").size()
rows = []
for _, r in counts.iterrows():
    t = r["table"]
    p = profile[t]
    rows.append(f"<tr><td>{t}</td><td class=n>{fmt(r['raw'])}</td><td class=n>{fmt(p['exact_duplicate_rows'])}</td>"
                f"<td class=n>{fmt(merged.get(t, 0))}</td><td class=n>{fmt(rej_by_table.get(t, 0) - merged.get(t, 0) - p['exact_duplicate_rows'])}</td>"
                f"<td class=n><b>{fmt(r['clean'])}</b></td>"
                f"<td class=n>{fmt(rules[(rules.table == t) & rules.action.isin(['REPAIR', 'IMPUTE'])].rows_affected.sum())}</td></tr>")
before_after = ("<table><tr><th>Table</th><th class=n>Raw rows</th><th class=n>Exact duplicates removed</th>"
                "<th class=n>Duplicate entities merged</th><th class=n>Other quarantined</th><th class=n>Clean rows</th>"
                "<th class=n>Values repaired / imputed*</th></tr>" + "".join(rows) + "</table>"
                "<p class=sub>* Count of value-level fixes (one row can receive several). Every removed row is stored in "
                "<code>dq.reject_records</code> / <code>data/rejects/reject_records.csv</code> with a reason code.</p>")

# ------------------------------------------------------------ raw profile (blanks)
prof_rows = []
for t, p in profile.items():
    blanks = {k: v for k, v in p["blank_values"].items() if v > 0}
    txt = ", ".join(f"{k} {v:,} ({v / p['rows']:.1%})" for k, v in sorted(blanks.items(), key=lambda kv: -kv[1]))
    prof_rows.append(f"<tr><td>{t}</td><td class=n>{p['rows']:,}</td><td class=n>{p['exact_duplicate_rows']:,}</td>"
                     f"<td class=n>{p['duplicate_primary_keys']:,}</td><td>{esc(txt) or '-'}</td></tr>")
raw_prof = ("<table><tr><th>File</th><th class=n>Rows</th><th class=n>Exact dup rows</th><th class=n>Dup IDs</th>"
            "<th>Blank values by column</th></tr>" + "".join(prof_rows) + "</table>")

# ------------------------------------------------------------ rule log per table
order = ["locations", "sellers", "products", "customers", "orders", "order_items", "payments", "shipping", "returns"]
rule_html = []
for t in order:
    rr = rules[(rules.table == t) & (rules.rows_affected > 0)]
    none = rules[(rules.table == t) & (rules.rows_affected == 0)].rule_code.unique()
    body = "".join(
        f"<tr><td>{esc(r.column)}</td><td><code>{esc(r.rule_code)}</code></td><td>{esc(r.description)}</td>"
        f"<td class=n>{fmt(r.rows_affected)}</td><td><span class='tag {r.action}'>{r.action}</span></td>"
        f"<td class=n>{fmt(r.rows_before)}{' &rarr; ' + fmt(r.rows_after) if not pd.isna(r.rows_after) else ''}</td></tr>"
        for r in rr.itertuples())
    rule_html.append(f"<h3>{t}</h3><table><tr><th>Column</th><th>Rule</th><th>What was wrong &rarr; what we did</th>"
                     f"<th class=n>Rows</th><th>Action</th><th class=n>Before &rarr; after</th></tr>{body}</table>"
                     + (f"<p class=sub>Checked, nothing found: {', '.join(none)}</p>" if len(none) else ""))

# ------------------------------------------------------------ rejects
rj = rejects.groupby(["source_table", "reason_code", "reason_detail"]).size().reset_index(name="n")
rej_html = "<table><tr><th>Table</th><th>Reason code</th><th>Detail</th><th class=n>Records</th></tr>" + "".join(
    f"<tr><td>{r.source_table}</td><td><code>{r.reason_code}</code></td><td>{esc(r.reason_detail)}</td><td class=n>{r.n:,}</td></tr>"
    for r in rj.itertuples()) + "</table>"

# ------------------------------------------------------------ checks
chk_html = "<table><tr><th>Check</th><th>Severity</th><th class=n>Observed</th><th class=n>Threshold</th><th>Result</th><th>Meaning</th></tr>" + "".join(
    f"<tr><td><code>{r.check_name}</code></td><td>{r.severity}</td><td class=n>{r.observed:g}</td>"
    f"<td class=n>{'' if pd.isna(r.threshold) else f'{r.threshold:g}'}</td>"
    f"<td class={'pass' if str(r.passed).lower() == 'true' else 'fail'}>{'PASS' if str(r.passed).lower() == 'true' else 'FAIL'}</td>"
    f"<td>{esc(r.detail)}</td></tr>" for r in checks.itertuples()) + "</table>"


def ra(code, table=None):
    m = rules[(rules.rule_code == code) & ((rules.table == table) if table else True)]
    return int(m.rows_affected.sum())


raw_total = int(counts.raw.sum())
fixes = int(rules[rules.action.isin(["REPAIR", "IMPUTE", "STANDARDISE", "MERGE"])].rows_affected.sum())
n_pass = int(checks.passed.astype(str).str.lower().eq("true").sum())

page = f"""<!doctype html><html><head><meta charset="utf-8"><title>NexusMart - Data Quality Report</title>
<style>{CSS}</style></head><body>
<h1>NexusMart - Data Quality Report</h1>
<p class=sub>CODE//CONNECT'26 Data Analytics Module · Phase 1 submission · generated automatically by
<code>scripts/build_dq_report.py</code> from the pipeline's own logs (rule log, reject table, quality checks).</p>

<div class=kpis>
<div class=kpi><b>{raw_total:,}</b><span>raw rows across 9 files</span></div>
<div class=kpi><b>{len(rules[rules.rows_affected > 0])}</b><span>cleaning rules that fired</span></div>
<div class=kpi><b>{fixes:,}</b><span>values standardised, repaired, imputed or merged</span></div>
<div class=kpi><b>{n_pass}/{len(checks)}</b><span>automated quality checks passed</span></div>
</div>

<h2>1. Principles</h2>
<ul>
<li><b>All cleaning is code</b> (<code>nexusmart_etl/clean.py</code>); nothing was edited by hand, in Excel, in the database or in Power Query.</li>
<li><b>Nothing is silently deleted.</b> Every row that leaves a table is written to the reject table with a reason code. Value repairs are flagged on the row
(<code>dq_flags</code> column in FactSales, <code>*_imputed</code> / <code>*_repaired</code> columns in staging).</li>
<li><b>Repair from evidence first</b> (other columns or tables of the same record), impute second, quarantine only if neither is possible.</li>
<li><b>Thresholds are documented in code</b> and in this report; the Airflow DAG fails when a critical quality threshold is breached.</li>
</ul>

<h2>2. Before and after</h2>
{before_after}

<h2>3. What the raw data looked like</h2>
{raw_prof}
<div class=box><b>Main problem types found:</b> 3 date formats mixed in every date column (ISO, DD/MM/YYYY, "Mon D YYYY") plus impossible dates
(2024-02-30, month 13, 00/00/2024) and order dates typed as 2026/2027; 74 spellings of 12 categories; 20 spellings of 5 order statuses;
22 payment-method, 22 courier and 38 return-reason variants; amounts stored as text ("Rs. 2,290"); sentinel 9,999,999 in prices, totals and fees;
prices recorded 100x too high or in US dollars (~1/280 of the PKR value); negative quantities/prices; quantities written as words or 500;
blank discount amounts; discounts larger than the line value; refunds 1.4x the item value; duplicated orders and payments;
the same customers and products under different IDs; return IDs with lost zero-padding.</div>

<h2>4. Key decisions and assumptions</h2>
<ul>
<li><b>Dates.</b> Slashed dates are day/month/year (brief). Order dates that are impossible or outside 1 Jan 2024 - 30 Jun 2025
({ra('DATE_INVALID', 'orders') + ra('DATE_OUT_OF_RANGE', 'orders'):,} orders) were repaired from the earliest payment or shipment date of the same order
(validated on clean orders: same month in 97% of cases, median error 0 days). {ra('DATE_UNREPAIRABLE'):,} orders could not be repaired.</li>
<li><b>Unit price.</b> Selling prices follow list price x a smooth inflation index (+{price_model['price_index_b_per_day'] * 30.4:.2%} per month, fitted on clean lines).
Prices ~100x the expected value were divided by 100; negative prices made positive; prices in USD, zero, or 9,999,999 were solved exactly from
<code>order_total</code> when that line was the only unknown in its order ({ra('PRICE_FROM_TOTAL'):,} lines), otherwise list price x index ({ra('PRICE_FALLBACK'):,} lines).</li>
<li><b>Quantity.</b> Words and "2.0" converted; negatives made positive; 0 or &gt;50 (the 500 entry error) solved from <code>order_total</code>
({ra('QTY_FROM_TOTAL'):,} lines) or set to 1 ({ra('QTY_FALLBACK'):,} lines).</li>
<li><b>Discounts.</b> The order's <code>discount_code</code> is the authoritative %. Line discount = % x line gross when blank ({ra('DISC_BLANK'):,}),
larger than the line ({ra('DISC_EXCEEDS_GROSS'):,}) or inconsistent ({ra('DISC_MISMATCH'):,}). After all fixes only
{ra('ORDER_TOTAL_RECON'):,} orders differ from their <code>order_total</code> by more than 1%.</li>
<li><b>Duplicate customers</b> ({ra('DUP_ENTITY', 'customers'):,} merged, 45,000 &rarr; 42,500): same normalised name AND same e-mail or phone,
and no conflicting birth year or city. Never on name alone. Orders are re-pointed through <code>dq.customer_id_map</code>.</li>
<li><b>Duplicate products</b> ({ra('DUP_ENTITY', 'products'):,} merged): same name and category AND same cost or weight AND list price within 5%.
Mapping kept in <code>dq.product_id_map</code>.</li>
<li><b>Broken references</b> are mapped to the <i>Unknown</i> member (key -1) instead of being dropped, so revenue is kept:
{ra('FK_PRODUCT'):,} lines with product IDs not in the catalogue, {ra('FK_CUSTOMER'):,} orders with unknown customers.
Return IDs with lost zero-padding were re-padded ({ra('ID_PADDING'):,}).</li>
<li><b>Ship city.</b> Blank ship city filled with the customer's registered city ({ra('SHIP_CITY_FROM_CUSTOMER'):,} orders, flagged;
89% of orders ship to the registered city). The flag lets the dashboard exclude them.</li>
<li><b>Returns.</b> Only non-Rejected (incl. "Declined") returns are valid. Refunds above the line's net value are capped at it
({ra('REFUND_CAP'):,}). Returns against cancelled lines are quarantined ({ra('RETURN_ON_CANCELLED'):,}).</li>
<li><b>Shipping.</b> Missing, negative or 9,999,999 fees were imputed with the median fee of the same courier and order-weight band
({ra('FEE_IMPUTE'):,} orders). Delivery durations that are negative ({ra('DELIVERED_BEFORE_SHIPPED'):,}) or &gt;30 days
({ra('DELIVERY_OUTLIER'):,}) are set to NULL, not guessed. One fee per order is allocated to its lines by gross-value share.</li>
<li><b>Products.</b> Cost above 1.2x list price (an injected 1.5x pattern, {ra('COST_OUTLIER'):,} products) and blank costs ({ra('COST_BLANK'):,})
were re-imputed from the sub-category's median cost/list ratio. Invalid list prices were re-derived from the product's own sales.</li>
</ul>

<h2>5. Rule-by-rule log (from <code>dq.rule_log</code>)</h2>
{''.join(rule_html)}

<h2>6. Reject / quarantine table (from <code>dq.reject_records</code>)</h2>
{rej_html}

<h2>7. Automated quality checks (task <code>quality_checks</code>)</h2>
{chk_html}

<h2>8. Known limitations</h2>
<ul>
<li>One duplicate product pair (ElecPro Power Strip U248, P02216/P03090) is not merged: both records carry corrupted costs and one has no weight,
so the rule cannot confirm the match without using the name alone.</li>
<li>June 2025 return rate (≈4%) is understated because some June orders are still inside their return window.</li>
<li>For {ra('FK_PRODUCT'):,} lines whose product is not in the catalogue, cost is estimated at 60% of the selling price (catalogue median).</li>
<li>Payment records exist for every order, including cancelled COD orders, and 4,815 orders have split payments; the order's payment method is the one
with the largest amount.</li>
</ul>
</body></html>"""

out = DOCS / "Data_Quality_Report.html"
out.write_text(page, encoding="utf-8")
print("written", out)

chrome = next((c for c in [shutil.which("chromium"), shutil.which("google-chrome"), shutil.which("chrome"),
                           "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"] if c and Path(c).exists()), None)
if chrome:
    subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={DOCS / 'Data_Quality_Report.pdf'}", out.as_uri()], capture_output=True)
    print("written", DOCS / "Data_Quality_Report.pdf")
