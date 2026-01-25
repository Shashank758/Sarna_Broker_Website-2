from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import os
from werkzeug.utils import secure_filename
from twilio.rest import Client

app = Flask(__name__)
app.secret_key = "sarna_broker_secret_key"

# ---------------- CONFIG ----------------
UPLOAD_FOLDER = "static/uploads/crops"
BILL_FOLDER = "static/uploads/bills"
PROFILE_FOLDER = "static/uploads/miller_docs" 

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BILL_FOLDER, exist_ok=True)
os.makedirs(PROFILE_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["BILL_FOLDER"] = BILL_FOLDER
app.config["PROFILE_FOLDER"] = PROFILE_FOLDER 

# ---------------- SMS CONFIG ----------------
# Twilio credentials - set these as environment variables or hardcode below
# Option 1: Use environment variables (recommended for production)
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')

# Option 2: Hardcode credentials directly (for testing - NOT recommended for production)
# Uncomment and fill in your credentials if not using environment variables:
if not TWILIO_ACCOUNT_SID:
    TWILIO_ACCOUNT_SID = 'AC2c246686986b3541694856ea9f89f126'
if not TWILIO_AUTH_TOKEN:
    TWILIO_AUTH_TOKEN = 'ca266fd170ae0255f2487d7cc8a658c7'
if not TWILIO_PHONE_NUMBER:
    TWILIO_PHONE_NUMBER = '+16285009154'

# ---------------- SMS HELPER FUNCTION ----------------
def send_sms(to_phone, message_text):
    """Send SMS using Twilio. Returns True if successful, False otherwise."""
    # Check if credentials are configured
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        print(f"⚠️ SMS not configured. Missing credentials.")
        print(f"   Account SID: {'Set' if TWILIO_ACCOUNT_SID else 'Missing'}")
        print(f"   Auth Token: {'Set' if TWILIO_AUTH_TOKEN else 'Missing'}")
        print(f"   Phone Number: {'Set' if TWILIO_PHONE_NUMBER else 'Missing'}")
        print(f"   Would send to {to_phone}: {message_text}")
        return False
    
    if not to_phone:
        print("⚠️ No phone number provided for SMS")
        return False
    
    try:
        # Ensure phone number has country code (assume +91 for India if not present)
        original_phone = to_phone
        if not to_phone.startswith('+'):
            if to_phone.startswith('91'):
                to_phone = '+' + to_phone
            else:
                to_phone = '+91' + to_phone.lstrip('0')
        
        print(f"📱 Attempting to send SMS to {to_phone} (original: {original_phone})")
        print(f"   From: {TWILIO_PHONE_NUMBER}")
        print(f"   Message: {message_text[:50]}...")
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_text,
            from_=TWILIO_PHONE_NUMBER,
            to=to_phone
        )
        print(f"✅ SMS sent successfully to {to_phone}")
        print(f"   Message SID: {message.sid}")
        print(f"   Status: {message.status}")
        return True
    except Exception as e:
        print(f"❌ Failed to send SMS to {to_phone}")
        print(f"   Error Type: {type(e).__name__}")
        print(f"   Error Message: {str(e)}")
        # Print more details for common errors
        if "Invalid" in str(e) or "not found" in str(e).lower():
            print(f"   ⚠️ Check your Twilio credentials (Account SID, Auth Token)")
        if "phone number" in str(e).lower() or "number" in str(e).lower():
            print(f"   ⚠️ Check the phone number format: {to_phone}")
        return False

def clean_phone_number(phone):
    """Clean phone number by removing spaces, dashes, and other non-digit characters except +."""
    if not phone:
        return None
    phone_str = str(phone).strip()
    # Remove all characters except digits and +
    cleaned = ''.join(c for c in phone_str if c.isdigit() or c == '+')
    # Ensure + is at the beginning if present
    if '+' in cleaned and not cleaned.startswith('+'):
        cleaned = '+' + cleaned.replace('+', '')
    return cleaned if cleaned else None

def get_buyer_phone(buyer_id):
    """Get buyer phone number from buyer_profiles."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT phone FROM buyer_profiles WHERE buyer_id=?", (buyer_id,))
    result = cur.fetchone()
    con.close()
    phone = result[0] if result and result[0] else None
    if phone:
        phone = clean_phone_number(phone)
        print(f"📞 Retrieved buyer phone for buyer_id {buyer_id}: {phone}")
    else:
        print(f"⚠️ No phone number found for buyer_id {buyer_id}")
    return phone

def get_miller_phone(miller_id):
    """Get miller phone number from miller_profiles (prefer owner_phone, fallback to phone)."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT owner_phone, phone FROM miller_profiles WHERE miller_id=?", (miller_id,))
    result = cur.fetchone()
    con.close()
    phone = None
    if result:
        phone = result[0] if result[0] else (result[1] if result[1] else None)
    if phone:
        phone = clean_phone_number(phone)
        print(f"📞 Retrieved miller phone for miller_id {miller_id}: {phone}")
    else:
        print(f"⚠️ No phone number found for miller_id {miller_id}")
    return phone

def get_all_buyer_phones():
    """Get all buyer phone numbers."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT phone FROM buyer_profiles WHERE phone IS NOT NULL AND phone != ''")
    results = cur.fetchall()
    con.close()
    phones = [clean_phone_number(r[0]) for r in results if r[0]]
    phones = [p for p in phones if p]  # Remove None values
    print(f"📞 Retrieved {len(phones)} buyer phone numbers for broadcast")
    return phones

# ---------------- DATABASE ----------------
def get_db():
    return sqlite3.connect("database.db", timeout=10, check_same_thread=False)
def upgrade_db():
    con = get_db()
    cur = con.cursor()

    # Get existing columns
    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "decision_at" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN decision_at DATETIME")

    if "reason" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN reason TEXT")

    con.commit()
    con.close()

def init_db():
    con = get_db()
    cur = con.cursor()

    # USERS
    cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT UNIQUE,
    password TEXT,
    role TEXT,
    status TEXT DEFAULT 'pending'
)
""")

    # FARMER CROPS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS crops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        farmer_id INTEGER,
        crop TEXT,
        variety TEXT,
        price INTEGER,
        quantity INTEGER,
        location TEXT,
        image TEXT,
        sold INTEGER DEFAULT 0
    )
    """)

    # TRADE BILLS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        bill_file TEXT,
        phone TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # MILLER STOCK
    cur.execute("""
    CREATE TABLE IF NOT EXISTS miller_stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        miller_id INTEGER,
        crop TEXT,
        quantity INTEGER,
        price INTEGER,
        condition TEXT,
        bag_type TEXT,
        deduction INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # MILLER STOCK HISTORY
    cur.execute("""
    CREATE TABLE IF NOT EXISTS miller_stock_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER,
        miller_id INTEGER,
        old_price INTEGER,
        new_price INTEGER,
        old_quantity INTEGER,
        new_quantity INTEGER,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # BUYER BOOKINGS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS miller_bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER,
        buyer_id INTEGER,
        quantity INTEGER,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # ---------------- MILLER PROFILE ----------------
    cur.execute("""
CREATE TABLE IF NOT EXISTS miller_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    miller_id INTEGER UNIQUE,
    mill_name TEXT,
    phone TEXT,
    address TEXT,
    document TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

    # DEFAULT ADMIN
    # DEFAULT ADMIN (SAFE)
    cur.execute("SELECT id FROM users WHERE role='admin'")
    if not cur.fetchone():
     cur.execute("""
        INSERT INTO users (name, email, password, role, status)
        VALUES (?, ?, ?, ?, ?)
    """, (
        "Admin",
        "admin@sarna.com",
        "admin123",
        "admin",
        "approved"
    ))


    con.commit()
    con.close()
    
def upgrade_miller_stock_status():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_stock)")
    cols = [c[1] for c in cur.fetchall()]

    if "status" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN status TEXT DEFAULT 'open'
        """)

    con.commit()
    con.close()

init_db()
def upgrade_staff_system():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cur.fetchall()]

    if "is_staff" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN is_staff INTEGER DEFAULT 0")

    if "parent_miller_id" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN parent_miller_id INTEGER")

    con.commit()
    con.close()
def upgrade_loading_invoices():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS loading_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER,
        loaded_qty INTEGER,
        invoice_file TEXT,
        truck_number TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Add new fields to loading_invoices if they don't exist
    cur.execute("PRAGMA table_info(loading_invoices)")
    cols = [c[1] for c in cur.fetchall()]

    if "truck_number" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN truck_number TEXT")

    # QC fields
    if "qc_weight" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_weight INTEGER")
    if "qc_moisture" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_moisture REAL")
    if "qc_remarks" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_remarks TEXT")
    if "qc_status" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_status TEXT DEFAULT 'pending'")
    if "qc_at" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_at DATETIME")
    
    # Per-truck final invoice fields
    if "final_invoice_file" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN final_invoice_file TEXT")
    if "payment_status" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN payment_status TEXT DEFAULT 'pending'")
    if "payment_at" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN payment_at DATETIME")

    con.commit()
    con.close()

upgrade_loading_invoices()
   
def get_effective_user_id():
    # For miller staff → parent miller
    if session.get("role") == "miller" and session.get("is_staff"):
        parent_id = session.get("parent_miller_id")
        if parent_id:
            return parent_id

    # Otherwise → logged in user
    return session.get("user_id")
@app.route("/_fix_staff_miller_data")
def fix_staff_miller_data():
    con = get_db()
    cur = con.cursor()

    # Fix miller_stock
    cur.execute("""
        UPDATE miller_stock
        SET miller_id = (
            SELECT parent_miller_id
            FROM users
            WHERE users.id = miller_stock.miller_id
        )
        WHERE miller_id IN (
            SELECT id FROM users WHERE is_staff=1
        )
    """)

    con.commit()
    con.close()
    return "✅ Miller data fixed"



def upgrade_partial_loading():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "loaded_qty" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN loaded_qty INTEGER DEFAULT 0
        """)

    if "loading_status" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN loading_status TEXT DEFAULT 'pending'
        """)
    if "close_reason" not in cols:
        cur.execute("""
        ALTER TABLE miller_bookings
        ADD COLUMN close_reason TEXT
    """)

    if "closed_by" not in cols:
        cur.execute("""
        ALTER TABLE miller_bookings
        ADD COLUMN closed_by TEXT
    """)

    con.commit()
    con.close()

def upgrade_users_table():
    con = get_db()
    cur = con.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in cur.fetchall()]

    if "status" not in cols:
        cur.execute(
            "ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'pending'"
        )

    con.commit()
    con.close()

upgrade_db()
upgrade_users_table()
upgrade_partial_loading()
upgrade_staff_system()
upgrade_miller_stock_status()

def upgrade_miller_booking_truck_status():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "truck_status" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN truck_status TEXT DEFAULT 'pending'
        """)

    if "truck_remark" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN truck_remark TEXT
        """)

    if "loaded_at" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN loaded_at DATETIME
        """)

    con.commit()
    con.close()

