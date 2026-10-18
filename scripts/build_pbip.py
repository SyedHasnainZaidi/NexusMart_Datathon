"""Generate the Power BI project (PBIP: TMDL semantic model + PBIR report) for the
NexusMart dashboard. Open powerbi/NexusMart_Dashboard.pbip in Power BI Desktop,
refresh, then File > Save as .pbix / Export to PDF.

The semantic model imports ONLY warehouse tables (dw.* and two dq views) from
PostgreSQL; server and database are model parameters (Server, Database).

    python scripts/build_pbip.py [--server localhost] [--database nexusmart] [--out powerbi]
"""
import argparse
import json
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "NexusMart_Dashboard"

# ----------------------------------------------------------------- columns
# (source table, model table, [(column, pg type)]) - generated from information_schema
COLUMNS_FILE = Path(__file__).with_name("pbip_columns.txt")


def lt():
    return str(uuid.uuid4())


def vid():
    return uuid.uuid4().hex[:20]


PG2TMDL = {"bigint": "int64", "integer": "int64", "smallint": "int64", "numeric": "double", "text": "string",
           "boolean": "boolean", "date": "dateTime", "timestamp without time zone": "dateTime"}

TABLES = {  # source -> model table name
    "dw.fact_sales": "fact_sales", "dw.dim_date": "dim_date", "dw.dim_customer": "dim_customer",
    "dw.dim_product": "dim_product", "dw.dim_seller": "dim_seller", "dw.dim_location": "dim_location",
    "dq.vw_latest_rejects": "dq_rejects", "dq.vw_latest_checks": "dq_checks",
}
HIDDEN = {"sales_key", "date_key", "customer_key", "product_key", "seller_key", "location_key", "return_date_key",
          "loaded_at", "updated_at"}
SORT_BY = {("dim_date", "month_name"): "month_number", ("dim_date", "day_name"): "day_of_week"}
SUM_COLS = {"quantity", "gross_amount", "discount_amount", "net_amount", "cost_amount", "shipping_fee",
            "returned_amount", "records"}

