import sqlite3
import os

DB_PATH = "e:/Sarna_Broker_Website-2/database.db"

def check_latest_stock():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    
    print("--- Latest Miller Stock ---")
    cur.execute("""
        SELECT id, miller_id, crop, quantity, status, note, created_at 
        FROM miller_stock 
        ORDER BY created_at DESC 
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        print(f"ID: {row[0]}")
        print(f"Miller ID: {row[1]}")
        print(f"Crop: {row[2]}")
        print(f"Quantity: {row[3]}")
        print(f"Status: {row[4]}")
        print(f"Note: {row[5]}")
        print(f"Created At: {row[6]}")
    else:
        print("No stock found.")

    con.close()

if __name__ == "__main__":
    check_latest_stock()