def upgrade_buyer_profile_table():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS buyer_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_id INTEGER UNIQUE,
        shop_name TEXT,
        phone TEXT,
        address TEXT,
        document TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Ensure new columns exist for richer trader profile
    cur.execute("PRAGMA table_info(buyer_profiles)")
    cols = [c[1] for c in cur.fetchall()]
    if "owner_name" not in cols:
        cur.execute("ALTER TABLE buyer_profiles ADD COLUMN owner_name TEXT")
    if "gst_doc" not in cols:
        cur.execute("ALTER TABLE buyer_profiles ADD COLUMN gst_doc TEXT")
    if "license_doc" not in cols:
        cur.execute("ALTER TABLE buyer_profiles ADD COLUMN license_doc TEXT")
    if "other_doc" not in cols:
        cur.execute("ALTER TABLE buyer_profiles ADD COLUMN other_doc TEXT")

    con.commit()
    con.close()
upgrade_buyer_profile_table()
upgrade_miller_booking_truck_status()

def upgrade_miller_booking_bill():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "bill_document" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN bill_document TEXT
        """)

    con.commit()
    con.close()

def upgrade_miller_booking_qc():
    """Add miller quality-check fields to miller_bookings."""
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "qc_weight" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_weight INTEGER")
    if "qc_moisture" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_moisture REAL")
    if "qc_remarks" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_remarks TEXT")
    if "qc_status" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_status TEXT DEFAULT 'pending'")
    if "qc_at" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_at DATETIME")

    con.commit()
    con.close()

upgrade_miller_booking_bill()
upgrade_miller_booking_qc()

def upgrade_miller_booking_order_id():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "order_id" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN order_id TEXT
        """)
        
        # Generate order IDs for existing bookings
        cur.execute("SELECT id FROM miller_bookings ORDER BY id")
        existing_bookings = cur.fetchall()
        for idx, booking in enumerate(existing_bookings, start=1):
            order_id = f"S{10000 + idx}"
            cur.execute("""
                UPDATE miller_bookings
                SET order_id=?
                WHERE id=?
            """, (order_id, booking[0]))

    con.commit()
    con.close()
def upgrade_miller_payment_fields():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_bookings)")
    cols = [c[1] for c in cur.fetchall()]

    if "final_invoice" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN final_invoice TEXT
        """)

    if "payment_status" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN payment_status TEXT DEFAULT 'pending'
        """)

    if "payment_at" not in cols:
        cur.execute("""
            ALTER TABLE miller_bookings
            ADD COLUMN payment_at DATETIME
        """)

    con.commit()
    con.close()
def upgrade_payments_table():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER,
        miller_id INTEGER,
        buyer_id INTEGER,
        amount INTEGER,
        status TEXT DEFAULT 'pending',
        paid_at DATETIME,
        invoice_file TEXT
    )
    """)

    con.commit()
    con.close()
def upgrade_miller_stock_reserved_qty():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_stock)")
    cols = [c[1] for c in cur.fetchall()]

    if "reserved_qty" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN reserved_qty INTEGER DEFAULT 0
        """)

    con.commit()
    con.close()

upgrade_miller_booking_qc()
upgrade_miller_booking_order_id()
upgrade_miller_payment_fields()
upgrade_payments_table()
upgrade_miller_stock_reserved_qty()

def generate_next_order_id():
    """Generate next order ID in format S10001, S10002, etc."""
    con = get_db()
    cur = con.cursor()
    
    # Get the highest order number
    cur.execute("""
        SELECT order_id FROM miller_bookings 
        WHERE order_id IS NOT NULL AND order_id LIKE 'S%'
        ORDER BY CAST(SUBSTR(order_id, 2) AS INTEGER) DESC
        LIMIT 1
    """)
    result = cur.fetchone()
    
    con.close()
    
    if result and result[0]:
        # Extract number from existing order_id (e.g., "S10001" -> 10001)
        try:
            last_number = int(result[0][1:])
            next_number = last_number + 1
        except ValueError:
            next_number = 10001
    else:
        # Start from S10001
        next_number = 10001
    
    return f"S{next_number}"

def upgrade_miller_profile_table():
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(miller_profiles)")
    cols = [c[1] for c in cur.fetchall()]

    if "mill_name" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN mill_name TEXT")

    if "phone" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN phone TEXT")

    if "address" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN address TEXT")

    if "document" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN document TEXT")

    # Add new fields for multiple documents and phone numbers
    if "owner_phone" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN owner_phone TEXT")
    
    if "accountant_phone" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN accountant_phone TEXT")
    
    if "staff_phone" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN staff_phone TEXT")
    
    if "gst_doc" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN gst_doc TEXT")
    
    if "mandi_doc" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN mandi_doc TEXT")
    
    if "other_doc" not in cols:
        cur.execute("ALTER TABLE miller_profiles ADD COLUMN other_doc TEXT")

    con.commit()
    con.close()

# Call the upgrade function after it's defined
upgrade_miller_profile_table()

# ---------------- AUTH ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "login.html",
                error="Please enter email and password"
            )

        con = get_db()
        cur = con.cursor()
        cur.execute(
            "SELECT id, name, email, password, role, status, is_staff, parent_miller_id FROM users WHERE email=? AND password=?",
            (email, password)
        )
        user = cur.fetchone()
        con.close()

        if not user:
            return render_template(
                "login.html",
                error="Invalid credentials"
            )

        if user[5] != "approved":
            return render_template(
                "login.html",
                error="⛔ Your account is not approved by admin yet"
            )

        session["user_id"] = user[0] 
        session["role"] = user[4]
        session["is_staff"] = user[6] if user[6] else 0
        session["parent_miller_id"] = user[7] if user[7] else None

        if user[4] == "farmer":
            return redirect("/my_commodity")
        elif user[4] == "buyer":
            return redirect("/market")
        elif user[4] == "miller":
            return redirect("/miller")
        else:
            return redirect("/admin")

    return render_template("login.html")


@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        con = get_db()
        cur = con.cursor()
        cur.execute("""
        INSERT INTO users (name,email,password,role)
        VALUES (?,?,?,?)
        """, (
            request.form["name"],
            request.form["email"],
            request.form["password"],
            request.form["role"]
        ))
        con.commit()
        con.close()
        return redirect("/")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- FARMER ----------------
@app.route("/post_crop", methods=["GET","POST"])
def post_crop():
    if session.get("role") != "farmer":
        return redirect("/")

    if request.method == "POST":
        image = request.files.get("image")
        filename = None
        if image and image.filename:
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        con = get_db()
        cur = con.cursor()
        cur.execute("""
        INSERT INTO crops (farmer_id,crop,variety,price,quantity,location,image)
        VALUES (?,?,?,?,?,?,?)
        """, (
           get_effective_user_id(),
            request.form["crop"],
            request.form["variety"],
            request.form["price"],
            request.form["quantity"],
            request.form["location"],
            filename
        ))
        con.commit()
        con.close()
        return redirect("/my_commodity")

    return render_template("post_crop.html")

@app.route("/my_commodity")
def my_commodity():
    if session.get("role") != "farmer":
        return redirect("/")
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM crops WHERE farmer_id=?", (get_effective_user_id(),))
    crops = cur.fetchall()
    con.close()
    return render_template("my_commodity.html", crops=crops)

# ---------------- MILLER ----------------
@app.route("/miller", methods=["GET", "POST"])
def miller_dashboard():    

    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()

    con = get_db()
    cur = con.cursor()

    # ❌ STAFF CANNOT POST STOCK
    if request.method == "POST":
        if session.get("is_staff"):
            return redirect("/miller")   # 🔒 block staff

        cur.execute("""
            INSERT INTO miller_stock
            (miller_id, crop, quantity, price, condition, bag_type, deduction)
            VALUES (?,?,?,?,?,?,?)
        """, (
            miller_id,
            request.form["crop"],
            request.form["quantity"],
            request.form["price"],
            request.form["condition"],
            request.form["bag_type"],
            request.form["deduction"]
        ))
        # Ensure the stock is visible in buyer market (market filters status='open')
        cur.execute("UPDATE miller_stock SET status='open' WHERE id=last_insert_rowid()")
        con.commit()
        
        # 📱 Send SMS to all buyers about new stock
        crop = request.form["crop"]
        quantity = request.form["quantity"]
        price = request.form["price"]
        buyer_phones = get_all_buyer_phones()
        message = f"🆕 New stock available! {crop} - Qty: {quantity}, Price: ₹{price}/unit. Check the market for details."
        for phone in buyer_phones:
            send_sms(phone, message)


# ✅ LIVE STOCKS
    cur.execute("""
    SELECT *
    FROM miller_stock
    WHERE miller_id=?
    ORDER BY created_at DESC
""", (miller_id,))
    stocks = cur.fetchall()

# ✅ BUYER BOOKINGS
    cur.execute("""
SELECT
    mb.id,              -- 0 booking_id
    u.name,             -- 1 buyer_name
    ms.crop,            -- 2 crop
    mb.quantity,        -- 3 booked
    mb.status,          -- 4 booking_status
    mb.reason,          -- 5 reason
    mb.decision_at,     -- 6 decision_at
    mb.loaded_qty,      -- 7 loaded
    mb.loading_status,  -- 8 loading_status
    mb.close_reason,    -- 9 close_reason
    mb.order_id,        -- 10 order_id
    mb.qc_weight,       -- 11 qc_weight
    mb.qc_moisture,     -- 12 qc_moisture
    mb.qc_remarks,      -- 13 qc_remarks
    mb.qc_status,       -- 14 qc_status
    mb.qc_at,           -- 15 qc_at

    IFNULL(p.status,'pending')     AS payment_status,  -- 16 ✅
    p.invoice_file                 AS final_invoice,   -- 17 ✅
    p.paid_at                      AS payment_at       -- 18 ✅

FROM miller_bookings mb
JOIN users u ON mb.buyer_id = u.id
JOIN miller_stock ms ON mb.stock_id = ms.id
LEFT JOIN payments p ON p.booking_id = mb.id
WHERE ms.miller_id=?
ORDER BY mb.created_at DESC
""", (miller_id,))
    bookings = cur.fetchall()

    # 🔹 FETCH PER-TRUCK LOADING INVOICES WITH QC DATA AND FINAL INVOICE
    cur.execute("""
    SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
           qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
           final_invoice_file, payment_status, payment_at
    FROM loading_invoices
    ORDER BY created_at ASC
""")
    rows = cur.fetchall()

