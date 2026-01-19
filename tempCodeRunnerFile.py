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
    cur.execute("SELECT * FROM users WHERE role='admin'")
    if not cur.fetchone():
        cur.execute("""
        INSERT INTO users (name,email,password,role,status)
        VALUES (?,?,?,?,?)
        """,(
              request.form["name"],
    request.form["email"],
    request.form["password"],
    request.form["role"],
    "pending"
        ))

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

upgrade_miller_booking_bill()

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

upgrade_miller_booking_order_id()

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
        mb.id,
        u.name,
        ms.crop,
        mb.quantity,
        mb.status,
        mb.reason,
        mb.decision_at,
        mb.loaded_qty,
        mb.loading_status,
        mb.bill_document,
        mb.order_id
    FROM miller_bookings mb
    JOIN users u ON mb.buyer_id = u.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    WHERE ms.miller_id=?
    ORDER BY mb.created_at DESC
""", (miller_id,))
    bookings = cur.fetchall()

    con.close()

    return render_template(
        "miller.html",
        stocks=stocks,
        bookings=bookings
    )
@app.route("/miller/update_loading/<int:id>", methods=["POST"])
def update_loading(id):
    if session.get("role") != "miller":
        return redirect("/")

    load_qty = int(request.form["load_qty"])

    con = get_db()
    cur = con.cursor()

    # Fetch booking
    cur.execute("""
        SELECT quantity, loaded_qty
        FROM miller_bookings
        WHERE id=?
    """, (id,))
    total_qty, loaded_qty = cur.fetchone()

    new_loaded = loaded_qty + load_qty

    if new_loaded >= total_qty:
        new_loaded = total_qty
        status = "completed"
    else:
        status = "partial"

    cur.execute("""
        UPDATE miller_bookings
        SET loaded_qty=?, loading_status=?
        WHERE id=?
    """, (new_loaded, status, id))

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
        WHERE mb.id=? AND ms.miller_id=? AND mb.loading_status='completed'
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
    profile = cur.fetchone()

    if request.method == "POST":
        shop_name = request.form["shop_name"]
        phone = request.form["phone"]
        address = request.form["address"]

        doc = request.files.get("document")
        filename = profile[5] if profile else None

        if doc and doc.filename:
            filename = secure_filename(doc.filename)
            doc.save(os.path.join(app.config["PROFILE_FOLDER"], filename))

        if profile:
            cur.execute("""
                UPDATE buyer_profiles
                SET shop_name=?, phone=?, address=?, document=?
                WHERE buyer_id=?
            """, (shop_name, phone, address, filename, session["user_id"]

))
        else:
            cur.execute("""
                INSERT INTO buyer_profiles
                (buyer_id, shop_name, phone, address, document)
                VALUES (?,?,?,?,?)
            """, (session["user_id"]

, shop_name, phone, address, filename))

        con.commit()
        con.close()
        return redirect("/buyer/profile")

    con.close()
    return render_template("buyer_profile.html", profile=profile)

@app.route("/miller/approve_booking/<int:id>")
def miller_approve_booking(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
    UPDATE miller_bookings
    SET status='approved', decision_at=CURRENT_TIMESTAMP
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
    SET price=?, quantity=?, condition=?, bag_type=?, deduction=?
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
    SELECT miller_stock.*, users.name
    FROM miller_stock
    JOIN users ON miller_stock.miller_id = users.id
    WHERE miller_stock.quantity > 0
    ORDER BY created_at DESC
    """)
    miller_stocks = cur.fetchall()

    cur.execute("""
SELECT
    mb.id,                 -- 0
    ms.crop,               -- 1
    mb.quantity,           -- 2 Booked
    mb.loaded_qty,         -- 3 Loaded
    (mb.quantity - mb.loaded_qty) AS remaining, -- 4 Remaining
    mb.truck_status,       -- 5 Status
    mb.loaded_at,          -- 6 Last updated
    mb.bill_document,      -- 7 Bill document
    mb.loading_status,      -- 8 Loading status
    mb.order_id            -- 9 Order ID
FROM miller_bookings mb
JOIN miller_stock ms ON mb.stock_id = ms.id
WHERE mb.buyer_id=?
ORDER BY mb.created_at DESC
""", (session.get("user_id"),))

    my_bookings = cur.fetchall()


    con.close()
    return render_template(
        "market.html",
        miller_stocks=miller_stocks,
        my_bookings=my_bookings
    )

@app.route("/book_miller_stock/<int:stock_id>", methods=["POST"])
def book_miller_stock(stock_id):
    if session.get("role") != "buyer":
        return redirect("/market")

    qty = int(request.form["quantity"])
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT quantity FROM miller_stock WHERE id=?", (stock_id,))
    row = cur.fetchone()

    if row and row[0] >= qty:
        cur.execute(
            "UPDATE miller_stock SET quantity=quantity-? WHERE id=?",
            (qty, stock_id)
        )
        order_id = generate_next_order_id()
        cur.execute("""
        INSERT INTO miller_bookings (stock_id,buyer_id,quantity,status,order_id)
        VALUES (?,?,?, 'pending', ?)
        """, (stock_id, get_effective_user_id(), qty, order_id))

        con.commit()   # ✅ VERY IMPORTANT

    con.close()
    return redirect("/market")

@app.route("/cancel_booking/<int:id>")
def cancel_booking(id):
    if session.get("role") != "buyer":
        return redirect("/market")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
    SELECT stock_id, quantity
    FROM miller_bookings
    WHERE id=? AND buyer_id=? AND status='pending'
    """, (id, get_effective_user_id()

))
    row = cur.fetchone()

    if row:
        stock_id, qty = row
        cur.execute(
            "UPDATE miller_stock SET quantity=quantity+? WHERE id=?",
            (qty, stock_id)
        )
        cur.execute(
            "UPDATE miller_bookings SET status='cancelled' WHERE id=?",
            (id,)
        )
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
            mb.id,                 -- invoice id
            buyer.name,            -- buyer
            miller.name,           -- miller
            ms.crop,               -- crop
            mb.quantity,           -- quantity
            ms.price,              -- price
            mb.loaded_at           -- date
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users buyer ON mb.buyer_id = buyer.id
        JOIN users miller ON ms.miller_id = miller.id
        WHERE mb.id=?
          AND mb.buyer_id=?
          AND mb.truck_status='loaded'
    """, (booking_id, get_effective_user_id()

))

    invoice = cur.fetchone()
    con.close()

    if not invoice:
        return "❌ Invoice available only after full loading.", 403

    return render_template("invoice.html", invoice=invoice)


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
