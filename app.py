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
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

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

upgrade_miller_booking_qc()
upgrade_miller_booking_order_id()
upgrade_miller_payment_fields()
upgrade_payments_table()

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

    # 🔹 FETCH PER-TRUCK LOADING INVOICES
    cur = get_db().cursor()
    cur.execute("""
    SELECT booking_id, loaded_qty, invoice_file, created_at
    FROM loading_invoices
    ORDER BY created_at ASC
""")
    rows = cur.fetchall()

# Group invoices by booking_id
    invoices_map = {}
    for r in rows:
     invoices_map.setdefault(r[0], []).append({
        "qty": r[1],
        "file": r[2],
        "date": r[3]
    })

    cur.connection.close()

    return render_template(
    "miller.html",
    stocks=stocks,
    bookings=bookings,
    invoices_map=invoices_map
)
@app.route("/miller/complete_payment/<int:booking_id>", methods=["POST"])
def miller_complete_payment(booking_id):
    if session.get("role") != "miller":
        return redirect("/")

    invoice = request.files.get("final_invoice")
    if not invoice or invoice.filename == "":
        return redirect("/miller")

    filename = secure_filename(invoice.filename)
    invoice.save(os.path.join(app.config["BILL_FOLDER"], filename))

    con = get_db()
    cur = con.cursor()

    # ✅ Only allow payment if fully loaded
    cur.execute("""
        SELECT mb.loaded_qty, mb.quantity
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=? AND ms.miller_id=? AND mb.loading_status='loaded'
    """, (booking_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/miller")

    # ✅ UPSERT payment
    cur.execute("""
        INSERT INTO payments
        (booking_id, miller_id, buyer_id, amount, status, paid_at, invoice_file)
        SELECT
            mb.id,
            ms.miller_id,
            mb.buyer_id,
            (mb.loaded_qty * ms.price),
            'paid',
            CURRENT_TIMESTAMP,
            ?
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=?
        ON CONFLICT(booking_id) DO UPDATE SET
            status='paid',
            paid_at=CURRENT_TIMESTAMP,
            invoice_file=excluded.invoice_file
    """, (filename, booking_id))

    con.commit()
    con.close()

    return redirect("/miller")

  
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
        mill_name = request.form["mill_name"]
        owner_phone = request.form.get("owner_phone", "")
        accountant_phone = request.form.get("accountant_phone", "")
        staff_phone = request.form.get("staff_phone", "")
        address = request.form["address"]

        # Handle multiple document uploads
        gst_doc = request.files.get("gst_doc")
        mandi_doc = request.files.get("mandi_doc")
        other_doc = request.files.get("other_doc")
        
        # Get existing filenames if profile exists
        # Column order: id(0), miller_id(1), mill_name(2), phone(3), address(4), document(5), 
        # created_at(6), owner_phone(7), accountant_phone(8), staff_phone(9), 
        # gst_doc(10), mandi_doc(11), other_doc(12)
        gst_filename = profile[10] if profile and len(profile) > 10 and profile[10] else None
        mandi_filename = profile[11] if profile and len(profile) > 11 and profile[11] else None
        other_filename = profile[12] if profile and len(profile) > 12 and profile[12] else None
        
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
        return redirect("/market")

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
        return redirect("/market")

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

    con.commit()
    con.close()

    return redirect("/market")

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

    # Fetch per-truck loading invoices
    booking_ids = [b[0] for b in my_bookings]
    invoices_map = {}
    if booking_ids:
        placeholders = ",".join(["?"] * len(booking_ids))
        cur.execute(f"""
        SELECT booking_id, loaded_qty, invoice_file, created_at
        FROM loading_invoices
        WHERE booking_id IN ({placeholders})
        ORDER BY created_at ASC
        """, booking_ids)
        rows = cur.fetchall()
        for r in rows:
            invoices_map.setdefault(r[0], []).append({
                "qty": r[1],
                "file": r[2],
                "date": r[3]
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
        SELECT booking_id, loaded_qty, invoice_file, created_at
        FROM loading_invoices
    """)
    invs = cur.fetchall()

    invoices_map = {}
    for i in invs:
        invoices_map.setdefault(i[0], []).append({
            "qty": i[1],
            "file": i[2],
            "date": i[3]
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

        con.commit()

    con.close()
    return redirect("/market")

@app.route("/buyer/update_loading/<int:id>", methods=["POST"])
def buyer_update_loading(id):
    if session.get("role") != "buyer":
        return redirect("/market")

    load_qty = int(request.form.get("load_qty", 0))
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
    cur.execute("""
        INSERT INTO loading_invoices
        (booking_id, loaded_qty, invoice_file)
        VALUES (?, ?, ?)
    """, (id, load_qty, filename))

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


@app.route("/miller/update_qc/<int:booking_id>", methods=["POST"])
def miller_update_qc(booking_id):
    """Miller records final weight / moisture after truck reaches mill."""
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()

    con = get_db()
    cur = con.cursor()

    # Ensure this booking belongs to the current miller
    cur.execute("""
        SELECT mb.id
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=? AND ms.miller_id=?
    """, (booking_id, miller_id))
    if not cur.fetchone():
        con.close()
        return redirect("/miller")

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

    cur.execute("""
        UPDATE miller_bookings
        SET qc_weight=?,
            qc_moisture=?,
            qc_remarks=?,
            qc_status='verified',
            qc_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (qc_weight_val, qc_moisture_val, qc_remarks, booking_id))

    con.commit()
    con.close()

    return redirect("/miller")


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
            mp.phone,
            mp.address,
            mp.document,
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
        SELECT u.name, u.email, p.mill_name, p.phone, p.address, p.document
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
    
# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