# Group invoices by booking_id with QC data and final invoice
    invoices_map = {}
    for r in rows:
     invoices_map.setdefault(r[1], []).append({
        "id": r[0],  # invoice id
        "qty": r[2],
        "file": r[3],
        "truck_number": r[4],
        "date": r[5],
        "qc_weight": r[6],
        "qc_moisture": r[7],
        "qc_remarks": r[8],
        "qc_status": r[9] or "pending",
        "qc_at": r[10],
        "final_invoice_file": r[11],
        "payment_status": r[12] or "pending",
        "payment_at": r[13]
    })

    con.close()

    return render_template(
    "miller.html",
    stocks=stocks,
    bookings=bookings,
    invoices_map=invoices_map
)
@app.route("/miller/approved")
def miller_approved_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # ✅ Fetch ALL approved bookings for this miller
    cur.execute("""
        SELECT
            mb.id,              -- 0 booking_id
            u.name,             -- 1 buyer
            ms.crop,            -- 2 crop
            mb.quantity,        -- 3 booked
            mb.status,          -- 4
            mb.reason,          -- 5
            mb.decision_at,     -- 6
            mb.loaded_qty,      -- 7 loaded
            mb.loading_status,  -- 8
            mb.close_reason,    -- 9
            mb.order_id,        -- 10

            mb.qc_weight,       -- 11
            mb.qc_moisture,     -- 12
            mb.qc_remarks,      -- 13
            mb.qc_status,       -- 14
            mb.qc_at,           -- 15

            IFNULL(p.status,'pending') AS payment_status, -- 16
            p.invoice_file,                                -- 17
            p.paid_at                                     -- 18
        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        WHERE
            ms.miller_id = ?
            AND mb.status = 'approved'
        ORDER BY mb.created_at DESC
    """, (miller_id,))

    approved = cur.fetchall()

    # ✅ Fetch per-truck invoices (WITH QC AND FINAL INVOICE)
    cur.execute("""
        SELECT
            id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
            qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
            final_invoice_file, payment_status, payment_at
        FROM loading_invoices
        ORDER BY created_at ASC
    """)

    rows = cur.fetchall()
    invoices_map = {}
    for r in rows:
        invoices_map.setdefault(r[1], []).append({
            "id": r[0],
            "qty": r[2],
            "file": r[3],
            "truck_number": r[4],
            "date": r[5],
            "qc_weight": r[6],
            "qc_moisture": r[7],
            "qc_remarks": r[8],
            "qc_status": r[9] or "pending",
            "qc_at": r[10],
            "final_invoice_file": r[11],
            "payment_status": r[12] or "pending",
            "payment_at": r[13]
        })

    con.close()

    return render_template(
        "miller_approved.html",
        approved=approved,
        invoices_map=invoices_map
    )


@app.route("/miller/qc")
def miller_qc_page():

    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # 1️⃣ Fetch bookings (same as miller dashboard)
    cur.execute("""
    SELECT
        mb.id, u.name, ms.crop, mb.quantity,
        mb.status, mb.reason, mb.decision_at,
        mb.loaded_qty, mb.loading_status,
        mb.close_reason, mb.order_id,

        mb.qc_weight, mb.qc_moisture, mb.qc_remarks,
        mb.qc_status, mb.qc_at,

        IFNULL(p.status,'pending'),
        p.invoice_file,
        p.paid_at
    FROM miller_bookings mb
    JOIN users u ON mb.buyer_id = u.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    LEFT JOIN payments p ON p.booking_id = mb.id
    WHERE ms.miller_id=?
    ORDER BY mb.created_at DESC
    """, (miller_id,))

    bookings = cur.fetchall()

    # 2️⃣ Fetch per-truck invoices (WITH QC AND FINAL INVOICE)
    cur.execute("""
    SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
           qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
           final_invoice_file, payment_status, payment_at
    FROM loading_invoices
    ORDER BY created_at ASC
    """)
    rows = cur.fetchall()

    invoices_map = {}
    for r in rows:
        invoices_map.setdefault(r[1], []).append({
            "id": r[0],
            "qty": r[2],
            "file": r[3],
            "truck_number": r[4],
            "date": r[5],
            "qc_weight": r[6],
            "qc_moisture": r[7],
            "qc_remarks": r[8],
            "qc_status": r[9] or "pending",
            "qc_at": r[10],
            "final_invoice_file": r[11],
            "payment_status": r[12] or "pending",
            "payment_at": r[13]
        })

    # 3️⃣ FILTER ONLY COMPLETED LOADING → QC REQUIRED
    completed_loading_qc = []
    for b in bookings:
        booked = b[3]
        loaded = b[7] or 0
        payment_status = b[16]
        final_invoice = b[17]

        if loaded == booked and not final_invoice and payment_status != 'paid':
            completed_loading_qc.append(b)

    con.close()

    return render_template(
        "miller_qc.html",
        completed_loading_qc=completed_loading_qc,
        invoices_map=invoices_map
    )


@app.route("/miller/final-hisab")
def miller_final_hisab_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # ✅ Fetch all bookings with loaded trucks for this miller
    cur.execute("""
        SELECT
            mb.id,              -- 0 booking_id
            u.name,             -- 1 buyer_name
            ms.crop,            -- 2 crop
            mb.quantity,        -- 3 booked
            mb.status,          -- 4 booking_status
            mb.reason,          -- 5 reason
            mb.decision_at,     -- 6 decision_at
            mb.loaded_qty,      -- 7 loaded
            mb.loading_status,  -- 8 loading_status
            mb.close_reason,    -- 9 close_reason
            mb.order_id,        -- 10 order_id

            mb.qc_weight,       -- 11
            mb.qc_moisture,     -- 12
            mb.qc_remarks,      -- 13
            mb.qc_status,       -- 14
            mb.qc_at,           -- 15

            IFNULL(p.status,'pending') AS payment_status, -- 16
            p.invoice_file                 AS final_invoice, -- 17
            p.paid_at                      AS payment_at,    -- 18
            ms.price                       AS price          -- 19

        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        WHERE
            ms.miller_id = ?
            AND mb.loading_status IN ('loaded', 'partial')
        ORDER BY mb.created_at DESC
    """, (miller_id,))

    all_bookings = cur.fetchall()

    # ✅ Fetch per-truck invoices + QC + FINAL INVOICE
    cur.execute("""
        SELECT
            li.id,
            li.booking_id,
            li.loaded_qty,
            li.invoice_file,
            li.truck_number,
            li.created_at,
            li.qc_weight,
            li.qc_moisture,
            li.qc_remarks,
            li.qc_status,
            li.qc_at,
            li.final_invoice_file,
            li.payment_status,
            li.payment_at
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE ms.miller_id = ?
        ORDER BY li.created_at ASC
    """, (miller_id,))
    rows = cur.fetchall()

    invoices_map = {}
    for r in rows:
        invoices_map.setdefault(r[1], []).append({
            "id": r[0],
            "qty": r[2],
            "file": r[3],
            "truck_number": r[4],
            "date": r[5],
            "qc_weight": r[6],
            "qc_moisture": r[7],
            "qc_remarks": r[8],
            "qc_status": r[9] or "pending",
            "qc_at": r[10],
            "final_invoice_file": r[11],
            "payment_status": r[12] or "pending",
            "payment_at": r[13]
        })

    con.close()

    return render_template(
        "miller_final_hisab.html",
        all_bookings=all_bookings,
        invoices_map=invoices_map
    )


@app.route("/miller/rejected")
def miller_rejected_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # 🔴 Fetch rejected / declined bookings
    cur.execute("""
        SELECT
            mb.id,              -- 0 booking_id
            u.name,             -- 1 buyer
            ms.crop,            -- 2 crop
            mb.quantity,        -- 3 qty
            mb.status,          -- 4
            mb.reason,          -- 5 rejection reason
            mb.decision_at,     -- 6
            mb.loaded_qty,      -- 7
            mb.loading_status,  -- 8
            mb.close_reason,    -- 9
            mb.order_id         -- 10
        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE
            ms.miller_id = ?
            AND mb.status = 'declined'
        ORDER BY mb.created_at DESC
    """, (miller_id,))

    rejected = cur.fetchall()
    con.close()

    return render_template("miller_rejected.html", rejected=rejected)
@app.route("/miller/payment-completed")
def miller_payment_completed_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            mb.id,              -- 0 booking_id
            u.name,             -- 1 buyer
            ms.crop,            -- 2 crop
            mb.quantity,        -- 3 booked
            mb.status,          -- 4
            mb.reason,          -- 5
            mb.decision_at,     -- 6
            mb.loaded_qty,      -- 7 loaded
            mb.loading_status,  -- 8
            mb.close_reason,    -- 9
            mb.order_id,        -- 10

            mb.qc_weight,       -- 11
            mb.qc_moisture,     -- 12
            mb.qc_remarks,      -- 13
            mb.qc_status,       -- 14
            mb.qc_at,           -- 15

            p.status,           -- 16 payment_status
            p.invoice_file,     -- 17 final_invoice
            p.paid_at           -- 18 payment_at
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON mb.buyer_id = u.id
        JOIN payments p ON p.booking_id = mb.id
        WHERE
            ms.miller_id = ?
            AND p.status = 'paid'
        ORDER BY p.paid_at DESC
    """, (miller_id,))

    payment_completed = cur.fetchall()
    con.close()

    return render_template(
        "miller_payment_completed.html",
        payment_completed=payment_completed
    )


@app.route("/miller/upload_final_invoice/<int:booking_id>", methods=["POST"])
def miller_upload_final_invoice(booking_id):
    """Upload final invoice (final hisab) separately from payment completion."""
    if session.get("role") != "miller":
        return redirect("/")

    invoice = request.files.get("final_invoice")
    if not invoice or invoice.filename == "":
        return redirect("/miller")

    filename = secure_filename(invoice.filename)
    invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Only allow if fully loaded and all trucks QC verified
    cur.execute("""
        SELECT mb.loaded_qty, mb.quantity, ms.miller_id
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=? AND ms.miller_id=? AND mb.loading_status='loaded'
    """, (booking_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/miller")

    # ✅ Check if payment record exists
    cur.execute("SELECT id FROM payments WHERE booking_id=?", (booking_id,))
    existing_payment = cur.fetchone()
    
    if existing_payment:
        # Update existing payment record
        cur.execute("""
            UPDATE payments
            SET invoice_file=?,
                status='pending'
            WHERE booking_id=?
        """, (filename, booking_id))
    else:
        # Insert new payment record
        cur.execute("""
            INSERT INTO payments
            (booking_id, miller_id, buyer_id, amount, status, invoice_file)
            SELECT
                mb.id,
                ms.miller_id,
                mb.buyer_id,
                (mb.loaded_qty * ms.price),
                'pending',
                ?
            FROM miller_bookings mb
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE mb.id=?
        """, (filename, booking_id))
    
    # 📱 Send SMS to buyer about final invoice
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, mb.loaded_qty, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (booking_id,))
    invoice_info = cur.fetchone()
    if invoice_info:
        buyer_id, order_id, loaded_qty, price = invoice_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            total_amount = loaded_qty * price
            message = f"📄 Final invoice uploaded for Order {order_id}. Amount: ₹{total_amount}. Please review and proceed with payment."
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect("/miller")

@app.route("/miller/mark_payment_done/<int:booking_id>", methods=["POST"])
def miller_mark_payment_done(booking_id):
    """Mark payment as done after final invoice is uploaded."""
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # ✅ Only allow if final invoice exists
    cur.execute("""
        SELECT p.invoice_file, ms.miller_id
        FROM payments p
        JOIN miller_bookings mb ON p.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE p.booking_id=? AND ms.miller_id=? AND p.invoice_file IS NOT NULL
    """, (booking_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/miller")

    # ✅ Update payment status to 'paid'
    cur.execute("""
        UPDATE payments
        SET status='paid',
            paid_at=CURRENT_TIMESTAMP
        WHERE booking_id=?
    """, (booking_id,))
    
    # 📱 Send SMS to buyer about payment completion
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, p.amount
        FROM miller_bookings mb
        JOIN payments p ON p.booking_id = mb.id
        WHERE mb.id=?
    """, (booking_id,))
    payment_info = cur.fetchone()
    if payment_info:
        buyer_id, order_id, amount = payment_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"✅ Payment received for Order {order_id}. Amount: ₹{amount}. Thank you!"
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect("/miller")

