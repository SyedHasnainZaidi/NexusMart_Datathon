# NexusMart Data Pipeline and Dashboard

**CODE//CONNECT'26 - Data Analytics Module**

This project takes NexusMart's messy sales files (9 CSV files) and turns them into a clean dashboard:

```
 9 raw CSV files  ->  Python cleans the data  ->  PostgreSQL database (star schema)  ->  Power BI dashboard
                      \_______________ run automatically by an Airflow pipeline ______________/
```

---

## 1. Where to find each submission item

| # | What was asked for | File(s) in this folder |
|---|---|---|
| 1 | Airflow pipeline code (`.py`) | `dags/nexusmart_pipeline.py` (the pipeline) and the `nexusmart_etl/` folder (the cleaning and loading code it runs) |
| 2 | Database scripts (`.sql`) | `sql/` folder (8 scripts, numbered in the order they run) |
| 3 | Data model diagram | `docs/star_schema.png` and `docs/star_schema.pdf` |
| 4 | Power BI dashboard (`.pbix` + PDF) | `powerbi/NexusMart_Dashboard.pbix` and `powerbi/NexusMart_Dashboard.pdf` |
| 5 | Short report of findings and advice | `docs/Executive_Summary.pdf` (3 pages) |
| 6 | Code | this whole folder |
| 7 | README | this file |
| 8 | Data quality report | `docs/Data_Quality_Report.pdf` (what was wrong with the data and how each problem was fixed) |

**No passwords are stored in the code.** They are read from a local `.env` file, which is not included.
The file `.env.example` only contains placeholder values.

---

## 2. Results at a glance (Jan 2024 - Jun 2025)

| Measure | Value |
|---|---|
| Net revenue | PKR 1,142.3 million |
| Gross sales | PKR 1,437.4 million |
| Margin | PKR 306.4 million (26.8%) |
| Orders | 160,000 |
| Average order value | PKR 8,257 |
| Return rate | 5.19% |
| Cancellation rate | 9.16% |
| Active customers | 31,521 |

The full story and our recommendations are in `docs/Executive_Summary.pdf`.

---

## 3. How to run it (step by step)

### What you need
- **Docker Desktop** (recommended). It needs about 4 GB of free memory and about 6 GB of free disk space.
  *Tip for Windows: if drive C: is nearly full, move Docker's storage to another drive in
  Docker Desktop -> Settings -> Resources -> Advanced -> Disk image location.*
- **Power BI Desktop** (Windows), to open the dashboard.
- The 9 competition CSV files.

### Steps

1. **Add the data.** Copy the 9 CSV files into the `data/raw/` folder:
   `customers.csv`, `products.csv`, `sellers.csv`, `locations.csv`, `orders.csv`, `order_items.csv`,
   `payments.csv`, `shipping.csv`, `returns.csv`.

2. **Create the settings file.** In this folder, make a copy of `.env.example` and name it `.env`.
   You can change the passwords inside it (letters and digits only).
   ```
   copy .env.example .env        (Windows)
   cp .env.example .env          (Mac / Linux)
   ```

3. **Start everything.** Open a terminal in this folder and run:
   ```
   docker compose up -d --build
   ```
   The first start takes a few minutes. This starts PostgreSQL and Airflow.
   *If the first build stops with an error, run `docker compose build airflow-webserver` once, then run the command above again.*

4. **Run the pipeline.**
   - Open **http://localhost:8080** in a browser.
   - Log in with `AIRFLOW_ADMIN_USER` and `AIRFLOW_ADMIN_PASSWORD` from your `.env` file.
   - Click the pipeline called **nexusmart_etl**, then click the **Trigger** (play) button.
   - All 7 steps turn green in about 2 minutes. You can run it again at any time and it will not create duplicates.

5. **Open the dashboard.**
   - Open `powerbi/NexusMart_Dashboard.pbix`. It already contains the data, so you can look at it straight away.
   - To reload it from your own database: **Transform data -> Edit parameters**, set Server to `localhost:5433` and
     Database to `nexusmart`, then click **Refresh**. Log in with `NEXUSMART_DB_USER` and `NEXUSMART_DB_PASSWORD` from `.env`.

6. **Stop everything** when you are done:
   ```
   docker compose down
   ```

### Running without Docker (optional)
If you have Python 3.10+ and PostgreSQL installed instead:
```
pip install -r requirements.txt
psql -U postgres -c "CREATE USER nexusmart WITH PASSWORD 'choose_a_password';" -c "CREATE DATABASE nexusmart OWNER nexusmart;"
```
Set the database settings as environment variables, then run the same 7 steps without Airflow:
```
# Windows PowerShell
$env:NEXUSMART_DB_USER="nexusmart"; $env:NEXUSMART_DB_PASSWORD="choose_a_password"
# Mac / Linux
export NEXUSMART_DB_USER=nexusmart NEXUSMART_DB_PASSWORD=choose_a_password

python scripts/run_pipeline.py
```
With PostgreSQL on your own computer, use Server `localhost` in Power BI instead of `localhost:5433`.

### Re-creating the reports and running the tests (optional)
```
python scripts/analysis.py            # answers to the business questions -> outputs/
python scripts/charts.py              # charts -> outputs/charts/
python scripts/build_dq_report.py     # docs/Data_Quality_Report.pdf
python scripts/build_exec_summary.py  # docs/Executive_Summary.pdf
python -m pytest -q                   # tests
```
With Docker, run them inside the container, for example:
`docker compose exec airflow-scheduler bash -c "cd /opt/nexusmart && python scripts/analysis.py"`

---

## 4. What the pipeline does

