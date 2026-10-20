"""Regenerates the messy sample dataset in this folder.

Run from the repo root:  .venv/bin/python samples/make_samples.py

The mess is deliberate, so each stage has something real to fix:
- sales.csv: mixed date formats, prices with "RM" and thousands separators, region
  spelled several ways (case, spaces, "N/A", blanks), duplicate rows, a row with an
  extra value, and order lines whose product is missing from products.
- products.csv: semicolon-delimited, a duplicated product code, a blank category.
- customers.xlsx: one sheet of customers (blank cells, inconsistent segment case) and
  a "Notes" sheet that is not data.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).parent
rng = random.Random(42)

PRODUCTS = [
    ("P-100", "Kopi Beans 1kg", "Beverages", "45.00"),
    ("P-101", "Teh Tarik Mix", "Beverages", "18.50"),
    ("P-102", "Nasi Lemak Kit", "Food", "22.00"),
    ("P-103", "Sambal Jar", "Food", "12.90"),
    ("P-104", "Rice Cooker", "Appliances", "189.00"),
    ("P-105", "Electric Kettle", "Appliances", "79.90"),
    ("P-106", "Tiffin Carrier", "Kitchenware", "35.00"),
    ("P-107", "Wok 36cm", "Kitchenware", "1,250.00"),
]
REGIONS = ["Central", "Northern", "Southern", "East Coast", "East Malaysia"]
REGION_MESS = {"Central": ["central", "CENTRAL", "Central "], "Northern": ["northern", " Northern"]}
SEGMENTS = ["Retail", "Wholesale", "Online"]


def messy_date(d: date) -> str:
    style = rng.random()
    if style < 0.6:
        return d.isoformat()
    if style < 0.85:
        return d.strftime("%d/%m/%Y")
    return d.strftime("%d %b %Y")


def messy_price(price: str) -> str:
    return rng.choice([price, price, f"RM {price}", f"RM{price}"])


def messy_region(region: str) -> str:
    roll = rng.random()
    if roll < 0.05:
        return ""
    if roll < 0.08:
        return "N/A"
    if roll < 0.25 and region in REGION_MESS:
        return rng.choice(REGION_MESS[region])
    return region


def write_sales() -> None:
    start = date(2024, 1, 1)
    rows = []
    for n in range(1, 241):
        code, _, _, price = rng.choice(PRODUCTS)
        if n % 37 == 0:
            code = "P-199"  # not in products
        rows.append([
            f"SO-{10000 + n}",
            messy_date(start + timedelta(days=rng.randrange(0, 640))),
            f"C{rng.randrange(1, 41):03d}",
            code,
            str(rng.choice([1, 1, 1, 2, 2, 3, 5, 10])),
            messy_price(price),
            messy_region(rng.choice(REGIONS)),
        ])
    rows += [rows[10], rows[55], rows[120]]  # exact duplicates
    with open(HERE / "sales.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Order ID", "Order Date", "Customer ID", "Product Code", "Qty", "Unit Price", "Region"])
        writer.writerows(rows)
        writer.writerow(["SO-10999", "2025-06-30", "C007", "P-101", "2", "18.50", "Central", "late entry"])


def write_products() -> None:
    with open(HERE / "products.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["product_code", "product_name", "category", "list_price"])
        for code, name, category, price in PRODUCTS:
            writer.writerow([code, name, "" if code == "P-106" else category, price])
        writer.writerow(["P-103", "Sambal Jar (old pack)", "Food", "11.90"])


def write_customers() -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Customers"
    sheet.append(["Customer ID", "Customer Name", "Segment", "State", "Joined"])
    for n in range(1, 41):
        segment = rng.choice(SEGMENTS)
        if rng.random() < 0.15:
            segment = segment.lower()
        state = rng.choice(["Selangor", "Penang", "Johor", "Pahang", "Sabah", "Sarawak", "Kuala Lumpur", None])
        joined = date(2022, 1, 1) + timedelta(days=rng.randrange(0, 900))
        sheet.append([f"C{n:03d}", f"Customer {n}", segment, state, joined])
    notes = book.create_sheet("Notes")
    notes.append(["Exported from the old CRM. Segments were typed by hand."])
    book.save(HERE / "customers.xlsx")


if __name__ == "__main__":
    write_sales()
    write_products()
    write_customers()