@app.route("/miller/edit_final_invoice/<int:booking_id>", methods=["POST"])
def miller_edit_final_invoice(booking_id):
    """Edit/replace final invoice (final hisab)."""
    if session.get("role") != "miller":
        return redirect("/")

    invoice = request.files.get("final_invoice")
    if not invoice or invoice.filename == "":
        return redirect("/miller")

    filename = secure_filename(invoice.filename)
    invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Verify this booking belongs to the miller
    cur.execute("""
        SELECT ms.miller_id
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=? AND ms.miller_id=?
    """, (booking_id, get_effective_user_id()))

    if not cur.fetchone():
        con.close()
        return redirect("/miller")

    # ✅ Update final invoice (keep payment status as is)
    cur.execute("""
        UPDATE payments
        SET invoice_file=?
        WHERE booking_id=?
    """, (filename, booking_id))
    
    # 📱 Send SMS to buyer about invoice update
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id
        FROM miller_bookings mb
        WHERE mb.id=?
    """, (booking_id,))
    invoice_info = cur.fetchone()
    if invoice_info:
        buyer_id, order_id = invoice_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"📄 Final invoice updated for Order {order_id}. Please review the updated invoice."
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect("/miller")

@app.route("/miller/upload_truck_final_invoice/<int:invoice_id>", methods=["POST"])
def miller_upload_truck_final_invoice(invoice_id):
    """Upload final invoice (final hisab) for a specific truck/loading invoice."""
    if session.get("role") != "miller":
        return redirect("/")

    final_invoice = request.files.get("truck_final_invoice")
    if not final_invoice or final_invoice.filename == "":
        return redirect(request.referrer or "/miller")

    filename = secure_filename(final_invoice.filename)
    final_invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Verify this invoice belongs to this miller and QC is verified
    cur.execute("""
        SELECT li.id, li.booking_id, li.truck_number, li.loaded_qty, mb.order_id
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE li.id=? AND ms.miller_id=? AND li.qc_status='verified'
    """, (invoice_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    invoice_db_id, booking_id, truck_number, loaded_qty, order_id = row

    # ✅ Update truck final invoice
    cur.execute("""
        UPDATE loading_invoices
        SET final_invoice_file=?,
            payment_status='pending'
        WHERE id=?
    """, (filename, invoice_id))

    # 📱 Send SMS to buyer about truck final invoice
    cur.execute("""
        SELECT mb.buyer_id, ms.crop, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (booking_id,))
    invoice_info = cur.fetchone()
    if invoice_info:
        buyer_id, crop, price = invoice_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            truck_info = f" (Truck: {truck_number})" if truck_number else ""
            total_amount = loaded_qty * price
            message = f"📄 Final invoice uploaded for Order {order_id}{truck_info}. Qty: {loaded_qty}, Amount: ₹{total_amount}. Please review."
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect(request.referrer or "/miller")

@app.route("/miller/edit_truck_final_invoice/<int:invoice_id>", methods=["POST"])
def miller_edit_truck_final_invoice(invoice_id):
    """Edit/replace final invoice for a specific truck."""
    if session.get("role") != "miller":
        return redirect("/")

    final_invoice = request.files.get("truck_final_invoice")
    if not final_invoice or final_invoice.filename == "":
        return redirect(request.referrer or "/miller")

    filename = secure_filename(final_invoice.filename)
    final_invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Verify this invoice belongs to this miller
    cur.execute("""
        SELECT li.id, li.booking_id, mb.order_id
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE li.id=? AND ms.miller_id=?
    """, (invoice_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    invoice_db_id, booking_id, order_id = row

    # ✅ Update truck final invoice (keep payment status as is)
    cur.execute("""
        UPDATE loading_invoices
        SET final_invoice_file=?
        WHERE id=?
    """, (filename, invoice_id))

    # 📱 Send SMS to buyer about invoice update
    cur.execute("""
        SELECT mb.buyer_id
        FROM miller_bookings mb
        WHERE mb.id=?
    """, (booking_id,))
    invoice_info = cur.fetchone()
    if invoice_info:
        buyer_id = invoice_info[0]
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"📄 Final invoice updated for Order {order_id}. Please review the updated invoice."
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect(request.referrer or "/miller")

@app.route("/miller/mark_truck_payment_done/<int:invoice_id>", methods=["POST"])
def miller_mark_truck_payment_done(invoice_id):
    """Mark payment as done for a specific truck."""
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # ✅ Verify this invoice belongs to this miller and has final invoice
    cur.execute("""
        SELECT li.id, li.booking_id, li.final_invoice_file, li.loaded_qty, mb.order_id
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE li.id=? AND ms.miller_id=? AND li.final_invoice_file IS NOT NULL
    """, (invoice_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    invoice_db_id, booking_id, final_invoice_file, loaded_qty, order_id = row

    # ✅ Update payment status to 'paid'
    cur.execute("""
        UPDATE loading_invoices
        SET payment_status='paid',
            payment_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (invoice_id,))

    # 📱 Send SMS to buyer about payment completion
    cur.execute("""
        SELECT mb.buyer_id, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (booking_id,))
    payment_info = cur.fetchone()
    if payment_info:
        buyer_id, price = payment_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            amount = loaded_qty * price
            message = f"✅ Payment received for Order {order_id} (Truck). Amount: ₹{amount}. Thank you!"
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect(request.referrer or "/miller")

  
@app.route("/miller/upload_bill/<int:booking_id>", methods=["POST"])
def upload_booking_bill(booking_id):
    if session.get("role") != "miller":
        return redirect("/")

    # Verify the booking belongs to this miller
    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT mb.id
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=? AND ms.miller_id=? AND mb.loading_status='loaded'
    """, (booking_id, miller_id))
    
    booking = cur.fetchone()
    if not booking:
        con.close()
        return redirect("/miller")

    # Handle file upload
    bill_file = request.files.get("bill_document")
    filename = None
    
    if bill_file and bill_file.filename:
        filename = secure_filename(bill_file.filename)
        # Add booking_id to filename to avoid conflicts
        name, ext = os.path.splitext(filename)
        filename = f"booking_{booking_id}_{name}{ext}"
        bill_file.save(os.path.join(app.config["BILL_FOLDER"], filename))

    # Update booking with bill document
    if filename:
        cur.execute("""
            UPDATE miller_bookings
            SET bill_document=?
            WHERE id=?
        """, (filename, booking_id))
        con.commit()

    con.close()
    return redirect("/miller")
    

    
@app.route("/miller/profile", methods=["GET", "POST"])
def miller_profile():

    # 🚫 Block staff completely
    if session.get("role") != "miller" or session.get("is_staff"):
        return redirect("/")

    # Ensure database is upgraded
    upgrade_miller_profile_table()

    miller_id = get_effective_user_id()


    con = get_db()
    cur = con.cursor()

    # ✅ Fetch miller profile
    cur.execute(
        "SELECT * FROM miller_profiles WHERE miller_id=?",
        (miller_id,)
    )
    profile = cur.fetchone()

    if request.method == "POST":
        try:
            mill_name = request.form.get("mill_name", "").strip()
            owner_phone = request.form.get("owner_phone", "").strip()
            accountant_phone = request.form.get("accountant_phone", "").strip()
            staff_phone = request.form.get("staff_phone", "").strip()
            address = request.form.get("address", "").strip()
            
            if not mill_name or not address:
                con.close()
                return render_template("miller_profile.html", profile=profile, error="Mill name and address are required")

            # Handle multiple document uploads
            gst_doc = request.files.get("gst_doc")
            mandi_doc = request.files.get("mandi_doc")
            other_doc = request.files.get("other_doc")
            
            # Get existing filenames if profile exists
            # Column order: id(0), miller_id(1), mill_name(2), phone(3), address(4), document(5), 
            # created_at(6), owner_phone(7), accountant_phone(8), staff_phone(9), 
            # gst_doc(10), mandi_doc(11), other_doc(12)
            gst_filename = None
            mandi_filename = None
            other_filename = None
            
            if profile and len(profile) > 12:
                gst_filename = profile[10] if profile[10] else None
                mandi_filename = profile[11] if profile[11] else None
                other_filename = profile[12] if profile[12] else None
            
            # Save GST document (only if new file is uploaded)
            if gst_doc and gst_doc.filename:
                gst_filename = secure_filename(gst_doc.filename)
                name, ext = os.path.splitext(gst_filename)
                gst_filename = f"gst_{miller_id}_{name}{ext}"
                gst_doc.save(os.path.join(app.config["PROFILE_FOLDER"], gst_filename))
            
            # Save Mandi document (only if new file is uploaded)
            if mandi_doc and mandi_doc.filename:
                mandi_filename = secure_filename(mandi_doc.filename)
                name, ext = os.path.splitext(mandi_filename)
                mandi_filename = f"mandi_{miller_id}_{name}{ext}"
                mandi_doc.save(os.path.join(app.config["PROFILE_FOLDER"], mandi_filename))
            
            # Save Other document (only if new file is uploaded)
            if other_doc and other_doc.filename:
                other_filename = secure_filename(other_doc.filename)
                name, ext = os.path.splitext(other_filename)
                other_filename = f"other_{miller_id}_{name}{ext}"
                other_doc.save(os.path.join(app.config["PROFILE_FOLDER"], other_filename))

            if profile:
                cur.execute("""
                    UPDATE miller_profiles
                    SET mill_name=?, owner_phone=?, accountant_phone=?, staff_phone=?, 
                        address=?, gst_doc=?, mandi_doc=?, other_doc=?
                    WHERE miller_id=?
                """, (mill_name, owner_phone, accountant_phone, staff_phone, address, 
                      gst_filename, mandi_filename, other_filename, miller_id))
            else:
                cur.execute("""
                    INSERT INTO miller_profiles
                    (miller_id, mill_name, owner_phone, accountant_phone, staff_phone, 
                     address, gst_doc, mandi_doc, other_doc)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (miller_id, mill_name, owner_phone, accountant_phone, staff_phone, 
                      address, gst_filename, mandi_filename, other_filename))

            con.commit()
            con.close()
            return redirect("/miller/profile")
        except Exception as e:
            con.rollback()
            con.close()
            print(f"Error saving miller profile: {str(e)}")
            return render_template("miller_profile.html", profile=profile, error=f"Error saving profile: {str(e)}")

    con.close()
    return render_template("miller_profile.html", profile=profile)

@app.route("/miller/create_staff", methods=["POST"])
def create_miller_staff():
    if session.get("role") != "miller" or session.get("is_staff"):
        return redirect("/")

    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]

    parent_miller_id = get_effective_user_id()  # 🔑 IMPORTANT

    con = get_db()
    cur = con.cursor()

    # prevent duplicate email
    cur.execute("SELECT id FROM users WHERE email=?", (email,))
    if cur.fetchone():
        con.close()
        return redirect("/miller")

    cur.execute("""
        INSERT INTO users
        (name, email, password, role, status, is_staff, parent_miller_id)
        VALUES (?, ?, ?, 'miller', 'approved', 1, ?)
    """, (name, email, password, parent_miller_id))

    con.commit()
    con.close()

    return redirect("/miller")


@app.route("/buyer/profile", methods=["GET", "POST"])
def buyer_profile():
    if session.get("role") != "buyer":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # Fetch existing profile
    cur.execute(
        "SELECT * FROM buyer_profiles WHERE buyer_id=?",
        (session["user_id"],)
    )
    row = cur.fetchone()
    cols = [d[0] for d in cur.description] if row else []
    profile = dict(zip(cols, row)) if row else None

    if request.method == "POST":
        shop_name = request.form["shop_name"]
        owner_name = request.form.get("owner_name")
        phone = request.form["phone"]
        address = request.form["address"]

        # Existing file names
        gst_existing = profile.get("gst_doc") if profile else None
        lic_existing = profile.get("license_doc") if profile else None
        other_existing = profile.get("other_doc") if profile else None

        # Uploads
        gst_doc = request.files.get("gst_doc")
        license_doc = request.files.get("license_doc")
        other_doc = request.files.get("other_doc")

        gst_filename = gst_existing
        if gst_doc and gst_doc.filename:
            gst_filename = secure_filename(gst_doc.filename)
            gst_doc.save(os.path.join(app.config["PROFILE_FOLDER"], gst_filename))

        lic_filename = lic_existing
        if license_doc and license_doc.filename:
            lic_filename = secure_filename(license_doc.filename)
            license_doc.save(os.path.join(app.config["PROFILE_FOLDER"], lic_filename))

        other_filename = other_existing
        if other_doc and other_doc.filename:
            other_filename = secure_filename(other_doc.filename)
            other_doc.save(os.path.join(app.config["PROFILE_FOLDER"], other_filename))

        if profile:
            cur.execute("""
                UPDATE buyer_profiles
                SET shop_name=?, owner_name=?, phone=?, address=?, gst_doc=?, license_doc=?, other_doc=?
                WHERE buyer_id=?
            """, (
                shop_name, owner_name, phone, address,
                gst_filename, lic_filename, other_filename,
                session["user_id"]
            ))
        else:
            cur.execute("""
                INSERT INTO buyer_profiles
                (buyer_id, shop_name, owner_name, phone, address, gst_doc, license_doc, other_doc)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                session["user_id"], shop_name, owner_name, phone, address,
                gst_filename, lic_filename, other_filename
            ))

        con.commit()
        con.close()
        return redirect("/buyer/profile")

    con.close()
    return render_template("buyer_profile.html", profile=profile)
@app.route("/buyer/close_remaining/<int:booking_id>", methods=["POST"])
def buyer_close_remaining(booking_id):
    if session.get("role") != "buyer":
        return redirect("/market")

    reason = request.form.get("reason", "").strip()
    if not reason:
        # Redirect back to referring page or default to /market
        return redirect(request.referrer or "/market")

    con = get_db()
    cur = con.cursor()

    # Fetch booking
    cur.execute("""
        SELECT stock_id, quantity, loaded_qty, status
        FROM miller_bookings
        WHERE id=? AND buyer_id=? AND status='approved'
    """, (booking_id, session["user_id"]))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/market")

    stock_id, booked_qty, loaded_qty, status = row
    loaded_qty = loaded_qty or 0
    remaining_qty = booked_qty - loaded_qty

    # Return remaining stock to miller
    if remaining_qty > 0:
        cur.execute("""
            UPDATE miller_stock
            SET quantity = quantity + ?
            WHERE id=?
        """, (remaining_qty, stock_id))

    # Close booking partially
    cur.execute("""
        UPDATE miller_bookings
        SET
            loading_status='partial_closed',
            close_reason=?,
            closed_by='buyer',
            decision_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (reason, booking_id))
    
    # 📱 Send SMS to miller about partial closure
    cur.execute("""
        SELECT ms.miller_id, mb.order_id, ms.crop, mb.quantity, mb.loaded_qty
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (booking_id,))
    close_info = cur.fetchone()
    if close_info:
        miller_id, order_id, crop, total_qty, loaded_qty = close_info
        miller_phone = get_miller_phone(miller_id)
        if miller_phone:
            remaining = total_qty - (loaded_qty or 0)
            message = f"⚠️ Order {order_id} partially closed. {crop} - Remaining: {remaining} qty. Reason: {reason}"
            send_sms(miller_phone, message)

    con.commit()
    con.close()

    # Redirect back to referring page or default to /market
    return redirect(request.referrer or "/market")

@app.route("/miller/approve_booking/<int:id>")
def miller_approve_booking(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # 🔒 Reserve stock instead of deducting
    cur.execute("""
        UPDATE miller_stock
        SET reserved_qty = reserved_qty + (
            SELECT quantity FROM miller_bookings WHERE id=?
        )
        WHERE id = (
            SELECT stock_id FROM miller_bookings WHERE id=?
        )
    """, (id, id))

    # Approve booking
    cur.execute("""
        UPDATE miller_bookings
        SET status='approved',
            decision_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (id,))
    
    # 📱 Send SMS to buyer about approval
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, ms.crop, mb.quantity
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (id,))
    booking_info = cur.fetchone()
    if booking_info:
        buyer_id, order_id, crop, qty = booking_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"✅ Order {order_id} approved! {crop} - Qty: {qty}. Please proceed with loading."
            send_sms(buyer_phone, message)

    con.commit()
    con.close()
    return redirect("/miller")



    return redirect("/admin")

@app.route("/miller/decline_booking/<int:id>", methods=["POST"])
def miller_decline_booking(id):
    if session.get("role") != "miller":
        return redirect("/")

    reason = request.form.get("reason", "Not specified")

    con = get_db()
    cur = con.cursor()

    # return stock to inventory
    cur.execute("""
    SELECT stock_id, quantity FROM miller_bookings WHERE id=?
    """, (id,))
    row = cur.fetchone()

    if row:
        stock_id, qty = row
        cur.execute("UPDATE miller_stock SET quantity=quantity+? WHERE id=?", (qty, stock_id))

    cur.execute("""
    UPDATE miller_bookings
    SET status='declined', reason=?, decision_at=CURRENT_TIMESTAMP
    WHERE id=?
    """, (reason, id))
    
    # 📱 Send SMS to buyer about decline
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, ms.crop
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (id,))
    booking_info = cur.fetchone()
    if booking_info:
        buyer_id, order_id, crop = booking_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"❌ Order {order_id} declined. {crop} - Reason: {reason}"
            send_sms(buyer_phone, message)

    con.commit()
    con.close()
    return redirect("/miller")

