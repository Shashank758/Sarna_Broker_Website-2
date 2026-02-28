import os
import psycopg2
from dotenv import load_dotenv

load_dotenv('e:/Sarna_Broker_Website-2/.env')
db_url = os.environ.get("DATABASE_URL")

if "render" in db_url or "aws" in db_url:
    ssl_mode = "require"
else:
    ssl_mode = "prefer" # Allow local without SSL

try:
    con = psycopg2.connect(db_url, sslmode=ssl_mode)
except psycopg2.OperationalError:
    con = psycopg2.connect(db_url, sslmode="disable")

cur = con.cursor()

cur.execute("UPDATE miller_stock SET crop = 'rice' WHERE LOWER(crop) = 'chawal'")
print(f'Updated {cur.rowcount} miller_stock chawal to rice')

cur.execute("UPDATE miller_stock SET crop = 'mustard' WHERE LOWER(crop) = 'sarso'")
print(f'Updated {cur.rowcount} miller_stock sarso to mustard')

con.commit()
con.close()