The Airflow pipeline (`dags/nexusmart_pipeline.py`) runs 7 steps in order:

| Step | What it does |
|---|---|
| 1. extract | Reads the 9 CSV files |
| 2. validate_raw | Checks that every required column is present (stops if not) |
| 3. clean | Fixes the data problems (see section 6) |
| 4. load_staging | Loads the cleaned data into the database |
| 5. build_dimensions | Builds the customer, product, seller, location and date tables |
| 6. build_fact | Builds the main sales table |
| 7. quality_checks | Runs 31 checks; the pipeline fails if a critical check fails |

- If a step fails because of a temporary problem, it retries automatically.
- Every step records how many rows it read, rejected and loaded (in the Airflow log and the `dq.run_log` table).
- Running the pipeline twice gives exactly the same result (tested).
- **New data:** replace the files in `data/raw/` and run the pipeline again. It accepts different file-name and
  column-name spellings, and new unknown labels are logged rather than breaking the run.

---

## 5. The dashboard

`powerbi/NexusMart_Dashboard.pbix` reads only from the cleaned database (not from the CSV files). Its pages are:

1. Executive Overview - headline numbers and monthly trend
2. Time Analysis - trends, moving average, seasonality, festivals
3. Product & Category
4. Geography - region -> province -> city (drill down)
5. Customer Analysis - repeat customers, segments, cohorts
6. Seller Performance - seller quality score and ranking
7. Returns & Cancellations
8. Data Quality - what the pipeline fixed or rejected
9. Seller Detail - right-click a seller and choose *Drill through* to open it
10. A hover tooltip page (shows extra detail when you hover over some charts)

`powerbi/NexusMart_Dashboard.pdf` is a PDF of all the pages. The rest of the `powerbi/` folder holds the dashboard
as source files (`.pbip`), the measure formulas (`measures.dax`), the colour theme and a build guide
(`PowerBI_Build_Guide.md`).

---

## 6. How the data was cleaned (summary)

Full details, with counts for every rule, are in `docs/Data_Quality_Report.pdf`.

| Problem | What we did |
|---|---|
| Duplicate rows | Removed 3,200 duplicate orders and 3,296 duplicate payments |
| Same customer or product entered twice | Merged 2,500 customers and 25 products |
| Many spellings of the same label | e.g. 74 spellings of categories reduced to 12; statuses, payment methods, couriers, provinces etc. also unified |
| Dates in different formats | 3 formats read correctly; impossible dates repaired from payment/shipping dates |
| Numbers stored as text | e.g. "Rs. 2,290" -> 2290, "one" -> 1 |
| Missing values | Filled in only when other data allows it (e.g. ship city from the customer's city), otherwise left blank and flagged |
| Wrong or extreme values | e.g. 9,999,999 placeholders, prices 100x too high, negative amounts - each fixed by a written rule |
| Links to records that don't exist | Linked to an "Unknown" record, or set aside |

Rejected records are kept with the reason they were rejected: `data/rejects/reject_records.csv`.
Quality check results are in `data/dq/`.

---

## 7. Database design (star schema)

Diagram: `docs/star_schema.png`

- **One main table, `fact_sales`**: one row for each product line in an order (315,143 rows).
- **Five lookup tables**: `dim_date`, `dim_customer`, `dim_product`, `dim_seller`, `dim_location`.
- Each lookup table has an "Unknown" row (key -1), so no sale is lost when a link is missing.
- The date table marks weekends, Pakistani holidays, Ramadan, the two Eids, 14 August, 11.11 and Black Friday.
- Customer details are overwritten when they change (SCD Type 1).

### How the main numbers are calculated (as defined in the brief)

| Measure | Formula |
|---|---|
| Gross sales | quantity x unit price, all lines |
| NMV | gross sales - discounts, excluding cancelled orders |
| Net revenue | NMV - refunds for accepted returns |
| Margin | net revenue - cost of goods |
| Return rate | lines returned / lines delivered |
| Cancellation rate | cancelled orders / all orders |
| Average order value | NMV / orders that were not cancelled |
| Active customers | customers with at least one order that was not cancelled |
| On-time delivery | delivered within 5 days of the order |
| Seller quality score | 30% revenue + 25% low returns + 25% on-time delivery + 20% low cancellations |

---

## 8. Folder guide

| Folder | What is inside |
|---|---|
| `dags/` | The Airflow pipeline |
| `nexusmart_etl/` | Python code for reading, cleaning and loading the data |
| `sql/` | Database scripts: tables, lookup tables, sales table, quality checks, views, business questions, seller score |
| `scripts/` | Run the pipeline without Airflow, analysis, charts, report builders, dashboard builder |
| `docs/` | Data model diagram, Executive Summary, Data Quality Report |
| `powerbi/` | Dashboard (`.pbix`), PDF of all pages, source files, measures, theme |
| `outputs/` | Answers to the business questions (`answers.json`), tables (`tables/`) and charts (`charts/`) |
| `data/` | `raw/` - put the CSV files here; `dq/` - quality check results; `rejects/` - rejected records |
| `tests/` | Automated tests |
| `docker/`, `docker-compose.yml` | Docker setup for PostgreSQL and Airflow |

---

## 9. Known limitations

- June 2025 return rate looks lower than it really is, because recent orders can still be returned.
- 932 order lines refer to products that are not in the product list. Their revenue is kept under "Unknown product".
- Blank ship cities were filled with the customer's own city and flagged; this affects 3.7% of net revenue.
- One possible duplicate product pair was not merged because only the name matches (see the Data Quality Report).
- Tested on Windows with Docker Desktop: all 7 steps succeed in about 2 minutes, and a second run changes nothing.