# ---------------- UPDATE MILLER STOCK ----------------
@app.route("/update_miller_stock/<int:id>", methods=["POST"])
def update_miller_stock(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT price,quantity FROM miller_stock WHERE id=?", (id,))
    old_price, old_qty = cur.fetchone()

    cur.execute("""
    UPDATE miller_stock
    SET price=?, quantity=?, condition=?, bag_type=?, deduction=?, status='open'
    WHERE id=? AND miller_id=?
    """, (
        request.form["price"],
        request.form["quantity"],
        request.form["condition"],
        request.form["bag_type"],
        request.form["deduction"],
        id,
        get_effective_user_id()


    ))

    cur.execute("""
    INSERT INTO miller_stock_history
    (stock_id,miller_id,old_price,new_price,old_quantity,new_quantity)
    VALUES (?,?,?,?,?,?)
    """, (
        id,
        get_effective_user_id(),
        old_price,
        request.form["price"],
        old_qty,
        request.form["quantity"]
    ))

    con.commit()
    
    # 📱 Send SMS to all buyers about stock update
    cur.execute("SELECT crop FROM miller_stock WHERE id=?", (id,))
    crop_result = cur.fetchone()
    if crop_result:
        crop = crop_result[0]
        new_price = request.form["price"]
        new_qty = request.form["quantity"]
        buyer_phones = get_all_buyer_phones()
        message = f"📢 Stock updated! {crop} - New Qty: {new_qty}, New Price: ₹{new_price}/unit. Check the market for details."
        for phone in buyer_phones:
            send_sms(phone, message)
    
    con.close()
    return redirect("/miller")

# ---------------- BUYER ----------------
@app.route("/market")
def market():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    SELECT 
        miller_stock.id,           -- 0
        miller_stock.miller_id,    -- 1
        miller_stock.crop,         -- 2
        miller_stock.quantity,     -- 3
        miller_stock.price,        -- 4
        miller_stock.condition,    -- 5
        miller_stock.bag_type,     -- 6
        miller_stock.deduction,    -- 7
        miller_stock.created_at,   -- 8
        miller_stock.status,       -- 9
        users.name                 -- 10 (miller name)
    FROM miller_stock
    JOIN users ON miller_stock.miller_id = users.id
    WHERE miller_stock.quantity > 0
    AND miller_stock.status = 'open'
    ORDER BY miller_stock.created_at DESC
    """)
    miller_stocks = cur.fetchall()

    cur.execute("""
SELECT
    mb.id,                 -- 0
    ms.crop,               -- 1
    mb.quantity,           -- 2
    mb.loaded_qty,         -- 3
    (mb.quantity - IFNULL(mb.loaded_qty, 0)), -- 4 remaining
    mb.truck_status,       -- 5
    mb.loaded_at,          -- 6
    mb.bill_document,      -- 7
    mb.loading_status,     -- 8
    mb.order_id,           -- 9
    mb.status,             -- 10
    mb.qc_weight,          -- 11
    mb.qc_moisture,        -- 12
    mb.qc_remarks,         -- 13
    mb.qc_status,          -- 14
    mb.qc_at,              -- 15
    mb.decision_at,        -- 16
    IFNULL(p.status,'pending') AS payment_status,  -- 17
    p.invoice_file              AS final_invoice,   -- 18
    p.paid_at                   AS payment_at      -- 19
FROM miller_bookings mb
JOIN miller_stock ms ON mb.stock_id = ms.id
LEFT JOIN payments p ON p.booking_id = mb.id
WHERE mb.buyer_id=?
ORDER BY mb.created_at DESC
""", (session["user_id"],))

    my_bookings = cur.fetchall()

    active_bookings = [
        b for b in my_bookings
        if b[8] in ('pending', 'partial')
    ]

    partial_closed_bookings = [
        b for b in my_bookings
        if b[8] == 'partial_closed'
    ]

    loaded_bookings = [
        b for b in my_bookings
        if b[8] == 'loaded'
    ]

    cancelled_bookings = [
        b for b in my_bookings
        if b[10] == 'cancelled'
    ]

    # Fetch per-truck loading invoices WITH QC DATA AND FINAL INVOICE
    all_booking_ids = [b[0] for b in my_bookings]
    invoices_map = {}
    if all_booking_ids:
        placeholders = ",".join(["?"] * len(all_booking_ids))
        cur.execute(f"""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at
        FROM loading_invoices
        WHERE booking_id IN ({placeholders})
        ORDER BY created_at ASC
        """, all_booking_ids)
        rows = cur.fetchall()
        for r in rows:
            invoices_map.setdefault(r[1], []).append({
                "id": r[0],  # invoice id
                "qty": r[2],
                "file": r[3],
                "truck_number": r[4],
                "date": r[5],
                "qc_weight": r[6],
                "qc_moisture": r[7],
                "qc_remarks": r[8],
                "qc_status": r[9] or "pending",
                "qc_at": r[10],
                "final_invoice_file": r[11],
                "payment_status": r[12] or "pending",
                "payment_at": r[13]
            })

    # Calculate totals
    total_booked = sum(b[2] or 0 for b in active_bookings)
    total_loaded = sum(b[3] or 0 for b in active_bookings)
    total_remaining = sum(b[4] or 0 for b in active_bookings)

    # Fetch all crops for market
    cur.execute("""
    SELECT crops.*, users.name
    FROM crops
    JOIN users ON crops.farmer_id = users.id
    WHERE crops.sold = 0
    ORDER BY crops.id DESC
    """)
    crops = cur.fetchall()

    con.close()
    
    return render_template(
        "market.html",
        crops=crops,
        miller_stocks=miller_stocks,
        my_bookings=active_bookings,
        partial_closed_bookings=partial_closed_bookings,
        loaded_bookings=loaded_bookings,
        invoices_map=invoices_map,
        total_booked=total_booked,
        total_loaded=total_loaded,
        total_remaining=total_remaining,
        cancelled_bookings=cancelled_bookings
    )
# ================= BUYER ORDER PAGES =================

def get_buyer_orders(filter_type):
    con = get_db()
    cur = con.cursor()

    where = ""
    if filter_type == "active":
        where = "AND mb.loading_status IN ('pending','partial')"
    elif filter_type == "partial":
        where = "AND mb.loading_status='partial_closed'"
    elif filter_type == "loaded":
        where = "AND mb.loading_status='loaded'"

    cur.execute(f"""
        SELECT
            mb.id,
            mb.order_id,
            ms.crop,
            mb.quantity,
            IFNULL(mb.loaded_qty,0),
            mb.loaded_at,
            mb.loading_status,

            mb.qc_weight,
            mb.qc_moisture,
            mb.qc_remarks,
            mb.qc_status,
            mb.qc_at,

            IFNULL(p.status,'na') AS payment_status,
            p.invoice_file,
            p.paid_at,

            u.name AS miller_name,
            mb.close_reason

        FROM miller_bookings mb
JOIN miller_stock ms ON mb.stock_id = ms.id
JOIN users u ON ms.miller_id = u.id
LEFT JOIN payments p ON p.booking_id = mb.id

        WHERE mb.buyer_id=?
        {where}
        ORDER BY mb.created_at DESC
    """, (session["user_id"],))

    rows = cur.fetchall()

    cur.execute("""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at
        FROM loading_invoices
        ORDER BY created_at ASC
    """)
    invs = cur.fetchall()

    invoices_map = {}
    for i in invs:
        invoices_map.setdefault(i[1], []).append({
            "id": i[0],  # invoice id
            "qty": i[2],
            "file": i[3],
            "truck_number": i[4],
            "date": i[5],
            "qc_weight": i[6],
            "qc_moisture": i[7],
            "qc_remarks": i[8],
            "qc_status": i[9] or "pending",
            "qc_at": i[10],
            "final_invoice_file": i[11],
            "payment_status": i[12] or "pending",
            "payment_at": i[13]
        })

    orders = []
    for r in rows:
        orders.append({
            "id": r[0],
            "order_id": r[1],
            "crop": r[2],
            "booked": r[3],
            "loaded": r[4],
            "loaded_at": r[5],
            "loading_status": r[6],

            "qc_weight": r[7],
            "qc_moisture": r[8],
            "qc_remarks": r[9],
            "qc_status": r[10],
            "qc_at": r[11],

            "payment_status": r[12],
            "final_invoice": r[13],
            "payment_at": r[14],

            "miller_name": r[15],
            "close_reason": r[16],

            "invoices": invoices_map.get(r[0], [])
        })

    con.close()
    return orders
def get_miller_orders_by_type(filter_type):
    con = get_db()
    cur = con.cursor()

    where = ""
    if filter_type == "approved":
        where = "AND mb.status='approved' AND mb.loading_status IN ('pending','partial')"
    elif filter_type == "qc":
        where = "AND mb.loading_status='loaded' AND mb.qc_status='pending'"
    elif filter_type == "final":
        where = "AND mb.loading_status='loaded' AND IFNULL(p.invoice_file,'') != '' AND IFNULL(p.status,'pending')='pending'"
    elif filter_type == "rejected":
        where = "AND mb.status IN ('declined','cancelled')"

    cur.execute(f"""
        SELECT
            mb.id,                -- 0
            mb.order_id,          -- 1
            u.name,               -- 2 buyer
            ms.crop,              -- 3
            mb.quantity,          -- 4 booked
            IFNULL(mb.loaded_qty,0), -- 5 loaded
            (mb.quantity - IFNULL(mb.loaded_qty,0)), -- 6 remaining
            mb.loading_status,    -- 7
            mb.qc_status,         -- 8
            mb.qc_weight,         -- 9
            mb.qc_moisture,       -- 10
            mb.qc_at,             -- 11
            IFNULL(p.status,'pending'), -- 12 payment_status
            p.invoice_file,       -- 13 final_invoice
            mb.close_reason       -- 14
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON mb.buyer_id = u.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        WHERE ms.miller_id=?
        {where}
        ORDER BY mb.created_at DESC
    """, (get_effective_user_id(),))

    rows = cur.fetchall()

    # 🔹 Fetch per-truck invoices WITH FINAL INVOICE
    cur.execute("""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at
        FROM loading_invoices
        ORDER BY created_at ASC
    """)
    invs = cur.fetchall()

    invoices_map = {}
    for i in invs:
        invoices_map.setdefault(i[1], []).append({
            "id": i[0],
            "qty": i[2],
            "file": i[3],
            "truck_number": i[4],
            "date": i[5],
            "qc_weight": i[6],
            "qc_moisture": i[7],
            "qc_remarks": i[8],
            "qc_status": i[9] or "pending",
            "qc_at": i[10],
            "final_invoice_file": i[11],
            "payment_status": i[12] or "pending",
            "payment_at": i[13]
        })

    orders = []
    for r in rows:
        orders.append({
            "id": r[0],
            "order_id": r[1],
            "buyer": r[2],
            "crop": r[3],
            "booked": r[4],
            "loaded": r[5],
            "remaining": r[6],
            "loading_status": r[7],
            "qc_status": r[8],
            "qc_weight": r[9],
            "qc_moisture": r[10],
            "qc_at": r[11],
            "payment_status": r[12],
            "final_invoice": r[13],
            "close_reason": r[14],
            "invoices": invoices_map.get(r[0], [])
        })

    con.close()
    return orders


@app.route("/buyer/active")
def buyer_active():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("active")
    return render_template("buyer_active.html", page_title="Active Orders", orders=orders)


@app.route("/buyer/partial")
def buyer_partial():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("partial")
    return render_template("buyer_partial.html", page_title="Partially Closed Orders", orders=orders)


@app.route("/buyer/loaded")
def buyer_loaded():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("loaded")
    return render_template("buyer_loaded.html", page_title="Loaded Orders", orders=orders)

   
@app.route("/buyer/payments")
def buyer_payments():
    if session.get("role") != "buyer":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
    SELECT
        mb.order_id,
        ms.crop,
        mb.loaded_qty,
        ms.price,
        (mb.loaded_qty * ms.price) AS total_amount,
        p.invoice_file,
        p.paid_at,
        u.name AS miller_name
    FROM payments p
    JOIN miller_bookings mb ON p.booking_id = mb.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users u ON ms.miller_id = u.id
    WHERE p.buyer_id=? AND p.status='paid'
    ORDER BY p.paid_at DESC
    """, (session["user_id"],))

    payments = cur.fetchall()
    con.close()

    return render_template("buyer_payments.html", payments=payments)