# ----------------------------------------------------------------- measures
M = [  # (name, folder, format, DAX)
    ("Gross Sales", "1 Sales", "#,0", "SUM(fact_sales[gross_amount])"),
    ("Discounts", "1 Sales", "#,0", "SUM(fact_sales[discount_amount])"),
    ("Cancelled Sales", "1 Sales", "#,0", "CALCULATE(SUM(fact_sales[net_amount]), fact_sales[is_cancelled] = TRUE())"),
    ("NMV", "1 Sales", "#,0", "CALCULATE(SUM(fact_sales[net_amount]), fact_sales[is_cancelled] = FALSE())"),
    ("Refunds", "1 Sales", "#,0", "SUM(fact_sales[returned_amount])"),
    ("Net Revenue", "1 Sales", "#,0", "[NMV] - [Refunds]"),
    ("Cost of Goods", "2 Profit", "#,0", "CALCULATE(SUM(fact_sales[cost_amount]), fact_sales[is_kept_line] = TRUE())"),
    ("Margin", "2 Profit", "#,0", "[Net Revenue] - [Cost of Goods]"),
    ("Margin %", "2 Profit", "0.0%", "DIVIDE([Margin], [Net Revenue])"),
    ("Shipping Cost", "2 Profit", "#,0", "SUM(fact_sales[shipping_fee])"),
    ("Margin After Shipping", "2 Profit", "#,0", "[Margin] - [Shipping Cost]"),
    ("Margin After Shipping %", "2 Profit", "0.0%", "DIVIDE([Margin After Shipping], [Net Revenue])"),
    ("Discount Rate", "2 Profit", "0.0%", "DIVIDE([Discounts], [Gross Sales])"),
    ("Leakage %", "2 Profit", "0.0%", "1 - DIVIDE([Net Revenue], [Gross Sales])"),
    ("Orders", "3 Orders", "#,0", "DISTINCTCOUNT(fact_sales[order_id])"),
    ("Non-Cancelled Orders", "3 Orders", "#,0", "CALCULATE(DISTINCTCOUNT(fact_sales[order_id]), fact_sales[is_cancelled] = FALSE())"),
    ("Cancelled Orders", "3 Orders", "#,0", "CALCULATE(DISTINCTCOUNT(fact_sales[order_id]), fact_sales[is_cancelled] = TRUE())"),
    ("Cancellation Rate", "3 Orders", "0.00%", "DIVIDE([Cancelled Orders], [Orders])"),
    ("AOV", "3 Orders", "#,0", "DIVIDE([NMV], [Non-Cancelled Orders])"),
    ("Units", "3 Orders", "#,0", "CALCULATE(SUM(fact_sales[quantity]), fact_sales[is_cancelled] = FALSE())"),
    ("Delivered Lines", "4 Returns", "#,0", "CALCULATE(COUNTROWS(fact_sales), fact_sales[is_delivered_line] = TRUE())"),
    ("Returned Lines", "4 Returns", "#,0", "CALCULATE(COUNTROWS(fact_sales), fact_sales[is_returned] = TRUE())"),
    ("Return Rate", "4 Returns", "0.00%", "DIVIDE([Returned Lines], [Delivered Lines])"),
    ("Refunds by Return Date", "4 Returns", "#,0", "CALCULATE([Refunds], USERELATIONSHIP(fact_sales[return_date_key], dim_date[date_key]))"),
    ("Active Customers", "5 Customers", "#,0", "CALCULATE(DISTINCTCOUNT(fact_sales[customer_key]), fact_sales[is_cancelled] = FALSE(), fact_sales[customer_key] <> -1)"),
    ("New Customer Revenue", "5 Customers", "#,0", "CALCULATE([Net Revenue], fact_sales[is_new_customer_order] = TRUE())"),
    ("Returning Customer Revenue", "5 Customers", "#,0", "[Net Revenue] - [New Customer Revenue]"),
    ("Observed CLV", "5 Customers", "#,0", "DIVIDE(CALCULATE([Net Revenue], fact_sales[customer_key] <> -1), [Active Customers])"),
    ("Repeat Purchase Rate", "5 Customers", "0.0%",
     "VAR c = ADDCOLUMNS(FILTER(VALUES(fact_sales[customer_key]), fact_sales[customer_key] <> -1), \"@o\", [Non-Cancelled Orders]) "
     "RETURN DIVIDE(COUNTROWS(FILTER(c, [@o] >= 2)), COUNTROWS(FILTER(c, [@o] >= 1)))"),
    ("On-Time Rate", "6 Delivery", "0.0%",
     "DIVIDE(CALCULATE(COUNTROWS(fact_sales), fact_sales[is_on_time] = TRUE(), fact_sales[is_delivered_line] = TRUE()), "
     "CALCULATE(COUNTROWS(fact_sales), NOT ISBLANK(fact_sales[is_on_time]), fact_sales[is_delivered_line] = TRUE()))"),
    ("Avg Delivery Days", "6 Delivery", "0.0", "AVERAGE(fact_sales[order_to_delivery_days])"),
    ("Seller Quality Score", "6 Delivery", "0.0", "AVERAGE(dim_seller[quality_score])"),
    ("Net Revenue per Day", "7 Time", "#,0", "DIVIDE([Net Revenue], CALCULATE(DISTINCTCOUNT(dim_date[full_date]), fact_sales))"),
    ("Net Revenue 3M Avg", "7 Time", "#,0",
     "IF(ISBLANK([Net Revenue]), BLANK(), AVERAGEX(CALCULATETABLE(VALUES(dim_date[year_month]), DATESINPERIOD(dim_date[full_date], MAX(dim_date[full_date]), -3, MONTH)), [Net Revenue]))"),
    ("Net Revenue 7D Avg", "7 Time", "#,0",
     "IF(ISBLANK([Net Revenue]), BLANK(), AVERAGEX(DATESINPERIOD(dim_date[full_date], MAX(dim_date[full_date]), -7, DAY), [Net Revenue]))"),
    ("Festival Uplift vs Normal %", "7 Time", "0.0%",
     "VAR n = CALCULATE([Net Revenue per Day], dim_date[event_name] = \"Normal day\") RETURN DIVIDE([Net Revenue per Day] - n, n)"),
    ("Latest Month", "7 Time", None, "FORMAT(CALCULATE(MAX(dim_date[full_date]), fact_sales), \"mmm yyyy\")"),
    ("Net Revenue Share %", "8 Geography", "0.0%", "DIVIDE([Net Revenue], CALCULATE([Net Revenue], ALLSELECTED(dim_location)))"),
    ("Top 5 City Share %", "8 Geography", "0.0%",
     "VAR t = TOPN(5, FILTER(ALLSELECTED(dim_location[city]), dim_location[city] <> \"Unknown\"), [Net Revenue]) "
     "RETURN DIVIDE(CALCULATE([Net Revenue], t), CALCULATE([Net Revenue], ALLSELECTED(dim_location)))"),
    ("Flagged Line %", "9 Data quality", "0.0%",
     "DIVIDE(CALCULATE(COUNTROWS(fact_sales), NOT ISBLANK(fact_sales[dq_flags])), COUNTROWS(fact_sales))"),
]
# month-over-month: latest month in the current selection vs the month before it
MOM_TMPL = ("VAR d = CALCULATE(MAX(dim_date[full_date]), fact_sales) VAR s = DATE(YEAR(d), MONTH(d), 1) "
            "VAR c = CALCULATE([{m}], DATESBETWEEN(dim_date[full_date], s, EOMONTH(d, 0))) "
            "VAR p = CALCULATE([{m}], DATESBETWEEN(dim_date[full_date], EDATE(s, -1), s - 1)) RETURN {ret}")
for base, kind in [("Net Revenue", "pct"), ("Orders", "pct"), ("AOV", "pct"), ("Units", "pct"), ("Active Customers", "pct"),
                   ("Return Rate", "pp"), ("Cancellation Rate", "pp"), ("Margin %", "pp")]:
    ret = "DIVIDE(c - p, p)" if kind == "pct" else "c - p"
    fmt = "+0.0%;-0.0%;0.0%" if kind == "pct" else "+0.00%;-0.00%;0.00%"
    M.append((f"{base} MoM", "0 MoM", fmt, MOM_TMPL.format(m=base, ret=ret)))


