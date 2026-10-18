# Power BI dashboard - build guide

> The finished dashboard is `NexusMart_Dashboard.pbix` (PDF: `NexusMart_Dashboard.pdf`). It was generated from code with
> `python scripts/build_pbip.py` (PBIP project = TMDL model + PBIR pages), opened in Power BI Desktop, refreshed from the
> PostgreSQL warehouse and saved as .pbix. This guide documents the same design so it can be rebuilt or extended by hand.

The dashboard must read from the **warehouse** (PostgreSQL), not the CSVs, and must not use templates.
This guide gives every step; the measures are in `measures.dax` and the theme in `nexusmart_theme.json`.
Expected time: 2-3 hours.

## 1. Connect to the warehouse

1. Run the pipeline first (README, step 3). The warehouse is then on `localhost:5433` (Docker) or `localhost:5432` (local PostgreSQL).
2. Power BI Desktop -> **Get data -> PostgreSQL database**
   - Server: `localhost:5433`  Database: `nexusmart`  Data connectivity mode: **Import**
   - Credentials: *Database* tab, user `nexusmart` and the password from your `.env`.
   - If Power BI asks for encryption, untick "Encrypt connections" (local only).
3. Select these tables and click **Load** (do **no** cleaning in Power Query - renaming is allowed):
   `dw.fact_sales`, `dw.dim_date`, `dw.dim_customer`, `dw.dim_product`, `dw.dim_seller`, `dw.dim_location`.
   Optional for the data-quality page: `dq.vw_latest_rejects`, `dq.vw_latest_rules`, `dq.quality_check_results`.
4. Rename the tables in the model to `fact_sales`, `dim_date`, `dim_customer`, `dim_product`, `dim_seller`, `dim_location` (drop the `dw ` prefix).

## 2. Model

Model view - create / check these relationships (all **many-to-one, single direction**, dimension -> fact):

| From (fact_sales) | To | Active |
|---|---|---|
| date_key | dim_date[date_key] | yes |
| return_date_key | dim_date[date_key] | **no** (inactive - used by `Refunds by Return Date`) |
| customer_key | dim_customer[customer_key] | yes |
| product_key | dim_product[product_key] | yes |
| seller_key | dim_seller[seller_key] | yes |
| location_key | dim_location[location_key] | yes |

- Select `dim_date` -> **Mark as date table** -> `full_date`.
- Sort `dim_date[month_name]` by `month_number`, `day_name` by `day_of_week`.
- Hide all `*_key` columns from report view.
- Create an empty table: Home -> Enter data -> name `_Measures`. Paste every measure from `measures.dax`
  (New measure, one at a time; keep the names). Set formats: currency PKR with 0 decimals for amounts,
  percentage with 1-2 decimals for rates.
- View -> Themes -> Browse for themes -> `nexusmart_theme.json`.

**Reference values** (full period, no filters) - your cards must show these:

| Measure | Value |
|---|---|
| Gross Sales | PKR 1,437.4M |
| NMV | PKR 1,200.0M |
| Net Revenue | PKR 1,142.3M |
| Margin | PKR 306.4M (26.8%) |
| Orders | 160,000 |
| AOV | PKR 8,257 |
| Units | 469,775 |
| Return Rate | 5.19% |
| Cancellation Rate | 9.16% |
| Active Customers | 31,521 |

## 3. Pages (1280 x 720)

Use the same layout on every page: title bar on top, **slicer row** under it, visuals below.
Slicers on every page (sync them via View -> Sync slicers): **Date** (`dim_date[full_date]`, between),
**Category** (`dim_product[category]`), **Region** (`dim_location[region]`), **Seller tier** (`dim_seller[seller_tier]`),
**Customer segment** (`dim_customer[customer_segment]`).

### Page 1 - Executive Overview
- 8 cards: Net Revenue, Orders, AOV, Units, Return Rate, Cancellation Rate, Active Customers, Margin %.
  Under each card add a small card with the MoM measure (`Net Revenue MoM %`, `Orders MoM %`, `AOV MoM %`,
  `Return Rate MoM pp`, `Cancellation Rate MoM pp`, `Active Customers MoM %`) - set conditional font color (green up / red down).
  MoM works when the date slicer covers whole months; it compares to the previous month.
