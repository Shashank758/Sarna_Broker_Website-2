from app import get_db

conn = get_db()
cur = conn.cursor()

cur.execute("UPDATE miller_stock SET crop = 'rice' WHERE LOWER(crop) = 'chawal'")
print(f'Updated {cur.rowcount} miller_stock chawal to rice')

cur.execute("UPDATE miller_stock SET crop = 'mustard' WHERE LOWER(crop) = 'sarso'")
print(f'Updated {cur.rowcount} miller_stock sarso to mustard')

conn.commit()
conn.close()