def q(name):  # TMDL object name quoting
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def tmdl_str(s):  # TMDL property value as a quoted string
    return '"' + s.replace('"', '""') + '"'


# ---------------------------------------------------------------- semantic model
def build_model(out: Path, server, database):
    cols = {}
    for line in COLUMNS_FILE.read_text().splitlines():
        src, c, t = line.split("|")
        cols.setdefault(src, []).append((c, t))
    sm = out / f"{NAME}.SemanticModel"
    d = sm / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    (d / "cultures").mkdir(parents=True, exist_ok=True)
    (sm / "definition.pbism").write_text(json.dumps({"version": "4.2", "settings": {}}, indent=2))
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n\n")
    (d / "cultures" / "en-US.tmdl").write_text("cultureInfo en-US\n\n")
    (d / "expressions.tmdl").write_text(
        f'expression Server = "{server}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {lt()}\n\n\tannotation PBI_ResultType = Text\n\n"
        f'expression Database = "{database}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {lt()}\n\n\tannotation PBI_ResultType = Text\n\n")
    order = []
    for src, tname in TABLES.items():
        schema, item = src.split(".")
        lines = [f"table {tname}"]
        if tname == "dim_date":
            lines.append("\tdataCategory: Time")
        lines += [f"\tlineageTag: {lt()}", ""]
        for c, t in cols[src]:
            dt = PG2TMDL[t]
            lines.append(f"\tcolumn {q(c)}")
            lines.append(f"\t\tdataType: {dt}")
            if tname == "dim_date" and c == "full_date":
                lines.append("\t\tisKey")
            if c in HIDDEN or (c.endswith("_key") and tname != "dq_checks"):
                lines.append("\t\tisHidden")
            if dt == "dateTime":
                lines.append("\t\tformatString: Short Date" if t == "date" else "\t\tformatString: General Date")
            elif dt == "int64":
                lines.append("\t\tformatString: 0")
            elif dt == "double":
                lines.append('\t\tformatString: "#,0.00"')
            elif dt == "boolean":
                lines.append('\t\tformatString: """TRUE"";""TRUE"";""FALSE"""')
            lines.append(f"\t\tlineageTag: {lt()}")
            summ = "sum" if c in SUM_COLS else "none"
            lines.append(f"\t\tsummarizeBy: {summ}")
            lines.append(f"\t\tsourceColumn: {c}")
            if (tname, c) in SORT_BY:
                lines.append(f"\t\tsortByColumn: {SORT_BY[(tname, c)]}")
            lines += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
        step = f'Source{{[Schema="{schema}",Item="{item}"]}}[Data]'
        body = [f"\t\t\t\t    Source = PostgreSQL.Database(Server, Database),",
                f"\t\t\t\t    tbl = {step}" + ("," if tname == "dim_date" else "")]
        result = "tbl"
        if tname == "dim_date":   # the Unknown member (-1) has no date: excluded so the table can be a date table
            body.append("\t\t\t\t    known = Table.SelectRows(tbl, each [date_key] <> -1)")
            result = "known"
        lines += [f"\tpartition {tname} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet", *body,
                  "\t\t\t\tin", f"\t\t\t\t    {result}", "", "\tannotation PBI_ResultType = Table", ""]
        (d / "tables" / f"{tname}.tmdl").write_text("\n".join(lines))
        order.append(tname)

    # measure table
    ml = ["table _Measures", f"\tlineageTag: {lt()}", ""]
    for name, folder, fmt, dax in M:
        ml.append(f"\tmeasure {q(name)} = {dax}")
        if fmt:
            ml.append(f"\t\tformatString: {tmdl_str(fmt)}")
        ml.append(f"\t\tdisplayFolder: {folder}")
        ml.append(f"\t\tlineageTag: {lt()}")
        ml.append("")
    ml += ["\tcolumn Value", "\t\tdataType: int64", "\t\tisHidden", "\t\tformatString: 0", f"\t\tlineageTag: {lt()}",
           "\t\tsummarizeBy: none", "\t\tisNameInferred", "\t\tsourceColumn: [Value]", "",
           "\t\tannotation SummarizationSetBy = Automatic", "",
           "\tpartition _Measures = calculated", "\t\tmode: import", '\t\tsource = ROW("Value", 1)', ""]
    (d / "tables" / "_Measures.tmdl").write_text("\n".join(ml))
    order.insert(0, "_Measures")

    rel = []
    for frm, to, active in [("fact_sales.date_key", "dim_date.date_key", True),
                            ("fact_sales.return_date_key", "dim_date.date_key", False),
                            ("fact_sales.customer_key", "dim_customer.customer_key", True),
                            ("fact_sales.product_key", "dim_product.product_key", True),
                            ("fact_sales.seller_key", "dim_seller.seller_key", True),
                            ("fact_sales.location_key", "dim_location.location_key", True)]:
        rel.append(f"relationship {lt()}")
        if not active:
            rel.append("\tisActive: false")
        rel += [f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]
    (d / "relationships.tmdl").write_text("\n".join(rel) + "\n")

    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
             "\tsourceQueryCulture: en-US", "\tvalueFilterBehavior: independent", "\tdataAccessOptions",
             "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
             "annotation __PBI_TimeIntelligenceEnabled = 0", "",
             f"annotation PBI_QueryOrder = {json.dumps(['Server', 'Database'] + [t for t in order if t != '_Measures'])}", "",
             'annotation PBI_ProTooling = ["DevMode"]', ""]
    model += [f"ref table {q(t)}" for t in order] + ["", "ref cultureInfo en-US", ""]
    (d / "model.tmdl").write_text("\n".join(model))
    (sm / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME}, "config": {"version": "2.0", "logicalId": lt()}}, indent=2))