@app.route("/book_miller_stock/<int:stock_id>", methods=["POST"])
def book_miller_stock(stock_id):
    if session.get("role") != "buyer":
        return redirect("/market")

    qty = int(request.form["quantity"])
    con = get_db()
    cur = con.cursor()

    # Check if stock exists and has enough quantity
    cur.execute("""
        SELECT quantity, status
        FROM miller_stock
        WHERE id=?
    """, (stock_id,))
    row = cur.fetchone()

    if row and row[0] >= qty and row[1] == 'open':
        # Generate order ID
        order_id = generate_next_order_id()
        
        # Create booking
        cur.execute("""
            INSERT INTO miller_bookings
            (stock_id, buyer_id, quantity, status, order_id)
            VALUES (?, ?, ?, 'pending', ?)
        """, (stock_id, session["user_id"], qty, order_id))
        
        booking_id = cur.lastrowid
        
        # DEDUCT quantity immediately from stock
        cur.execute("""
            UPDATE miller_stock
            SET quantity = quantity - ?
            WHERE id=?
        """, (qty, stock_id))
        
        # Close stock if quantity reaches 0
        cur.execute("""
            UPDATE miller_stock
            SET status='closed'
            WHERE id=? AND quantity <= 0
        """, (stock_id,))
        
        # 📱 Send SMS to miller about new booking
        cur.execute("""
            SELECT ms.miller_id, ms.crop
            FROM miller_stock ms
            WHERE ms.id = ?
        """, (stock_id,))
        stock_info = cur.fetchone()
        if stock_info:
            miller_id, crop = stock_info
            miller_phone = get_miller_phone(miller_id)
            if miller_phone:
                message = f"🆕 New booking received! Order {order_id}: {crop} - Qty: {qty}. Please review and approve."
                send_sms(miller_phone, message)
        
        con.commit()

    con.close()
    return redirect("/market")


@app.route("/cancel_booking/<int:id>")
def cancel_booking(id):
    if session.get("role") != "buyer":
        return redirect("/market")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
    SELECT stock_id, quantity, loaded_qty
    FROM miller_bookings
    WHERE id=? AND buyer_id=? AND status IN ('pending','approved') AND loaded_qty=0
    """, (id, get_effective_user_id()))
    row = cur.fetchone()

    if row:
        stock_id, qty, loaded = row
        loaded = loaded or 0
        remaining = max(0, qty - loaded)

        if remaining > 0:
            cur.execute(
                "UPDATE miller_stock SET quantity=quantity+? WHERE id=?",
                (remaining, stock_id)
            )

        # Keep original booked qty; mark cancelled while preserving loaded part
        cur.execute("""
            UPDATE miller_bookings
            SET status='cancelled',
                loading_status='cancelled',
                decision_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (id,))
        
        # 📱 Send SMS to miller about cancellation
        cur.execute("""
            SELECT ms.miller_id, mb.order_id, ms.crop, mb.quantity
            FROM miller_bookings mb
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE mb.id=?
        """, (id,))
        cancel_info = cur.fetchone()
        if cancel_info:
            miller_id, order_id, crop, qty = cancel_info
            miller_phone = get_miller_phone(miller_id)
            if miller_phone:
                message = f"❌ Order {order_id} cancelled by buyer. {crop} - Qty: {qty}. Stock returned to inventory."
                send_sms(miller_phone, message)

        con.commit()

    con.close()
    return redirect("/market")