- Column chart: `dim_date[year_month]` x `Net Revenue`, line `Net Revenue 3M Avg` (same axis).
- Bar chart: category x Net Revenue.
- Waterfall: Gross Sales -> Discounts -> cancellations -> Refunds -> Net Revenue (build a small "Bridge" table with 5 rows and a SWITCH measure, or use 4 cards).

### Page 2 - Time Analysis
- Line chart: `year_month` x Net Revenue + Net Revenue 3M Avg.
- Line chart (weekly): `dim_date[week_start_date]` x Net Revenue + `Net Revenue 7D Avg` on a daily axis variant.
- Column chart: `dim_date[event_name]` x `Net Revenue per Day` (shows Ramadan / 11.11 / Black Friday spikes) + tooltip with Return Rate, Cancellation Rate.
- Matrix: rows `year`, columns `month_name`, values Net Revenue (seasonality heat map with conditional background).
- Bar: day_name x Net Revenue per Day.

### Page 3 - Product & Category
- Clustered bar: category x Net Revenue and Margin; card/visual for Margin % by category (conditional colors: < 15% orange).
- Table: top 10 products by Net Revenue (Top N filter) with Return Rate; second table bottom 10.
- Scatter: product - X = Net Revenue, Y = Return Rate, size = Units, legend = category (high-sales / high-return products in the upper-right).
- Bar: category x Gross Sales vs Net Revenue (shows leakage).

### Page 4 - Geography
- Filled map or bar: province x Net Revenue (map: `dim_location[latitude/longitude]` bubble map of cities).
- Donut or 100% bar: region share (`Net Revenue Share %`).
- Card: `Top 5 City Share %` (expected 51.4%).
- Table: city, province, Net Revenue, Orders, Avg Delivery Days, Cancellation Rate, Return Rate.
- Column: population_tier x Net Revenue, AOV, Cancellation Rate.

### Page 5 - Customer Analysis
- Stacked column: year_month x New Customer Revenue / Returning Customer Revenue.
- Bar: customer_segment x Net Revenue, with customer count (`Active Customers`) in tooltip; card `Segment Revenue Share %`.
- Matrix (cohort): rows `dim_customer[cohort_month]`, columns `dim_date[year_month]`, values `Active Customers` (conditional background).
- Cards: Observed CLV (expected ~PKR 36.2k), Repeat Purchase Rate (expected ~71.8%).
- Table: top 20 customers by Net Revenue (full_name, city, order_count, segment).

### Page 6 - Seller Performance
- Table: seller_name, seller_tier, Net Revenue, Return Rate, On-Time Rate, Avg Delivery Days, Cancellation Rate, Seller Quality Score
  (sort by score; filter visual to sellers with Delivered Lines >= 100).
- Clustered column: seller_tier x Return Rate, On-Time Rate, Cancellation Rate.
- Scatter: seller - X = On-Time Rate, Y = Return Rate, size = Net Revenue.

### Page 7 - Returns & Cancellations
- Line: year_month x Return Rate and Cancellation Rate.
- Bars: Return Rate by category, by seller (top 15), by region; Cancellation Rate by payment_method and province.
- Donut/bar: `fact_sales[return_reason]` x Returned Lines.
- Line: `Refunds by Return Date` by month (uses the inactive relationship).

### Optional Page 8 - Data Quality
- Table from `dq.vw_latest_rejects`; table from `dq.quality_check_results` (latest run); card `Flagged Line %`.

## 4. Interactivity (required)
- **Drill-through**: create page "Seller Detail" (or "Product Detail") with `dim_seller[seller_name]` in the drill-through well; show KPI cards, monthly trend and the seller's products. Right-click a seller on page 6 -> Drill through.
- **Drill-down**: on the Geography bar chart put `region > province > city` in the axis; on the time chart `year > quarter > year_month`.
- **Tooltip page**: new page, Page information -> Allow use as tooltip, size Tooltip; show Net Revenue, Margin %, Return Rate, Cancellation Rate. Set it as the report-page tooltip on the category and province charts.
- **Bookmarks**: on page 3 create two bookmarks "Revenue view" / "Margin view" that swap a visual; add two buttons with bookmark actions.

## 5. Export
- File -> Save as `NexusMart_Dashboard.pbix`.
- File -> Export -> **Export to PDF** (all pages) -> `NexusMart_Dashboard.pdf`.