# ---------------------------------------------------------------- report helpers
def C(table, col):
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
            "queryRef": f"{table}.{col}", "nativeQueryRef": col}


def ME(name):
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}}, "Property": name}},
            "queryRef": f"_Measures.{name}", "nativeQueryRef": name}


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


TOOLTIP_PAGE = {"name": None}


def visual(vtype, x, y, w, h, roles=None, title=None, sort=None, objects=None, z=0, sync=None, filters=None, tooltip=False):
    v = {"visualType": vtype}
    if roles:
        qs = {}
        for role, projs in roles.items():
            qs[role] = {"projections": [dict(p) for p in projs]}
        v["query"] = {"queryState": qs}
        if sort:
            proj, direction = sort
            v["query"]["sortDefinition"] = {"sort": [{"field": proj["field"], "direction": direction}]}
    if objects:
        v["objects"] = objects
    vco = {"subTitle": [{"properties": {"show": lit("false")}}]}
    if title:
        vco["title"] = [{"properties": {"show": lit("true"), "text": lit("'" + title.replace("'", "''") + "'")}}]
    if tooltip and TOOLTIP_PAGE["name"]:
        vco["visualTooltip"] = [{"properties": {"type": lit("'ReportPage'"), "section": lit("'" + TOOLTIP_PAGE["name"] + "'")}}]
    v["visualContainerObjects"] = vco
    if sync:
        v["syncGroup"] = {"groupName": sync, "fieldChanges": True, "filterChanges": True}
    v["drillFilterOtherVisuals"] = True
    out = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.13.0/schema.json",
           "name": vid(), "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z}, "visual": v}
    if filters:
        out["filterConfig"] = {"filters": filters}
    return out


def not_in(table, column, values):
    """Visual-level filter: column NOT IN values (values are DAX literals, e.g. "'UNKNOWN'" or "null")."""
    return {"name": vid(), "field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
            "type": "Categorical",
            "filter": {"Version": 2, "From": [{"Name": "t", "Entity": table, "Type": 0}],
                       "Where": [{"Condition": {"Not": {"Expression": {"In": {
                           "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}}],
                           "Values": [[{"Literal": {"Value": v}}] for v in values]}}}}}]}}


MILLIONS = {"Net Revenue", "Gross Sales", "NMV", "Margin", "Refunds", "Discounts"}


def kpi(measure, x, y, w, h, title=None, size=20, label=True):
    units = "1000000D" if measure in MILLIONS else "1D"
    props = {"labelDisplayUnits": lit(units), "fontSize": lit(f"{size}D")}
    if units != "1D":
        props["labelPrecision"] = lit("1L")
    # no title -> the card's own category label (the measure name) is shown under the value
    objs = {"labels": [{"properties": props}],
            "categoryLabels": [{"properties": {"show": lit("false" if (title or not label) else "true"), "fontSize": lit("9D")}}]}
    return visual("card", x, y, w, h, {"Values": [ME(measure)]}, title=title, objects=objs)


# Editorial look: ink text on warm paper, Georgia headings, one red accent, rules instead of boxes
INK, INK_2, ACCENT = "#1A1A1A", "#5A554D", "#BF3B2E"
PERIOD = "Jan 2024 – Jun 2025  ·  amounts in PKR"