@app.route("/buyer/update_loading/<int:id>", methods=["POST"])
def buyer_update_loading(id):
    if session.get("role") != "buyer":
        return redirect("/market")

    load_qty = int(request.form.get("load_qty", 0))
    truck_number = (request.form.get("truck_number") or "").strip()
    invoice = request.files.get("invoice")

    if load_qty <= 0 or not invoice:
        return redirect("/market")

    # Save invoice
    filename = secure_filename(invoice.filename)
    invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # Fetch booking details
    cur.execute("""
        SELECT quantity, loaded_qty, stock_id
        FROM miller_bookings
        WHERE id=? AND buyer_id=? AND status='approved'
    """, (id, session["user_id"]))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/market")

    total_qty, loaded_qty, stock_id = row
    loaded_qty = loaded_qty or 0
    remaining = total_qty - loaded_qty

    if load_qty > remaining:
        load_qty = remaining

    new_loaded = loaded_qty + load_qty

    loading_status = "loaded" if new_loaded == total_qty else "partial"
    truck_status = loading_status

    # 🔹 Update booking
    cur.execute("""
        UPDATE miller_bookings
        SET loaded_qty=?,
            loading_status=?,
            truck_status=?,
            loaded_at=CURRENT_TIMESTAMP
        WHERE id=? AND buyer_id=?
    """, (new_loaded, loading_status, truck_status, id, session["user_id"]))

    # 🔹 Save per-truck invoice
    truck_number_val = truck_number if truck_number else None
    cur.execute("""
        INSERT INTO loading_invoices
        (booking_id, loaded_qty, invoice_file, truck_number)
        VALUES (?, ?, ?, ?)
    """, (id, load_qty, filename, truck_number_val))

    # 🔹 MOVE RESERVED → USED STOCK
    cur.execute("""
        UPDATE miller_stock
        SET
            quantity = quantity - ?,
            reserved_qty = reserved_qty - ?
        WHERE id=?
    """, (load_qty, load_qty, stock_id))

    # 🔹 Auto close stock if empty
    cur.execute("""
        UPDATE miller_stock
        SET status='closed'
        WHERE quantity <= 0
    """)
    
    # 📱 Send SMS to miller about loading update
    cur.execute("""
        SELECT ms.miller_id, mb.order_id, ms.crop, mb.loaded_qty, mb.quantity
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
    """, (id,))
    loading_info = cur.fetchone()
    if loading_info:
        miller_id, order_id, crop, loaded_qty, total_qty = loading_info
        miller_phone = get_miller_phone(miller_id)
        if miller_phone:
            truck_part = f" Truck: {truck_number}" if truck_number else ""
            message = f"🚚 Loading update for Order {order_id}: {crop} - Loaded: {loaded_qty}/{total_qty}.{truck_part} Invoice uploaded."
            send_sms(miller_phone, message)

    con.commit()
    con.close()

    return redirect("/market")

@app.route("/buyer/edit_loading_invoice/<int:invoice_id>", methods=["POST"])
def buyer_edit_loading_invoice(invoice_id):
    """Edit/replace a loading invoice (per-truck invoice)."""
    if session.get("role") != "buyer":
        return redirect("/market")

    truck_number = (request.form.get("truck_number") or "").strip()

    invoice = request.files.get("invoice")
    if not invoice or invoice.filename == "":
        return redirect("/market")

    filename = secure_filename(invoice.filename)
    invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Verify this invoice belongs to the buyer
    cur.execute("""
        SELECT li.id, mb.buyer_id
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        WHERE li.id=? AND mb.buyer_id=?
    """, (invoice_id, session["user_id"]))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/market")

    # ✅ Update the invoice file (+ truck number)
    truck_number_val = truck_number if truck_number else None
    cur.execute("""
        UPDATE loading_invoices
        SET invoice_file=?,
            truck_number=?
        WHERE id=?
    """, (filename, truck_number_val, invoice_id))

    con.commit()
    con.close()

    return redirect("/market")


@app.route("/invoice/<int:booking_id>")
def invoice(booking_id):
    if session.get("role") != "buyer":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
    SELECT
        mb.id,
        buyer.name,
        miller.name,
        ms.crop,
        mb.loaded_qty,
        ms.price,
        p.paid_at
    FROM miller_bookings mb
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users buyer ON mb.buyer_id = buyer.id
    JOIN users miller ON ms.miller_id = miller.id
    JOIN payments p ON p.booking_id = mb.id
    WHERE mb.id=? AND mb.buyer_id=? AND p.status='paid'
""", (booking_id, get_effective_user_id()))


    invoice = cur.fetchone()
    con.close()

    if not invoice:
        return "❌ Invoice available only after full loading.", 403

    return render_template("invoice.html", invoice=invoice)


@app.route("/miller/update_qc/<int:invoice_id>", methods=["POST"])
def miller_update_qc(invoice_id):
    """Miller records quality check for a specific truck/invoice."""
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()

    con = get_db()
    cur = con.cursor()

    # Ensure this invoice belongs to a booking of the current miller
    cur.execute("""
        SELECT li.id
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE li.id=? AND ms.miller_id=?
    """, (invoice_id, miller_id))
    if not cur.fetchone():
        con.close()
        return redirect(request.referrer or "/miller")

    qc_weight = request.form.get("qc_weight") or None
    qc_moisture = request.form.get("qc_moisture") or None
    qc_remarks = request.form.get("qc_remarks") or ""

    try:
        qc_weight_val = int(qc_weight) if qc_weight not in (None, "",) else None
    except ValueError:
        qc_weight_val = None

    try:
        qc_moisture_val = float(qc_moisture) if qc_moisture not in (None, "",) else None
    except ValueError:
        qc_moisture_val = None

    # Update QC for this specific invoice (truck)
    cur.execute("""
        UPDATE loading_invoices
        SET qc_weight=?,
            qc_moisture=?,
            qc_remarks=?,
            qc_status='verified',
            qc_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (qc_weight_val, qc_moisture_val, qc_remarks, invoice_id))
    
    # 📱 Send SMS to buyer about QC update
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, li.loaded_qty, li.truck_number
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        WHERE li.id=?
    """, (invoice_id,))
    qc_info = cur.fetchone()
    if qc_info:
        buyer_id, order_id, loaded_qty, truck_number = qc_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            qc_details = f"Weight: {qc_weight_val or 'N/A'}, Moisture: {qc_moisture_val or 'N/A'}"
            truck_part = f" Truck: {truck_number}." if truck_number else ""
            message = f"✅ QC verified for Order {order_id},{truck_part} Truck Qty: {loaded_qty}. {qc_details}"
            send_sms(buyer_phone, message)

    con.commit()
    con.close()

    return redirect(request.referrer or "/miller")


# ---------------- ADMIN ----------------
@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    farmer_count = sum(1 for u in users if u[4]=="farmer")
    buyer_count  = sum(1 for u in users if u[4]=="buyer")
    miller_count = sum(1 for u in users if u[4]=="miller")

    cur.execute("""
    SELECT miller_stock.*, users.name
    FROM miller_stock
    JOIN users ON miller_stock.miller_id = users.id
    """)
    stocks = cur.fetchall()

    cur.execute("""
    SELECT h.*, u.name
    FROM miller_stock_history h
    JOIN users u ON h.miller_id = u.id
    ORDER BY h.updated_at DESC
    """)
    history = cur.fetchall()

    cur.execute("""
    SELECT
        mb.id,                 -- 0 Booking ID
        buyer.name,            -- 1 Buyer
        miller.name,           -- 2 Miller
        ms.crop,               -- 3 Crop
        mb.quantity,           -- 4 Qty
        ms.price,              -- 5 Price
        (mb.quantity * ms.price), -- 6 Total
        mb.status,             -- 7 Booking status
        mb.truck_status,       -- 8 🚚 Loading status
        mb.loaded_at,          -- 9 Loaded date
        mb.truck_remark,       -- 10 Remark
        mb.order_id            -- 11 Order ID
    FROM miller_bookings mb
    JOIN users buyer ON mb.buyer_id = buyer.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users miller ON ms.miller_id = miller.id
    ORDER BY mb.created_at DESC
""")
    bookings = cur.fetchall()

    
    # 🔹 BUYER PROFILES
    cur.execute("""
        SELECT
        bp.id,
        u.name,
        bp.shop_name,
        bp.phone,
        bp.address,
        bp.document,
        bp.created_at
    FROM buyer_profiles bp
    JOIN users u ON bp.buyer_id = u.id
    ORDER BY bp.created_at DESC
    """)
    buyer_profiles = cur.fetchall()

    # 🔹 MILLER PROFILES
    cur.execute("""
        SELECT
            mp.id,
            u.name,
            mp.mill_name,
            mp.owner_phone,
            mp.address,
            mp.gst_doc,
            mp.mandi_doc,
            mp.other_doc,
            mp.created_at
        FROM miller_profiles mp
        JOIN users u ON mp.miller_id = u.id
        ORDER BY mp.created_at DESC
    """)
    miller_profiles = cur.fetchall()
    cur.execute("""
    SELECT
        u.id,                     -- 0
        u.name,                   -- 1
        u.email,                  -- 2
        u.role,                   -- 3
        u.status,                 -- 4
        u.is_staff,               -- 5
        pm.name                   -- 6 Parent miller name
    FROM users u
    LEFT JOIN users pm
        ON u.parent_miller_id = pm.id
    WHERE u.role != 'admin'
    ORDER BY u.id DESC
""")

    all_users = cur.fetchall()

    # Get all main millers (not staff) for comparison
    cur.execute("""
        SELECT u.id, u.name
        FROM users u
        WHERE u.role = 'miller' AND (u.is_staff = 0 OR u.is_staff IS NULL)
        ORDER BY u.name
    """)
    millers = cur.fetchall()

    # Calculate statistics for charts
    # Booking status distribution
    pending_bookings = sum(1 for b in bookings if b[7] == 'pending')
    approved_bookings = sum(1 for b in bookings if b[7] == 'approved')
    declined_bookings = sum(1 for b in bookings if b[7] == 'declined')
    
    # Total revenue (from approved bookings)
    total_revenue = sum(b[6] for b in bookings if b[7] == 'approved')
    
    # Stock statistics by crop
    crop_stats = {}
    for stock in stocks:
        crop = stock[2]
        if crop not in crop_stats:
            crop_stats[crop] = {'quantity': 0, 'count': 0}
        crop_stats[crop]['quantity'] += stock[3] or 0
        crop_stats[crop]['count'] += 1
    
    # Recent bookings (last 7 days)
    cur.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count
        FROM miller_bookings
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    """)
    recent_data = cur.fetchall()
    recent_bookings_dates = [row[0] or '' for row in recent_data]
    recent_bookings_counts = [row[1] or 0 for row in recent_data]
    
    # User status distribution
    approved_users = sum(1 for u in users if u[4] == 'approved')
    pending_users = sum(1 for u in users if u[4] == 'pending')
    blocked_users = sum(1 for u in users if u[4] == 'blocked')
    
    # Total bookings count
    total_bookings = len(bookings)
    
    # Total stock quantity
    total_stock_qty = sum(s[3] or 0 for s in stocks)

    con.close()

    return render_template(
        "admin.html",
       users=users,
    stocks=stocks,
    history=history,
    bills=[],
    bookings=bookings,
    miller_profiles=miller_profiles,
    farmer_count=farmer_count,
    buyer_profiles=buyer_profiles,
    buyer_count=buyer_count,
    miller_count=miller_count,
    all_users=all_users,
    millers=millers,
    # Chart data
    pending_bookings=pending_bookings,
    approved_bookings=approved_bookings,
    declined_bookings=declined_bookings,
    total_revenue=total_revenue,
    crop_stats=crop_stats,
    recent_bookings_dates=recent_bookings_dates,
    recent_bookings_counts=recent_bookings_counts,
    approved_users=approved_users,
    pending_users=pending_users,
    blocked_users=blocked_users,
    total_bookings=total_bookings,
    total_stock_qty=total_stock_qty,
    )
    
