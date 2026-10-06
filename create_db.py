"""Create sales.db with sample products (your original script, made safer).

Run once:            python create_db.py
Recreate from zero:  python create_db.py --reset
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sales.db"

SAMPLE_PRODUCTS = [
    ("Toyota", 2022, "spare_part", "Brake Pad", 50.0),
    ("Toyota", 2022, "spare_part", "Air Filter", 20.0),
    ("Honda", 2021, "accessory", "Car Cover", 30.0),
    ("Nissan", 2023, "spare_part", "Oil Filter", 25.0),
    ("Suzuki", 2020, "accessory", "Seat Cover", 40.0),
]


def main(reset: bool = False):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if reset:
        cur.execute("DROP TABLE IF EXISTS products")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle TEXT NOT NULL,
            year INTEGER,
            product_type TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL
        )
        """
    )
    if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO products (vehicle, year, product_type, name, price) VALUES (?, ?, ?, ?, ?)",
            SAMPLE_PRODUCTS,
        )
        print(f"Inserted {len(SAMPLE_PRODUCTS)} sample products.")
    else:
        print("products table already has data - nothing inserted (use --reset to start over).")
    conn.commit()
    conn.close()
    print(f"Database ready: {DB_PATH}")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