def textbox(text, x, y, w, h, size="20pt", color=INK, z=0, bold=True, font=None, italic=False, align=None, bg=None):
    style = {"fontSize": size, "color": color}
    if bold:
        style["fontWeight"] = "bold"
    if font:
        style["fontFamily"] = font
    if italic:
        style["fontStyle"] = "italic"
    para = {"textRuns": [{"value": text, "textStyle": style}]}
    if align:
        para["horizontalTextAlignment"] = align
    v = {"visualType": "textbox",
         "objects": {"general": [{"properties": {"paragraphs": [para]}}]},
         "drillFilterOtherVisuals": True}
    if bg:  # a filled strip, used for the horizontal rules
        v["visualContainerObjects"] = {"background": [{"properties": {
            "show": lit("true"), "color": {"solid": {"color": lit(f"'{bg}'")}}, "transparency": lit("0D")}}]}
    return {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.13.0/schema.json",
            "name": vid(), "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z}, "visual": v}


def rule(x, y, w, h=2, color=INK, z=2):
    """Horizontal rule (newspaper-style divider)."""
    return textbox(" ", x, y, w, h, size="1pt", color=color, z=z, bold=False, bg=color)


LABELS = {"labels": [{"properties": {"show": lit("true")}}]}
SEASONALITY_GRID = {"grid": [{"properties": {"rowPadding": lit("0D"), "textSize": lit("7D")}}],
                    "values": [{"properties": {"fontSize": lit("8D")}}],
                    "rowHeaders": [{"properties": {"fontSize": lit("8D")}}]}


def slicer_row(y=60):
    specs = [(C("dim_date", "full_date"), "Order date", "'Between'", "date"),
             (C("dim_product", "category"), "Category", "'Dropdown'", "category"),
             (C("dim_location", "region"), "Region", "'Dropdown'", "region"),
             (C("dim_seller", "seller_tier"), "Seller tier", "'Dropdown'", "tier"),
             (C("dim_customer", "customer_segment"), "Customer segment", "'Dropdown'", "segment")]
    out, x, w = [], 20, 240
    for i, (f, title, mode, grp) in enumerate(specs):
        out.append(visual("slicer", x + i * (w + 8), y, w, 60, {"Values": [f]}, title=title,
                          objects={"data": [{"properties": {"mode": lit(mode)}}]}, z=1000 + i, sync=grp))
    return out


def page(title, subtitle, visuals_, slicers=True):
    vs = [textbox("NEXUSMART", 20, 0, 300, 18, size="8pt", color=ACCENT, z=3),
          textbox(PERIOD, 860, 0, 400, 18, size="8pt", color=INK_2, z=4, bold=False, align="right"),
          textbox(title, 20, 12, 900, 32, size="17pt", font="Georgia"),
          textbox(subtitle, 20, 38, 1240, 18, size="8pt", color=INK_2, z=1, bold=False, italic=True),
          rule(20, 56, 1240, 2)]
    if slicers:
        vs += slicer_row()
        vs.append(rule(20, 121, 1240, 1, color="#CFC6B8", z=5))
    z = 10
    for v in visuals_:
        v["position"]["z"] = z
        v["position"]["tabOrder"] = z
        z += 1
        vs.append(v)
    return {"displayName": title, "visuals": vs}


def build_pages():
    """Returns the page list. The tooltip page is created first so its name can be referenced."""
    tip_name = vid()
    TOOLTIP_PAGE["name"] = tip_name
    drill_name = vid()
    P = []
    # 1 Executive overview
    kpis = ["Net Revenue", "Orders", "AOV", "Units", "Return Rate", "Cancellation Rate", "Active Customers", "Margin %"]
    v = []
    w = 148
    for i, k in enumerate(kpis):
        x = 20 + i * (w + 8)
        v.append(kpi(k, x, 124, w, 96))
        v.append(kpi(f"{k} MoM", x, 224, w, 50, size=12, label=False))  # metric is named by the card above
    v.append(textbox("Row above: change vs previous month (MoM)", 20, 274, 420, 20, size="8pt", color=INK_2, bold=False, italic=True))
    v.append(visual("lineClusteredColumnComboChart", 20, 296, 620, 414,
                    {"Category": [C("dim_date", "year_month")], "Y": [ME("Net Revenue")], "Y2": [ME("Net Revenue 3M Avg")]},
                    title="Net revenue by month (PKR) with 3-month moving average",
                    sort=(C("dim_date", "year_month"), "Ascending"), tooltip=True))
    v.append(visual("clusteredBarChart", 650, 296, 300, 414, {"Category": [C("dim_product", "category")], "Y": [ME("Net Revenue")]},
                    title="Net revenue by category", sort=(ME("Net Revenue"), "Descending"), tooltip=True))
    v.append(visual("multiRowCard", 960, 296, 300, 414,
                    {"Values": [ME("Gross Sales"), ME("Discounts"), ME("Cancelled Sales"), ME("Refunds"), ME("Net Revenue"), ME("Leakage %")]},
                    title="From gross sales to net revenue (PKR)"))
    P.append(page("Executive Overview", "All amounts in PKR · definitions as in the brief · MoM = latest month in the selection vs the month before", v))

    # 2 Time analysis
    v = [visual("lineChart", 20, 126, 620, 284, {"Category": [C("dim_date", "year_month")], "Y": [ME("Net Revenue"), ME("Net Revenue 3M Avg")]},
                title="Monthly net revenue and 3-month moving average", sort=(C("dim_date", "year_month"), "Ascending"), tooltip=True),
         visual("lineChart", 650, 126, 610, 284, {"Category": [C("dim_date", "week_start_date")], "Y": [ME("Net Revenue")]},
                title="Weekly net revenue", sort=(C("dim_date", "week_start_date"), "Ascending")),
         visual("clusteredColumnChart", 20, 418, 420, 292, {"Category": [C("dim_date", "event_name")], "Y": [ME("Net Revenue per Day")]},
                title="Net revenue per day: festivals vs normal days", sort=(ME("Net Revenue per Day"), "Descending"), objects=LABELS, tooltip=True),
         # months as rows so every value is shown in full PKR (12 months + total fit without scrolling)
         visual("pivotTable", 450, 397, 520, 318, {"Rows": [C("dim_date", "month_name")], "Columns": [C("dim_date", "year")],
                                                     "Values": [ME("Net Revenue")]}, title="Seasonality: net revenue by year and month",
                objects=SEASONALITY_GRID),
         visual("clusteredColumnChart", 980, 418, 280, 292, {"Category": [C("dim_date", "day_name")], "Y": [ME("Net Revenue per Day")]},
                title="Net revenue per day by weekday", sort=(C("dim_date", "day_name"), "Ascending"))]
    P.append(page("Time Analysis", "Trends, moving averages, seasonality and festival comparisons", v))

    # 3 Product & category
    v = [visual("clusteredBarChart", 20, 126, 400, 292, {"Category": [C("dim_product", "category")], "Y": [ME("Net Revenue"), ME("Margin")]},
                title="Net revenue and margin by category", sort=(ME("Net Revenue"), "Descending"), tooltip=True),
         visual("clusteredBarChart", 430, 126, 300, 292, {"Category": [C("dim_product", "category")], "Y": [ME("Margin %")]},
                title="Margin % by category", sort=(ME("Margin %"), "Descending"), objects=LABELS, tooltip=True),
         visual("clusteredBarChart", 740, 126, 520, 292, {"Category": [C("dim_product", "category")], "Y": [ME("Gross Sales"), ME("Net Revenue")]},
                title="Gross sales vs net revenue by category (leakage)", sort=(ME("Gross Sales"), "Descending")),
         visual("tableEx", 20, 428, 430, 282, {"Values": [C("dim_product", "product_name"), C("dim_product", "category"), ME("Net Revenue"), ME("Return Rate")]},
                title="Top products by net revenue", sort=(ME("Net Revenue"), "Descending"),
                filters=[not_in("dim_product", "product_id", ["'UNKNOWN'"])]),
         visual("tableEx", 460, 428, 400, 282, {"Values": [C("dim_product", "product_name"), C("dim_product", "category"), ME("Net Revenue"), ME("Units")]},
                title="Bottom products by net revenue", sort=(ME("Net Revenue"), "Ascending"),
                filters=[not_in("dim_product", "product_id", ["'UNKNOWN'"])]),
         visual("scatterChart", 870, 428, 390, 282, {"Category": [C("dim_product", "product_name")], "Series": [C("dim_product", "category")],
                                                      "X": [ME("Net Revenue")], "Y": [ME("Return Rate")]},
                title="Sales vs return rate by product (top-right = high sales, high returns)")]
    P.append(page("Product & Category", "Category revenue and margin, best and worst products, sales vs return rates", v))

    # 4 Geography
    v = [visual("clusteredBarChart", 20, 126, 400, 292, {"Category": [C("dim_location", "region"), C("dim_location", "province"), C("dim_location", "city")],
                                                         "Y": [ME("Net Revenue")]},
                title="Net revenue by region > province > city (drill down)", sort=(ME("Net Revenue"), "Descending"), objects=LABELS, tooltip=True),
         visual("donutChart", 430, 126, 300, 292, {"Category": [C("dim_location", "region")], "Y": [ME("Net Revenue")]}, title="Regional revenue share"),
         kpi("Top 5 City Share %", 740, 126, 255, 92, title="Share of the top 5 cities"),
         kpi("Avg Delivery Days", 1005, 126, 255, 92, title="Avg days to delivery"),
         visual("clusteredColumnChart", 740, 226, 520, 192, {"Category": [C("dim_location", "population_tier")],
                                                             "Y": [ME("Cancellation Rate"), ME("Return Rate")]},
                title="Location tier comparison: cancellation and return rates", objects=LABELS),
         visual("tableEx", 20, 428, 800, 282, {"Values": [C("dim_location", "city"), C("dim_location", "province"), ME("Net Revenue"), ME("Net Revenue Share %"),
                                                          ME("Orders"), ME("Avg Delivery Days"), ME("Cancellation Rate"), ME("Return Rate")]},
                title="City performance", sort=(ME("Net Revenue"), "Descending")),
         visual("clusteredBarChart", 830, 428, 430, 282, {"Category": [C("dim_location", "province")], "Y": [ME("Avg Delivery Days")]},
                title="Average delivery days by province", sort=(ME("Avg Delivery Days"), "Descending"), objects=LABELS, tooltip=True)]
    P.append(page("Geography", "City and province performance, regional share and location tiers", v))

    # 5 Customers
    v = [visual("columnChart", 20, 126, 620, 284, {"Category": [C("dim_date", "year_month")], "Y": [ME("New Customer Revenue"), ME("Returning Customer Revenue")]},
                title="Net revenue from new vs returning customers", sort=(C("dim_date", "year_month"), "Ascending")),
         visual("clusteredBarChart", 650, 126, 380, 284, {"Category": [C("dim_customer", "customer_segment")], "Y": [ME("Net Revenue")]},
                title="Net revenue by RFM segment", sort=(ME("Net Revenue"), "Descending"), objects=LABELS, tooltip=True,
                filters=[not_in("dim_customer", "customer_segment", ["'Unknown'"])]),
         kpi("Observed CLV", 1040, 126, 220, 90, title="Observed CLV (PKR)"),
         kpi("Repeat Purchase Rate", 1040, 222, 220, 90, title="Repeat purchase rate"),
         kpi("Active Customers", 1040, 318, 220, 92, title="Active customers"),
         visual("pivotTable", 20, 418, 760, 292, {"Rows": [C("dim_customer", "cohort_month")], "Columns": [C("dim_date", "year_month")],
                                                    "Values": [ME("Active Customers")]}, title="Cohort retention: active customers by first-order month",
                filters=[not_in("dim_customer", "customer_id", ["'UNKNOWN'"])]),
         visual("tableEx", 790, 418, 470, 292, {"Values": [C("dim_customer", "full_name"), C("dim_customer", "city"), C("dim_customer", "customer_segment"), ME("Orders"), ME("Net Revenue")]},
                title="Top customers", sort=(ME("Net Revenue"), "Descending"),
                filters=[not_in("dim_customer", "customer_id", ["'UNKNOWN'"])])]
    P.append(page("Customer Analysis", "New vs returning, RFM segments, cohorts, lifetime value and top customers", v))

    # 6 Sellers
    v = [visual("tableEx", 20, 126, 790, 584, {"Values": [C("dim_seller", "quality_rank"), C("dim_seller", "seller_name"), C("dim_seller", "seller_tier"),
                                                         ME("Seller Quality Score"), ME("Net Revenue"), ME("Return Rate"), ME("On-Time Rate"),
                                                         ME("Avg Delivery Days"), ME("Cancellation Rate")]},
                title="Seller ranking - score = 30% revenue + 25% low returns + 25% on-time + 20% low cancellations (sellers with 100+ delivered lines)",
                sort=(ME("Seller Quality Score"), "Descending"), filters=[not_in("dim_seller", "seller_id", ["'UNKNOWN'"])]),
         visual("clusteredColumnChart", 820, 126, 440, 284, {"Category": [C("dim_seller", "seller_tier")],
                                                             "Y": [ME("Return Rate"), ME("Cancellation Rate")]},
                title="Seller tier comparison: return and cancellation rates", objects=LABELS),
         visual("scatterChart", 820, 418, 440, 292, {"Category": [C("dim_seller", "seller_name")], "X": [ME("On-Time Rate")], "Y": [ME("Return Rate")],
                                                      "Size": [ME("Net Revenue")]}, title="On-time rate vs return rate (bubble = revenue)")]
    P.append(page("Seller Performance", "Ranking, revenue, return rates, delivery times and tier comparison · right-click a seller > Drill through > Seller Detail", v))

    # 7 Returns & cancellations
    v = [visual("lineChart", 20, 126, 620, 284, {"Category": [C("dim_date", "year_month")], "Y": [ME("Return Rate"), ME("Cancellation Rate")]},
                title="Return and cancellation rates by month", sort=(C("dim_date", "year_month"), "Ascending")),
         visual("clusteredBarChart", 650, 126, 300, 284, {"Category": [C("fact_sales", "return_reason")], "Y": [ME("Returned Lines")]},
                title="Return reasons", sort=(ME("Returned Lines"), "Descending"), objects=LABELS,
                filters=[not_in("fact_sales", "return_reason", ["null"])]),
         visual("clusteredBarChart", 960, 126, 300, 284, {"Category": [C("fact_sales", "payment_method")], "Y": [ME("Cancellation Rate")]},
                title="Cancellation rate by payment method", sort=(ME("Cancellation Rate"), "Descending"), objects=LABELS, tooltip=True),
         visual("clusteredBarChart", 20, 418, 410, 292, {"Category": [C("dim_product", "category")], "Y": [ME("Return Rate")]},
                title="Return rate by category", sort=(ME("Return Rate"), "Descending"), objects=LABELS, tooltip=True),
         visual("clusteredBarChart", 440, 418, 410, 292, {"Category": [C("dim_location", "province")], "Y": [ME("Cancellation Rate"), ME("Return Rate")]},
                title="Cancellation and return rate by province", sort=(ME("Cancellation Rate"), "Descending")),
         visual("tableEx", 860, 418, 400, 292, {"Values": [C("dim_seller", "seller_name"), ME("Delivered Lines"), ME("Return Rate"), ME("Cancellation Rate")]},
                title="Return rate by seller", sort=(ME("Return Rate"), "Descending"))]
    P.append(page("Returns & Cancellations", "Rates by category, seller, region and payment method; reasons and trends", v))

    # 8 Data quality (warehouse dq tables; no star-schema slicers)
    v = [visual("tableEx", 20, 70, 600, 300, {"Values": [C("dq_rejects", "source_table"), C("dq_rejects", "reason_code"), C("dq_rejects", "records")]},
                title="Quarantined / merged records (reject table, latest run)"),
         kpi("Flagged Line %", 630, 70, 300, 90, title="Order lines carrying a repair flag"),
         visual("clusteredBarChart", 630, 170, 630, 200, {"Category": [C("fact_sales", "dq_flags")], "Y": [ME("Delivered Lines")]},
                title="Repairs applied to order lines (dq_flags)", sort=(ME("Delivered Lines"), "Descending"),
                filters=[not_in("fact_sales", "dq_flags", ["null"])]),
         visual("tableEx", 20, 380, 1240, 330, {"Values": [C("dq_checks", "check_name"), C("dq_checks", "severity"), C("dq_checks", "observed"),
                                                           C("dq_checks", "threshold"), C("dq_checks", "passed"), C("dq_checks", "detail")]},
                title="Automated quality checks (latest pipeline run)")]
    P.append(page("Data Quality", "What the pipeline fixed and how it was checked", v, slicers=False))

    # 9 drill-through page
    drill = page("Seller Detail", "Drill-through target: right-click a seller on any page > Drill through > Seller Detail", [
        kpi("Net Revenue", 20, 126, 240, 88), kpi("Return Rate", 270, 126, 240, 88),
        kpi("On-Time Rate", 520, 126, 240, 88), kpi("Cancellation Rate", 770, 126, 240, 88),
        kpi("Seller Quality Score", 1020, 126, 240, 88),
        visual("lineChart", 20, 222, 620, 488, {"Category": [C("dim_date", "year_month")], "Y": [ME("Net Revenue")]}, title="Monthly net revenue",
               sort=(C("dim_date", "year_month"), "Ascending")),
        visual("tableEx", 650, 222, 610, 488, {"Values": [C("dim_product", "product_name"), C("dim_product", "category"), ME("Net Revenue"), ME("Return Rate")]},
               title="Products sold by this seller", sort=(ME("Net Revenue"), "Descending"))])
    drill["name"] = drill_name
    drill["drillthrough"] = C("dim_seller", "seller_name")["field"]
    P.append(drill)

    # 10 tooltip page
    tip = {"name": tip_name, "displayName": "Tooltip - KPIs", "width": 320, "height": 240, "tooltip": True, "visuals": [
        kpi("Net Revenue", 5, 5, 150, 72), kpi("Margin %", 165, 5, 150, 72),
        kpi("Return Rate", 5, 83, 150, 72), kpi("Cancellation Rate", 165, 83, 150, 72),
        kpi("Orders", 5, 161, 150, 72), kpi("AOV", 165, 161, 150, 72)]}
    for i, vv in enumerate(tip["visuals"]):
        vv["position"]["z"] = i
        vv["position"]["tabOrder"] = i
    P.append(tip)
    return P


def build_report(out: Path):
    rp = out / f"{NAME}.Report"
    d = rp / "definition"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    (rp / "definition.pbir").write_text(json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2))
    (rp / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME}, "config": {"version": "2.0", "logicalId": lt()}}, indent=2))
    (d / "version.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0"}, indent=2))
    base_theme = "Fluent2-CY26SU09"
    rv = {"visual": "2.13.0", "report": "3.4.0", "page": "2.3.1"}
    report = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": base_theme, "reportVersionAtImport": rv, "type": "SharedResources"},
            "customTheme": {"name": "NexusMart.json", "reportVersionAtImport": rv, "type": "RegisteredResources"}},
        "objects": {"section": [{"properties": {"verticalAlignment": lit("'Top'")}}]},
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": base_theme, "path": f"BaseThemes/{base_theme}.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "NexusMart.json", "path": "NexusMart.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized", "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}}
    (d / "report.json").write_text(json.dumps(report, indent=2))
    reg = rp / "StaticResources" / "RegisteredResources"
    reg.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "powerbi" / "nexusmart_theme.json", reg / "NexusMart.json")

    order = []
    for p in build_pages():
        pid = p.get("name") or vid()
        order.append(pid)
        pd_ = d / "pages" / pid
        (pd_ / "visuals").mkdir(parents=True, exist_ok=True)
        meta = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
                "name": pid, "displayName": p["displayName"], "displayOption": "FitToPage",
                "height": p.get("height", 720), "width": p.get("width", 1280)}
        if p.get("tooltip"):
            meta["type"] = "Tooltip"
            meta["visibility"] = "HiddenInViewMode"
            meta["pageBinding"] = {"name": vid(), "type": "Tooltip", "referenceScope": "Default"}
        if p.get("drillthrough"):
            fname = vid()
            meta["filterConfig"] = {"filters": [{"name": fname, "field": p["drillthrough"], "type": "Categorical",
                                                 "howCreated": "Drillthrough"}]}
            meta["pageBinding"] = {"name": vid(), "type": "Drillthrough", "referenceScope": "Default",
                                   "parameters": [{"name": vid(), "boundFilter": fname, "fieldExpr": p["drillthrough"]}]}
        (pd_ / "page.json").write_text(json.dumps(meta, indent=2))
        for v in p["visuals"]:
            vd = pd_ / "visuals" / v["name"]
            vd.mkdir(parents=True, exist_ok=True)
            (vd / "visual.json").write_text(json.dumps(v, indent=2))
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": order, "activePageName": order[0]}, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="localhost")
    ap.add_argument("--database", default="nexusmart")
    ap.add_argument("--out", default=str(ROOT / "powerbi"))
    a = ap.parse_args()
    out = Path(a.out)
    for sub in [f"{NAME}.SemanticModel/definition", f"{NAME}.Report/definition"]:
        shutil.rmtree(out / sub, ignore_errors=True)
    build_model(out, a.server, a.database)
    build_report(out)
    (out / f"{NAME}.pbip").write_text(json.dumps({"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                                  "settings": {"enableAutoRecovery": True}}, indent=2))
    print("PBIP written to", out)
