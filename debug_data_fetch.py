from app import app, get_db

with app.app_context():
    con = get_db()
    cur = con.cursor()
    
    print("--- RECENT BOOKINGS (miller_bookings) ---")
    cur.execute("""
        SELECT id, stock_id, buyer_id, quantity, status, order_id, created_at 
        FROM miller_bookings 
        ORDER BY created_at DESC LIMIT 5
    """)
    rows = cur.fetchall()
    if not rows:
        print("No bookings found.")
    for row in rows:
        print(row)

    print("\n--- STOCK CHECK (miller_stock) ---")
    cur.execute("SELECT id, miller_id, status FROM miller_stock ORDER BY created_at DESC LIMIT 5")
    for row in cur.fetchall():
        print(row)
        
    con.close()
