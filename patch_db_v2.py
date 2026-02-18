import os
import psycopg2

DB_URLS = [
    "postgresql://postgres:password@localhost/sarna_db",
    "postgresql://postgres@localhost/sarna_db",
    "postgresql://postgres:postgres@localhost/sarna_db",
    "postgresql://localhost/sarna_db?user=postgres",
    "postgresql://localhost/sarna_db"
]

def patch_db():
    for db_url in DB_URLS:
        print(f"Trying connection: {db_url.split('@')[-1] if '@' in db_url else db_url} ...")
        try:
            con = psycopg2.connect(db_url, sslmode="disable")
            con.autocommit = False
            cur = con.cursor()
            
            # Patch miller_bookings
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'miller_bookings'")
            columns = [row[0] for row in cur.fetchall()]
            
            if 'deadline_at' not in columns:
                print("Adding deadline_at to miller_bookings...")
                cur.execute("ALTER TABLE miller_bookings ADD COLUMN deadline_at TIMESTAMP")
            else:
                print("deadline_at already exists.")

            # Patch miller_stock
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'miller_stock'")
            stock_cols = [row[0] for row in cur.fetchall()]
            
            if 'duration' not in stock_cols:
                print("Adding duration to miller_stock...")
                cur.execute("ALTER TABLE miller_stock ADD COLUMN duration INTEGER")
            else:
                print("duration already exists.")

            con.commit()
            con.close()
            print("SUCCESS: Database patched successfully.")
            return
        except Exception as e:
            print(f"Failed: {e}")
            continue
    
    print("ALL ATTEMPTS FAILED.")

if __name__ == "__main__":
    patch_db()
