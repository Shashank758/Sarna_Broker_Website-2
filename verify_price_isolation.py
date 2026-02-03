import sqlite3
import os

DB_PATH = "e:/Sarna_Broker_Website-2/database.db"

def check_db():
    if not os.path.exists(DB_PATH):
        print("Database not found!")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # 1. Check schema
    print("Checking schema for miller_bookings...")
    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = {c[1]: c[2] for c in cur.fetchall()}
    
    if "price" in cols:
        print("✅ Column 'price' exists in miller_bookings.")
    else:
        print("❌ Column 'price' MISSING in miller_bookings.")
        con.close()
        return

    # 2. Check backfill
    print("\nChecking data sample...")
    cur.execute("SELECT id, quantity, price FROM miller_bookings LIMIT 5")
    rows = cur.fetchall()
    
    for r in rows:
        print(f"Booking ID: {r[0]}, Qty: {r[1]}, Price: {r[2]}")
        if r[2] is None:
            print(f"❌ Warning: Booking {r[0]} has NULL price!")
        else:
            print(f"✅ Booking {r[0]} has price.")

    # 3. Double check if any price is null
    cur.execute("SELECT COUNT(*) FROM miller_bookings WHERE price IS NULL")
    null_count = cur.fetchone()[0]
    if null_count == 0:
        print("\n✅ All bookings have a price set.")
    else:
        print(f"\n❌ {null_count} bookings still have NULL price!")

    con.close()

if __name__ == "__main__":
    check_db()
