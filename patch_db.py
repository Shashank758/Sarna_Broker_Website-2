import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def patch_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not set")
        return

    try:
        if "render" in db_url or "aws" in db_url:
            ssl_mode = "require"
        else:
            ssl_mode = "disable" # Assume local for now to be safe/simple

        print(f"Connecting to DB...")
        con = psycopg2.connect(db_url, sslmode=ssl_mode)
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
        print("Database patched successfully.")
    except Exception as e:
        print(f"Error patching DB: {e}")

if __name__ == "__main__":
    patch_db()
