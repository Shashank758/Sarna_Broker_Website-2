from app import get_db

con = get_db()
cur = con.cursor()
cur.execute('SELECT id, user_id, title, is_read, created_at FROM notifications ORDER BY id DESC LIMIT 5;')
print("Notifications:")
for row in cur.fetchall():
    print(row)
cur.execute('SELECT id, name, role FROM users ORDER BY id DESC LIMIT 5;')
print("\nUsers:")
for row in cur.fetchall():
    print(row)

cur.execute('SELECT id, stock_id, buyer_id, status FROM miller_bookings ORDER BY id DESC LIMIT 5;')
print("\nBookings:")
for row in cur.fetchall():
    print(row)

con.close()