@app.route("/admin/api/miller_stock/<int:miller_id>")
def get_miller_stock_api(miller_id):
    """API endpoint to get miller stock data for comparison"""
    if session.get("role") != "admin":
        return {"error": "Unauthorized"}, 403
    
    con = get_db()
    cur = con.cursor()
    
    # Get miller info
    cur.execute("SELECT id, name FROM users WHERE id=? AND role='miller'", (miller_id,))
    miller = cur.fetchone()
    
    if not miller:
        con.close()
        return {"error": "Miller not found"}, 404
    
    # Get miller stock
    cur.execute("""
        SELECT crop, quantity, price, condition, bag_type, deduction, created_at
        FROM miller_stock
        WHERE miller_id=?
        ORDER BY created_at DESC
    """, (miller_id,))
    stocks = cur.fetchall()
    
    # Format stock data
    stock_data = []
    for stock in stocks:
        stock_data.append({
            "crop": stock[0],
            "quantity": stock[1],
            "price": stock[2],
            "condition": stock[3],
            "bag_type": stock[4],
            "deduction": stock[5],
            "created_at": stock[6]
        })
    
    con.close()
    
    return {
        "miller_id": miller[0],
        "miller_name": miller[1],
        "stocks": stock_data
    }

@app.route("/admin/compare")
def admin_compare():
    """Miller Rate Comparison Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    # Get all main millers (not staff) for comparison
    cur.execute("""
        SELECT u.id, u.name
        FROM users u
        WHERE u.role = 'miller' AND (u.is_staff = 0 OR u.is_staff IS NULL)
        ORDER BY u.name
    """)
    millers = cur.fetchall()
    
    con.close()
    
    return render_template("admin_compare.html", millers=millers)

@app.route("/admin/users")
def admin_users():
    """User Access Control Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
    SELECT
        u.id,                     -- 0
        u.name,                   -- 1
        u.email,                  -- 2
        u.role,                   -- 3
        u.status,                 -- 4
        u.is_staff,               -- 5
        pm.name                   -- 6 Parent miller name
    FROM users u
    LEFT JOIN users pm
        ON u.parent_miller_id = pm.id
    WHERE u.role != 'admin'
    ORDER BY u.id DESC
""")
    all_users = cur.fetchall()
    con.close()
    
    return render_template("admin_users.html", all_users=all_users)

@app.route("/admin/stock")
def admin_stock():
    """Miller Stock (Latest) Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
    SELECT miller_stock.*, users.name
    FROM miller_stock
    JOIN users ON miller_stock.miller_id = users.id
    ORDER BY miller_stock.created_at DESC
    """)
    stocks = cur.fetchall()
    con.close()
    
    return render_template("admin_stock.html", stocks=stocks)

@app.route("/admin/stock-history")
def admin_stock_history():
    """Miller Stock Update History Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
    SELECT h.*, u.name
    FROM miller_stock_history h
    JOIN users u ON h.miller_id = u.id
    ORDER BY h.updated_at DESC
    """)
    history = cur.fetchall()
    con.close()
    
    return render_template("admin_stock_history.html", history=history)

@app.route("/admin/bookings")
def admin_bookings():
    """Miller Bookings (Admin Control) Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
    SELECT
        mb.id,                 -- 0 Booking ID
        buyer.name,            -- 1 Buyer
        miller.name,           -- 2 Miller
        ms.crop,               -- 3 Crop
        mb.quantity,           -- 4 Qty
        ms.price,              -- 5 Price
        (mb.quantity * ms.price), -- 6 Total
        mb.status,             -- 7 Booking status
        mb.truck_status,       -- 8 Truck status
        mb.loaded_at,          -- 9 Loaded date
        mb.truck_remark,       -- 10 Remark
        mb.order_id,           -- 11 Order ID
        mb.loading_status,     -- 12 Loading status
        mb.bill_document,      -- 13 Bill document
        mb.loaded_qty          -- 14 Loaded quantity
    FROM miller_bookings mb
    JOIN users buyer ON mb.buyer_id = buyer.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users miller ON ms.miller_id = miller.id
    ORDER BY mb.created_at DESC
""")
    bookings = cur.fetchall()
    con.close()
    
    return render_template("admin_bookings.html", bookings=bookings)

@app.route("/admin/miller-profiles")
def admin_miller_profiles():
    """Miller Profiles Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        SELECT
            mp.id,
            u.name,
            mp.mill_name,
            mp.owner_phone,
            mp.address,
            mp.gst_doc,
            mp.mandi_doc,
            mp.other_doc,
            mp.created_at
        FROM miller_profiles mp
        JOIN users u ON mp.miller_id = u.id
        ORDER BY mp.created_at DESC
    """)
    miller_profiles = cur.fetchall()
    con.close()
    
    return render_template("admin_miller_profiles.html", miller_profiles=miller_profiles)

@app.route("/admin/buyer-profiles")
def admin_buyer_profiles():
    """Buyer/Trader Profiles Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        SELECT
        bp.id,
        u.name,
        bp.shop_name,
        bp.phone,
        bp.address,
        bp.document,
        bp.created_at
    FROM buyer_profiles bp
    JOIN users u ON bp.buyer_id = u.id
    ORDER BY bp.created_at DESC
    """)
    buyer_profiles = cur.fetchall()
    con.close()
    
    return render_template("admin_buyer_profiles.html", buyer_profiles=buyer_profiles)

@app.route("/admin/update_deduction/<int:stock_id>", methods=["POST"])
def admin_update_deduction(stock_id):
    if session.get("role") != "admin":
        return redirect("/")

    deduction = request.form.get("deduction", 0)

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE miller_stock
        SET deduction=?
        WHERE id=?
    """, (deduction, stock_id))

    con.commit()
    con.close()

    return redirect("/admin/stock")
    
@app.route("/admin/approve_user/<int:id>")
def approve_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='approved' WHERE id=?", (id,))
    con.commit()
    con.close()
    return redirect("/admin/users")
@app.route("/admin/block_user/<int:id>")
def block_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='blocked' WHERE id=?", (id,))
    con.commit()
    con.close()
    return redirect("/admin/users")
@app.route("/admin/reject_user/<int:id>")
def reject_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='rejected' WHERE id=?", (id,))
    con.commit()
    con.close()
    return redirect("/admin/users")
    
@app.route("/admin/miller/<int:miller_id>")
def admin_view_miller(miller_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT u.name, u.email, p.mill_name, p.owner_phone, p.address, p.gst_doc
        FROM users u
        LEFT JOIN miller_profiles p ON u.id = p.miller_id
        WHERE u.id=?
    """, (miller_id,))
    miller = cur.fetchone()

    con.close()
    return render_template("admin_miller_profile.html", miller=miller)
    
@app.route("/admin/approve_booking/<int:id>")
def admin_approve_booking(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE miller_bookings
        SET status='approved',
            decision_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (id,))

    con.commit()
    con.close()
    return redirect("/admin/bookings")
@app.route("/admin/decline_booking/<int:id>")
def admin_decline_booking(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE miller_bookings
        SET status='declined',
            decision_at=CURRENT_TIMESTAMP,
            reason='Declined by admin'
        WHERE id=?
    """, (id,))

    con.commit()
    con.close()
    return redirect("/admin/bookings")

# ---------------- SMS TEST ROUTE ----------------
@app.route("/test_sms", methods=["GET", "POST"])
def test_sms():
    """Test SMS functionality - for debugging only"""
    result = {"success": False, "message": "", "details": {}}
    
    if request.method == "POST":
        test_phone = request.form.get("phone", "")
        test_message = request.form.get("message", "Test SMS from Sarna Broker")
        
        result["details"]["phone"] = test_phone
        result["details"]["message"] = test_message
        result["details"]["twilio_account_sid"] = "Set" if TWILIO_ACCOUNT_SID else "Missing"
        result["details"]["twilio_auth_token"] = "Set" if TWILIO_AUTH_TOKEN else "Missing"
        result["details"]["twilio_phone_number"] = TWILIO_PHONE_NUMBER if TWILIO_PHONE_NUMBER else "Missing"
        
        if test_phone:
            success = send_sms(test_phone, test_message)
            result["success"] = success
            result["message"] = "SMS sent successfully!" if success else "Failed to send SMS. Check console for details."
        else:
            result["message"] = "Please provide a phone number"
    
    return f"""
    <html>
    <head><title>SMS Test</title></head>
    <body style="font-family: Arial; padding: 20px;">
        <h2>SMS Test Page</h2>
        <form method="POST">
            <p>
                <label>Phone Number (with country code):</label><br>
                <input type="text" name="phone" placeholder="+919876543210" style="width: 300px; padding: 5px;" required>
            </p>
            <p>
                <label>Test Message:</label><br>
                <textarea name="message" style="width: 300px; padding: 5px; height: 60px;">Test SMS from Sarna Broker</textarea>
            </p>
            <p>
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer;">Send Test SMS</button>
            </p>
        </form>
        {f'''
        <div style="margin-top: 20px; padding: 15px; background: {'#d4edda' if result['success'] else '#f8d7da'}; border: 1px solid {'#c3e6cb' if result['success'] else '#f5c6cb'};">
            <h3>{'✅ Success' if result['success'] else '❌ Failed'}</h3>
            <p><strong>Message:</strong> {result['message']}</p>
            <p><strong>Details:</strong></p>
            <ul>
                <li>Phone: {result['details'].get('phone', 'N/A')}</li>
                <li>Account SID: {result['details'].get('twilio_account_sid', 'N/A')}</li>
                <li>Auth Token: {result['details'].get('twilio_auth_token', 'N/A')}</li>
                <li>Twilio Phone: {result['details'].get('twilio_phone_number', 'N/A')}</li>
            </ul>
        </div>
        ''' if request.method == "POST" else ""}
        <hr>
        <p><strong>Current Configuration:</strong></p>
        <ul>
            <li>Account SID: {'✅ Set' if TWILIO_ACCOUNT_SID else '❌ Missing'}</li>
            <li>Auth Token: {'✅ Set' if TWILIO_AUTH_TOKEN else '❌ Missing'}</li>
            <li>Phone Number: {TWILIO_PHONE_NUMBER if TWILIO_PHONE_NUMBER else '❌ Missing'}</li>
        </ul>
    </body>
    </html>
    """
    
# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
