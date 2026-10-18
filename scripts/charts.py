"""Static charts for the executive summary / DQ report (from outputs/tables)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "outputs" / "tables"
C = ROOT / "outputs" / "charts"
C.mkdir(parents=True, exist_ok=True)
BLUE, ORANGE, AQUA, GRAY, INK, INK2 = "#2a78d6", "#eb6834", "#1baf7a", "#a3a29c", "#0b0b0b", "#52514e"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#d9d8d4", "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#ecebe7", "grid.linewidth": 0.8, "axes.axisbelow": True,
                     "axes.titleweight": "bold", "axes.titlesize": 10, "axes.titlecolor": INK, "axes.titlelocation": "left"})
mil = mt.FuncFormatter(lambda v, _: f"{v / 1e6:,.0f}M")
pct = mt.PercentFormatter(1.0, decimals=0)


def fig(w=6.4, h=2.6):
    return plt.subplots(figsize=(w, h), dpi=200)


m = pd.read_csv(T / "profitability_monthly.csv", index_col=0)
x = range(len(m)); lab = [s[2:].replace("-", "/") for s in m.index]

# 1 net revenue trend
f, ax = fig()
ax.bar(x, m.net_revenue, color=BLUE, width=0.7)
ax.plot(x, m.net_revenue.rolling(3).mean(), color=ORANGE, lw=2, label="3-month moving average")
ax.yaxis.set_major_formatter(mil); ax.set_xticks(list(x)); ax.set_xticklabels(lab, rotation=0, fontsize=7)
ax.set_title("Net revenue by month (PKR)"); ax.legend(frameon=False, loc="upper left", fontsize=8)
for i, (k, v) in enumerate(m.net_revenue.items()):
    if k in ("2024-11", "2025-03", "2025-06"):
        ax.annotate(f"{v/1e6:.0f}M", (i, v), ha="center", va="bottom", fontsize=7, color=INK2, xytext=(0, 2), textcoords="offset points")
f.tight_layout(); f.savefig(C / "01_net_revenue_monthly.png"); plt.close(f)

# 2 margin squeeze: margin %, discount rate, shipping ratio (same unit: % ) - one axis
f, ax = fig()
ax.plot(x, m.margin_pct, color=BLUE, lw=2, marker="o", ms=3, label="Margin % of net revenue")
ax.plot(x, m.contribution_pct, color=AQUA, lw=2, marker="o", ms=3, label="Margin after shipping %")
ax.plot(x, m.discount_rate, color=ORANGE, lw=2, marker="o", ms=3, label="Discount % of gross sales")
ax.plot(x, m.shipping_ratio, color=GRAY, lw=2, marker="o", ms=3, label="Shipping cost % of net revenue")
ax.yaxis.set_major_formatter(pct); ax.set_xticks(list(x)); ax.set_xticklabels(lab, fontsize=7); ax.set_ylim(0, 0.33)
ax.set_title("Why profit is flat: discounts and shipping eat the margin")
ax.legend(frameon=False, ncol=2, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.12))
f.set_size_inches(6.4, 3.0); f.tight_layout(); f.savefig(C / "02_margin_drivers.png"); plt.close(f)

# 3 category revenue vs margin %
c = pd.read_csv(T / "category.csv", index_col=0).drop(index="Unknown", errors="ignore").sort_values("net_revenue")
f, (a1, a2) = plt.subplots(1, 2, figsize=(6.4, 2.9), dpi=200, sharey=True, gridspec_kw={"width_ratios": [1.3, 1]})
a1.barh(c.index, c.net_revenue, color=BLUE, height=0.65); a1.xaxis.set_major_formatter(mil); a1.set_title("Net revenue (PKR)")
cols = [ORANGE if v < 0.15 else BLUE for v in c.margin_pct]
a2.barh(c.index, c.margin_pct, color=cols, height=0.65); a2.xaxis.set_major_formatter(pct); a2.set_title("Margin %")
for i, v in enumerate(c.margin_pct):
    a2.text(v + 0.005, i, f"{v:.0%}", va="center", fontsize=7, color=INK2)
a1.tick_params(axis="y", labelsize=7.5); a2.set_xlim(0, 0.55)
f.tight_layout(); f.savefig(C / "03_category_revenue_margin.png"); plt.close(f)

# 4 discount bands
d = pd.read_csv(T / "discount_bands.csv", index_col=0)
f, (a1, a2) = plt.subplots(1, 2, figsize=(6.4, 2.4), dpi=200)
a1.bar(d.index, d.margin_pct, color=[BLUE if v > 0 else ORANGE for v in d.margin_pct], width=0.6)
a1.axhline(0, color=INK2, lw=0.8); a1.yaxis.set_major_formatter(pct); a1.set_title("Margin % by discount level")
for i, v in enumerate(d.margin_pct):
    a1.text(i, v + 0.01 if v >= 0 else 0.01, f"{v:.0%}", ha="center", fontsize=7, color=INK2)
a2.bar(d.index, d.cancellation_rate, color=BLUE, width=0.6); a2.yaxis.set_major_formatter(pct); a2.set_title("Cancellation rate by discount")
for i, v in enumerate(d.cancellation_rate):
    a2.text(i, v + 0.002, f"{v:.1%}", ha="center", fontsize=7, color=INK2)
for a in (a1, a2):
    a.tick_params(axis="x", labelsize=7.5)
f.tight_layout(); f.savefig(C / "04_discount_bands.png"); plt.close(f)

# 5 delivery speed vs cancellations by province
p = pd.read_csv(T / "province_delivery.csv", index_col=0).drop(index="Unknown", errors="ignore")
pm = pd.read_csv(T / "payment_methods.csv", index_col=0).sort_values("cancellation_rate")
f, (a1, a2) = plt.subplots(1, 2, figsize=(6.4, 2.6), dpi=200)
a1.scatter(p.avg_delivery_days, p.cancellation_rate, s=p.orders / 400 + 12, color=BLUE, edgecolor="white", linewidth=1.5, zorder=3)
short = {"Islamabad Capital Territory": "ICT", "Khyber Pakhtunkhwa": "KP", "Gilgit-Baltistan": "GB", "Azad Kashmir": "AJK"}
for k, r in p.iterrows():
    a1.annotate(short.get(k, k), (r.avg_delivery_days, r.cancellation_rate), fontsize=7, color=INK2, xytext=(4, -3), textcoords="offset points")
a1.yaxis.set_major_formatter(pct); a1.set_xlabel("Avg days from order to delivery"); a1.set_title("Slower provinces cancel more")
a2.barh(pm.index, pm.cancellation_rate, color=[ORANGE if k == "COD" else BLUE for k in pm.index], height=0.6)
a2.xaxis.set_major_formatter(pct); a2.set_title("Cancellation rate by payment")
for i, v in enumerate(pm.cancellation_rate):
    a2.text(v + 0.002, i, f"{v:.1%}", va="center", fontsize=7, color=INK2)
a2.set_xlim(0, 0.17)
f.tight_layout(); f.savefig(C / "05_delivery_payment_cancellations.png"); plt.close(f)

# 6 festivals uplift
e = pd.read_csv(T / "festival_windows.csv", index_col=0)
f, ax = fig(6.4, 2.4)
ax.bar(e.index, e.uplift_vs_prior_28d, color=[BLUE if v >= 0 else ORANGE for v in e.uplift_vs_prior_28d], width=0.6)
ax.axhline(0, color=INK2, lw=0.8); ax.yaxis.set_major_formatter(pct)
ax.set_title("Revenue per day vs the 28 days before each event"); ax.set_ylim(-0.5, 2.0)
ax.tick_params(axis="x", labelsize=6.5, rotation=20)
for i, v in enumerate(e.uplift_vs_prior_28d):
    ax.text(i, v + 0.04 if v >= 0 else 0.04, f"{v:+.0%}", ha="center", fontsize=7, color=INK2)
f.tight_layout(); f.savefig(C / "06_festival_uplift.png"); plt.close(f)
print("charts ->", C)
