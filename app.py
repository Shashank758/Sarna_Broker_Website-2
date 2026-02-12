from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg2
import psycopg2.extras
import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------ #
#  APP CONFIG
# ------------------------------------------------------------------ #
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# Session security
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

# Upload folders
UPLOAD_FOLDER = "static/uploads/crops"
BILL_FOLDER = "static/uploads/bills"
PROFILE_FOLDER = "static/uploads/miller_docs"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BILL_FOLDER, exist_ok=True)
os.makedirs(PROFILE_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["BILL_FOLDER"] = BILL_FOLDER
app.config["PROFILE_FOLDER"] = PROFILE_FOLDER

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  TWILIO SMS  (env-vars only — no hardcoded secrets)
# ------------------------------------------------------------------ #
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")


def send_sms(to_phone, message_text):
    """Send SMS using Twilio. Returns True on success, False otherwise."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        logger.warning("SMS not configured — missing Twilio env vars. Would send to %s: %s", to_phone, message_text)
        return False

    if not to_phone:
        logger.warning("No phone number provided for SMS")
        return False

    try:
        if not to_phone.startswith("+"):
            if to_phone.startswith("91"):
                to_phone = "+" + to_phone
            else:
                to_phone = "+91" + to_phone.lstrip("0")

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_text, from_=TWILIO_PHONE_NUMBER, to=to_phone
        )
        logger.info("SMS sent to %s (SID %s)", to_phone, message.sid)
        return True
    except Exception as e:
        logger.error("Failed to send SMS to %s: %s", to_phone, e)
        return False


def clean_phone_number(phone):
    if not phone:
        return None
    cleaned = "".join(c for c in str(phone).strip() if c.isdigit() or c == "+")
    if "+" in cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned.replace("+", "")
    return cleaned or None


# ------------------------------------------------------------------ #
#  DATABASE
# ------------------------------------------------------------------ #
def get_db():
    """Return a new psycopg2 connection. Requires DATABASE_URL env var."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except psycopg2.OperationalError as e:
        logger.error("Database connection failed: %s", e)
        raise


# ------------------------------------------------------------------ #
#  SCHEMA INIT  (idempotent — safe to run on every start)
# ------------------------------------------------------------------ #
def init_db():
    con = get_db()
    try:
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT,
            status TEXT DEFAULT 'pending',
            is_staff INTEGER DEFAULT 0,
            parent_miller_id INTEGER
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS miller_stock (
            id SERIAL PRIMARY KEY,
            miller_id INTEGER,
            crop TEXT,
            quantity INTEGER,
            price INTEGER,
            condition TEXT,
            bag_type TEXT,
            deduction INTEGER,
            note TEXT,
            status TEXT DEFAULT 'open',
            auto_approve INTEGER DEFAULT 0,
            auto_approve_min_qty INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS miller_stock_history (
            id SERIAL PRIMARY KEY,
            stock_id INTEGER,
            miller_id INTEGER,
            old_price INTEGER,
            new_price INTEGER,
            old_quantity INTEGER,
            new_quantity INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS miller_bookings (
            id SERIAL PRIMARY KEY,
            stock_id INTEGER,
            buyer_id INTEGER,
            quantity INTEGER,
            status TEXT DEFAULT 'pending',
            reason TEXT,
            decision_at TIMESTAMP,
            loading_status TEXT DEFAULT 'pending',
            loaded_qty INTEGER DEFAULT 0,
            close_reason TEXT,
            closed_by TEXT,
            truck_status TEXT DEFAULT 'pending',
            truck_remark TEXT,
            loaded_at TIMESTAMP,
            bill_document TEXT,
            qc_weight INTEGER,
            qc_moisture REAL,
            qc_remarks TEXT,
            qc_status TEXT DEFAULT 'pending',
            qc_at TIMESTAMP,
            order_id TEXT,
            price INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS miller_deduction_options (
            id SERIAL PRIMARY KEY,
            miller_id INTEGER,
            text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS miller_profiles (
            id SERIAL PRIMARY KEY,
            miller_id INTEGER UNIQUE,
            mill_name TEXT,
            phone TEXT,
            address TEXT,
            document TEXT,
            owner_name TEXT,
            owner_phone TEXT,
            staff_phone TEXT,
            accountant_phone TEXT,
            gst_doc TEXT,
            license_doc TEXT,
            other_doc TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS buyer_profiles (
            id SERIAL PRIMARY KEY,
            buyer_id INTEGER UNIQUE,
            shop_name TEXT,
            phone TEXT,
            address TEXT,
            document TEXT,
            owner_name TEXT,
            gst_doc TEXT,
            license_doc TEXT,
            other_doc TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS loading_invoices (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER,
            loaded_qty INTEGER,
            invoice_file TEXT,
            truck_number TEXT,
            qc_weight INTEGER,
            qc_moisture REAL,
            qc_remarks TEXT,
            qc_status TEXT DEFAULT 'pending',
            qc_at TIMESTAMP,
            final_invoice_file TEXT,
            payment_status TEXT DEFAULT 'pending',
            payment_at TIMESTAMP,
            debit_note INTEGER,
            qc_freight INTEGER,
            qc_broken REAL,
            qc_karda REAL,
            qc_oil REAL,
            qc_mitti REAL,
            qc_ssa REAL,
            qc_claim REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_bills (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            bill_file TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS crops (
            id SERIAL PRIMARY KEY,
            farmer_id INTEGER,
            crop TEXT,
            variety TEXT,
            price INTEGER,
            quantity INTEGER,
            location TEXT,
            image TEXT,
            sold INTEGER DEFAULT 0
        )""")

        # Default admin (hashed password)
        cur.execute("SELECT id FROM users WHERE role='admin'")
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (name, email, password, role, status) VALUES (%s,%s,%s,%s,%s)",
                ("Admin", "admin@sarna.com", generate_password_hash("admin123"), "admin", "approved"),
            )

        con.commit()
    except Exception as e:
        con.rollback()
        logger.error("init_db failed: %s", e)
        raise
    finally:
        con.close()


init_db()


# ------------------------------------------------------------------ #
#  HELPERS
# ------------------------------------------------------------------ #
def get_effective_user_id():
    if session.get("role") == "miller" and session.get("is_staff"):
        parent_id = session.get("parent_miller_id")
        if parent_id:
            return parent_id
    return session.get("user_id")


def get_buyer_phone(buyer_id):
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT phone FROM buyer_profiles WHERE buyer_id=%s", (buyer_id,))
        result = cur.fetchone()
        return clean_phone_number(result[0]) if result and result[0] else None
    finally:
        con.close()


def get_miller_phone(miller_id):
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT owner_phone, phone FROM miller_profiles WHERE miller_id=%s", (miller_id,))
        result = cur.fetchone()
        if result:
            return clean_phone_number(result[0] or result[1])
        return None
    finally:
        con.close()


def get_all_buyer_phones():
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT DISTINCT phone FROM buyer_profiles WHERE phone IS NOT NULL AND phone != ''")
        results = cur.fetchall()
        phones = [clean_phone_number(r[0]) for r in results if r[0]]
        return [p for p in phones if p]
    finally:
        con.close()


def generate_next_order_id():
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM miller_bookings")
        count = cur.fetchone()[0]
        return f"ORDER-{10001 + count}"
    finally:
        con.close()


# ------------------------------------------------------------------ #
#  CONTEXT PROCESSOR  (navbar notification counts)
# ------------------------------------------------------------------ #
@app.context_processor
def inject_pending_counts():
    if not session.get("user_id"):
        return dict()
    counts = {}
    miller_id = get_effective_user_id() if session.get("role") == "miller" else session.get("user_id")
    buyer_id = session.get("user_id")

    con = None
    try:
        con = get_db()
        cur = con.cursor()

        if session.get("role") == "miller":
            cur.execute(
                "SELECT count(*) FROM miller_bookings mb JOIN miller_stock ms ON mb.stock_id=ms.id WHERE ms.miller_id=%s AND mb.status='pending'",
                (miller_id,),
            )
            counts["miller_pending_orders"] = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM loading_invoices li JOIN miller_bookings mb ON li.booking_id=mb.id JOIN miller_stock ms ON mb.stock_id=ms.id WHERE ms.miller_id=%s AND (li.qc_status IS NULL OR li.qc_status='pending')",
                (miller_id,),
            )
            counts["miller_pending_qc"] = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM loading_invoices li JOIN miller_bookings mb ON li.booking_id=mb.id JOIN miller_stock ms ON mb.stock_id=ms.id WHERE ms.miller_id=%s AND li.final_invoice_file IS NOT NULL AND (li.payment_status IS NULL OR li.payment_status!='paid')",
                (miller_id,),
            )
            counts["miller_pending_payments"] = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM miller_bookings mb JOIN miller_stock ms ON mb.stock_id=ms.id WHERE ms.miller_id=%s AND mb.status='approved' AND (mb.loading_status IS NULL OR mb.loading_status IN ('pending','partial','active'))",
                (miller_id,),
            )
            counts["miller_active_orders"] = cur.fetchone()[0]

            counts["miller_total_pending"] = (
                counts["miller_pending_orders"]
                + counts["miller_pending_qc"]
                + counts["miller_pending_payments"]
                + counts["miller_active_orders"]
            )

        if session.get("role") == "buyer":
            cur.execute("SELECT count(*) FROM miller_bookings WHERE buyer_id=%s AND status='pending'", (buyer_id,))
            counts["buyer_pending_requests"] = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM miller_bookings WHERE buyer_id=%s AND status='approved' AND (loading_status IS NULL OR loading_status IN ('pending','partial','active'))",
                (buyer_id,),
            )
            counts["buyer_active_orders"] = cur.fetchone()[0]

            counts["buyer_total_pending"] = counts["buyer_pending_requests"] + counts["buyer_active_orders"]

    except Exception as e:
        logger.error("Error in context processor: %s", e)
        return dict()
    finally:
        if con:
            con.close()
    return counts


# ------------------------------------------------------------------ #
#  ROUTES — AUTH
# ------------------------------------------------------------------ #
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        email = request.form["email"].lower()
        password = request.form["password"]

        con = get_db()
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT id, role, COALESCE(is_staff,0), parent_miller_id, status, password FROM users WHERE lower(email)=%s",
                (email,),
            )
            user = cur.fetchone()
        finally:
            con.close()

        if not user:
            return render_template("login.html", error="User not found")

        if user[4] != "approved":
            return render_template("login.html", error=f"Your account is {user[4]}. Please wait for admin approval.")

        stored_pw = user[5]
        # Support both hashed and legacy plain-text passwords
        pw_ok = False
        if stored_pw and stored_pw.startswith(("pbkdf2:", "scrypt:")):
            pw_ok = check_password_hash(stored_pw, password)
        else:
            pw_ok = stored_pw == password  # legacy plain-text fallback

        if not pw_ok:
            return render_template("login.html", error="Invalid password")

        session["user_id"] = user[0]
        session["role"] = user[1]
        session["is_staff"] = user[2]
        session["parent_miller_id"] = user[3]
        session.permanent = True

        if user[1] == "admin":
            return redirect("/admin/dashboard")
        elif user[1] == "miller":
            return redirect("/miller")
        elif user[1] == "buyer":
            return redirect("/market")
        elif user[1] == "farmer":
            return redirect("/farmer")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        con = get_db()
        try:
            name = request.form["name"]
            email = request.form["email"].lower()
            password = request.form["password"]
            role = request.form["role"]
            phone = request.form.get("phone")
            address = request.form.get("address")

            cur = con.cursor()
            cur.execute(
                "INSERT INTO users (name,email,password,role,status) VALUES (%s,%s,%s,%s,'pending') RETURNING id",
                (name, email, generate_password_hash(password), role),
            )
            user_id = cur.fetchone()[0]

            def save_doc(file_obj, prefix):
                if file_obj and file_obj.filename:
                    fname = secure_filename(f"{prefix}_{user_id}_{file_obj.filename}")
                    file_obj.save(os.path.join(app.config["PROFILE_FOLDER"], fname))
                    return fname
                return None

            gst_doc = save_doc(request.files.get("gst_doc"), "gst")
            license_doc = save_doc(request.files.get("license_doc"), "lic")
            other_doc = save_doc(request.files.get("other_doc"), "other")

            if role == "miller":
                cur.execute(
                    """INSERT INTO miller_profiles
                       (miller_id,mill_name,phone,address,document,owner_name,owner_phone,staff_phone,accountant_phone,gst_doc,license_doc,other_doc)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
                        request.form.get("mill_name"),
                        phone,
                        address,
                        gst_doc,
                        request.form.get("owner_name"),
                        request.form.get("owner_phone"),
                        request.form.get("staff_phone"),
                        request.form.get("accountant_phone"),
                        gst_doc,
                        license_doc,
                        other_doc,
                    ),
                )
            elif role == "buyer":
                cur.execute(
                    """INSERT INTO buyer_profiles
                       (buyer_id,shop_name,phone,address,document,owner_name,gst_doc,license_doc,other_doc)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        user_id,
                        request.form.get("shop_name"),
                        phone,
                        address,
                        gst_doc,
                        request.form.get("owner_name"),
                        gst_doc,
                        license_doc,
                        other_doc,
                    ),
                )

            con.commit()
            return redirect("/")
        except Exception as e:
            con.rollback()
            logger.error("Registration failed: %s", e)
            return render_template("register.html", error=f"Registration failed: {e}")
        finally:
            con.close()

    return render_template("register.html")


# ------------------------------------------------------------------ #
#  ROUTES — MILLER
# ------------------------------------------------------------------ #
@app.route("/miller", methods=["GET", "POST"])
def miller_dashboard():
    if session.get("role") != "miller":
        return redirect("/")
    miller_id = get_effective_user_id()

    con = get_db()
    try:
        cur = con.cursor()

        if request.method == "POST":
            if session.get("is_staff"):
                return redirect("/miller")

            cur.execute(
                """INSERT INTO miller_stock
                   (miller_id,crop,quantity,price,condition,bag_type,deduction,note,auto_approve_min_qty)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    miller_id,
                    request.form["crop"],
                    100000,
                    request.form["price"],
                    request.form["condition"],
                    request.form["bag_type"],
                    request.form["deduction"],
                    request.form.get("note", ""),
                    request.form.get("auto_approve_min_qty", 0),
                ),
            )
            new_id = cur.fetchone()[0]
            cur.execute("UPDATE miller_stock SET status='open' WHERE id=%s", (new_id,))
            con.commit()

            msg = f"🆕 New stock available! {request.form['crop']} - Price: ₹{request.form['price']}/unit. {request.form.get('note','')} Check market."
            for phone in get_all_buyer_phones():
                send_sms(phone, msg)

            return redirect(url_for("miller_dashboard"))

        # GET — dashboard data
        cur.execute("SELECT * FROM miller_stock WHERE miller_id=%s ORDER BY created_at DESC", (miller_id,))
        stocks = cur.fetchall()

        cur.execute(
            """SELECT mb.id, u.name, ms.crop, mb.quantity, mb.status, mb.reason, mb.decision_at,
                      mb.loaded_qty, mb.loading_status, mb.close_reason, mb.order_id,
                      mb.qc_weight, mb.qc_moisture, mb.qc_remarks, mb.qc_status, mb.qc_at,
                      COALESCE(li.payment_status,'pending'), li.invoice_file, li.paid_at
               FROM miller_bookings mb
               JOIN users u ON mb.buyer_id=u.id
               JOIN miller_stock ms ON mb.stock_id=ms.id
               LEFT JOIN loading_invoices li ON li.booking_id=mb.id
               WHERE ms.miller_id=%s ORDER BY mb.created_at DESC""",
            (miller_id,),
        )
        bookings = cur.fetchall()

        cur.execute(
            "SELECT id, text FROM miller_deduction_options WHERE miller_id=%s ORDER BY created_at DESC",
            (miller_id,),
        )
        opts = cur.fetchall()

        cur.execute(
            """SELECT li.id, li.booking_id, li.loaded_qty, li.invoice_file, li.truck_number, li.created_at,
                      li.qc_weight, li.qc_moisture, li.qc_remarks, li.qc_status, li.qc_at,
                      li.final_invoice_file, li.payment_status, li.payment_at, li.qc_freight
               FROM loading_invoices li
               JOIN miller_bookings mb ON li.booking_id=mb.id
               JOIN miller_stock ms ON mb.stock_id=ms.id
               WHERE ms.miller_id=%s ORDER BY li.created_at ASC""",
            (miller_id,),
        )
        inv_rows = cur.fetchall()
        invoices_map = {}
        for r in inv_rows:
            invoices_map.setdefault(r[1], []).append(
                {
                    "id": r[0], "qty": r[2], "file": r[3], "truck_number": r[4], "date": r[5],
                    "qc_weight": r[6], "qc_moisture": r[7], "qc_remarks": r[8],
                    "qc_status": r[9] or "pending", "qc_at": r[10],
                    "final_invoice_file": r[11], "payment_status": r[12] or "pending",
                    "payment_at": r[13], "qc_freight": r[14],
                }
            )

        cur.execute(
            """SELECT COUNT(*) FROM loading_invoices li
               JOIN miller_bookings mb ON li.booking_id=mb.id
               JOIN miller_stock ms ON mb.stock_id=ms.id
               WHERE ms.miller_id=%s AND li.final_invoice_file IS NOT NULL
                 AND (li.payment_status IS NULL OR li.payment_status!='paid')""",
            (miller_id,),
        )
        pending_pay = cur.fetchone()[0] or 0
    finally:
        con.close()

    return render_template(
        "miller.html",
        stocks=stocks,
        bookings=bookings,
        invoices_map=invoices_map,
        deduction_options=opts,
        total_pending_payments=pending_pay,
        active_stocks_count=len(stocks),
        pending_bookings_count=sum(1 for b in bookings if b[4] == "pending"),
        approved_bookings_count=sum(1 for b in bookings if b[4] == "approved"),
        qc_pending_count=sum(1 for r in inv_rows if (r[9] is None or r[9] == "pending")),
    )


@app.route("/update_miller_stock/<int:id>", methods=["POST"])
def update_miller_stock(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    try:
        cur = con.cursor()

        cur.execute("SELECT price, quantity FROM miller_stock WHERE id=%s", (id,))
        old = cur.fetchone() or (0, 0)

        cur.execute(
            """UPDATE miller_stock_history SET new_price=%s, updated_at=CURRENT_TIMESTAMP
               WHERE stock_id=%s AND id=(SELECT MAX(id) FROM miller_stock_history WHERE stock_id=%s)""",
            (request.form["price"], id, id),
        )

        cur.execute(
            """UPDATE miller_stock SET
               note=%s, condition=%s, bag_type=%s, deduction=%s, price=%s,
               auto_approve_min_qty=%s, status='open'
               WHERE id=%s AND miller_id=%s""",
            (
                request.form.get("note", ""),
                request.form["condition"],
                request.form["bag_type"],
                request.form["deduction"],
                request.form["price"],
                request.form.get("auto_approve_min_qty", 0),
                id,
                get_effective_user_id(),
            ),
        )

        cur.execute(
            """INSERT INTO miller_stock_history
               (stock_id,miller_id,old_price,new_price,old_quantity,new_quantity)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (id, get_effective_user_id(), old[0], request.form["price"], old[1], old[1]),
        )

        con.commit()
    finally:
        con.close()
    return redirect("/miller")


@app.route("/miller/respond_booking/<int:id>", methods=["POST"])
def respond_booking(id):
    if session.get("role") != "miller":
        return redirect("/")

    action = request.form["action"]
    con = get_db()
    try:
        cur = con.cursor()

        cur.execute(
            "SELECT mb.buyer_id, mb.order_id, ms.crop, mb.quantity, mb.stock_id FROM miller_bookings mb JOIN miller_stock ms ON mb.stock_id=ms.id WHERE mb.id=%s",
            (id,),
        )
        meta = cur.fetchone()

        if action == "approve":
            cur.execute("UPDATE miller_bookings SET status='approved', decision_at=CURRENT_TIMESTAMP WHERE id=%s", (id,))
            if meta:
                phone = get_buyer_phone(meta[0])
                if phone:
                    send_sms(phone, f"✅ Order {meta[1]} Approved! {meta[2]}. Please arrange for loading.")
        else:
            reason = request.form.get("reason", "No reason provided")
            if meta:
                cur.execute("UPDATE miller_stock SET quantity=quantity+%s WHERE id=%s", (meta[3], meta[4]))
            cur.execute(
                "UPDATE miller_bookings SET status='declined', reason=%s, decision_at=CURRENT_TIMESTAMP WHERE id=%s",
                (reason, id),
            )
            if meta:
                phone = get_buyer_phone(meta[0])
                if phone:
                    send_sms(phone, f"❌ Order {meta[1]} declined. {meta[2]} - Reason: {reason}")

        con.commit()
    finally:
        con.close()
    return redirect("/miller")


@app.route("/miller_save_deduction", methods=["POST"])
def miller_save_deduction():
    if session.get("role") != "miller":
        return redirect("/")
    text = request.form.get("text")
    if text:
        con = get_db()
        try:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO miller_deduction_options (miller_id, text) VALUES (%s,%s)",
                (get_effective_user_id(), text),
            )
            con.commit()
        finally:
            con.close()
    return redirect("/miller")


# ------------------------------------------------------------------ #
#  ROUTES — BUYER / MARKET
# ------------------------------------------------------------------ #
@app.route("/market")
def market():
    if session.get("role") != "buyer":
        return redirect("/")

    con = get_db()
    try:
        cur = con.cursor()

        cur.execute(
            """SELECT ms.*, u.name, mp.mill_name, mp.address
               FROM miller_stock ms
               JOIN users u ON ms.miller_id=u.id
               LEFT JOIN miller_profiles mp ON ms.miller_id=mp.miller_id
               WHERE ms.status='open' AND ms.quantity>0
               ORDER BY ms.created_at DESC"""
        )
        stocks = cur.fetchall()

        cur.execute(
            """SELECT mb.id, ms.crop, mb.quantity, mb.status, mb.reason, u.name, mb.decision_at,
                      mb.loading_status, mb.loaded_qty, mb.order_id,
                      COALESCE(li.payment_status,'pending'), li.invoice_file, li.paid_at,
                      ms.price, ms.condition, ms.bag_type, ms.deduction,
                      li.id, li.final_invoice_file
               FROM miller_bookings mb
               JOIN miller_stock ms ON mb.stock_id=ms.id
               JOIN users u ON ms.miller_id=u.id
               LEFT JOIN loading_invoices li ON li.booking_id=mb.id
               WHERE mb.buyer_id=%s ORDER BY mb.created_at DESC""",
            (session["user_id"],),
        )
        rows = cur.fetchall()

        bookings_map = {}
        for r in rows:
            bid = r[0]
            if bid not in bookings_map:
                bookings_map[bid] = {
                    "id": bid, "crop": r[1], "quantity": r[2], "status": r[3], "reason": r[4],
                    "miller_name": r[5], "decision_at": r[6], "loading_status": r[7],
                    "loaded_qty": r[8], "order_id": r[9], "price": r[13], "condition": r[14],
                    "bag_type": r[15], "deduction": r[16], "invoices": [],
                }
            if r[17]:
                bookings_map[bid]["invoices"].append(
                    {"id": r[17], "payment_status": r[10], "invoice_file": r[11], "paid_at": r[12], "final_invoice_file": r[18]}
                )
    finally:
        con.close()

    return render_template("market.html", stocks=stocks, bookings=list(bookings_map.values()), now=datetime.now())


@app.route("/book_miller_stock/<int:stock_id>", methods=["POST"])
def book_miller_stock(stock_id):
    if session.get("role") != "buyer":
        return redirect("/market")

    try:
        qty = float(request.form["quantity"])
    except (TypeError, ValueError):
        flash("Invalid quantity.", "error")
        return redirect("/market")
    if qty <= 0:
        flash("Quantity must be > 0.", "error")
        return redirect("/market")

    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT quantity, status, price, auto_approve_min_qty FROM miller_stock WHERE id=%s", (stock_id,))
        row = cur.fetchone()

        if not row or row[1] != "open" or row[0] < qty:
            flash("Stock unavailable or insufficient.", "error")
        else:
            pid = generate_next_order_id()
            auto = (row[3] or 0) > 0 and qty <= (row[3] or 0)
            status = "approved" if auto else "pending"

            cur.execute(
                """INSERT INTO miller_bookings (stock_id,buyer_id,quantity,status,order_id,price)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (stock_id, session["user_id"], qty, status, pid, row[2]),
            )
            bid = cur.fetchone()[0]

            if auto:
                cur.execute("UPDATE miller_bookings SET decision_at=CURRENT_TIMESTAMP WHERE id=%s", (bid,))
                flash(f"Order {pid} Auto-Approved!", "success")
            else:
                flash(f"Order {pid} placed. Waiting for approval.", "success")

            cur.execute("UPDATE miller_stock SET quantity=quantity-%s WHERE id=%s", (qty, stock_id))
            cur.execute("UPDATE miller_stock SET status='closed' WHERE id=%s AND quantity<=0", (stock_id,))

            # SMS to Miller
            cur.execute("SELECT miller_id, crop FROM miller_stock WHERE id=%s", (stock_id,))
            sinfo = cur.fetchone()
            if sinfo:
                mphone = get_miller_phone(sinfo[0])
                if mphone:
                    txt = f"✅ Auto-Approved Order {pid}: {sinfo[1]}" if auto else f"🆕 New booking {pid}: {sinfo[1]}. Please review."
                    send_sms(mphone, txt)

            con.commit()
    except Exception as e:
        con.rollback()
        logger.error("Booking error: %s", e)
        flash("An error occurred while placing the order.", "error")
    finally:
        con.close()
    return redirect("/market")


# ------------------------------------------------------------------ #
#  ROUTES — FARMER
# ------------------------------------------------------------------ #
@app.route("/farmer")
def farmer_dashboard():
    if session.get("role") != "farmer":
        return redirect("/")
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT * FROM crops WHERE farmer_id=%s ORDER BY id DESC", (session["user_id"],))
        my_crops = cur.fetchall()
    finally:
        con.close()
    return render_template("my_commodity.html", crops=my_crops)


@app.route("/add_crop", methods=["POST"])
def add_crop():
    if session.get("role") != "farmer":
        return redirect("/")
    con = get_db()
    try:
        cur = con.cursor()
        file = request.files.get("image")
        img_name = None
        if file and file.filename:
            img_name = secure_filename(f"crop_{session['user_id']}_{file.filename}")
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], img_name))

        cur.execute(
            """INSERT INTO crops (farmer_id,crop,variety,price,quantity,location,image)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (session["user_id"], request.form["crop"], request.form["variety"],
             request.form["price"], request.form["quantity"], request.form["location"], img_name),
        )
        con.commit()
    finally:
        con.close()
    return redirect("/farmer")


# ------------------------------------------------------------------ #
#  ROUTES — ADMIN
# ------------------------------------------------------------------ #
@app.route("/admin/dashboard")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect("/")
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("SELECT * FROM users ORDER BY id DESC")
        users = cur.fetchall()
        cur.execute(
            "SELECT tb.id, u.name, tb.bill_file, tb.created_at, tb.phone FROM trade_bills tb JOIN users u ON tb.user_id=u.id ORDER BY tb.created_at DESC"
        )
        bills = cur.fetchall()
    finally:
        con.close()
    return render_template("admin.html", users=users, bills=bills)


@app.route("/admin/approve_user/<int:user_id>")
def approve_user(user_id):
    if session.get("role") != "admin":
        return redirect("/")
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("UPDATE users SET status='approved' WHERE id=%s", (user_id,))
        con.commit()
    finally:
        con.close()
    phone = get_buyer_phone(user_id) or get_miller_phone(user_id)
    if phone:
        send_sms(phone, "✅ Your Sarna account has been approved! You can now login.")
    return redirect("/admin/dashboard")


@app.route("/admin/reject_user/<int:user_id>")
def reject_user(user_id):
    if session.get("role") != "admin":
        return redirect("/")
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute("UPDATE users SET status='rejected' WHERE id=%s", (user_id,))
        con.commit()
    finally:
        con.close()
    return redirect("/admin/dashboard")


@app.route("/upload_bill", methods=["POST"])
def upload_bill():
    if "user_id" not in session:
        return redirect("/")
    f = request.files["bill"]
    if f:
        fn = secure_filename(f.filename)
        f.save(os.path.join(app.config["BILL_FOLDER"], fn))
        con = get_db()
        try:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO trade_bills (user_id, bill_file, phone) VALUES (%s,%s,%s)",
                (session["user_id"], fn, request.form.get("phone")),
            )
            con.commit()
        finally:
            con.close()
    return redirect("/" + session["role"])


# ------------------------------------------------------------------ #
#  ROUTES — PASSWORD RESET
# ------------------------------------------------------------------ #
@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].lower()
        con = get_db()
        try:
            cur = con.cursor()
            cur.execute("SELECT id, role, is_staff, parent_miller_id FROM users WHERE lower(email)=%s", (email,))
            user = cur.fetchone()

            if not user:
                return render_template("forgot_password.html", error="Email not found.")

            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires_at = datetime.now() + timedelta(hours=1)

            cur.execute(
                "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s,%s,%s)",
                (user[0], token_hash, expires_at),
            )
            con.commit()

            reset_link = url_for("reset_password", token=token, _external=True)
            phone_num = None

            # Try buyer profile
            cur.execute("SELECT phone FROM buyer_profiles WHERE buyer_id=%s", (user[0],))
            r = cur.fetchone()
            if r and r[0]:
                phone_num = clean_phone_number(r[0])

            if not phone_num:
                target_id = user[0]
                if user[1] == "miller" and user[2] and user[3]:
                    target_id = user[3]
                cur.execute(
                    "SELECT owner_phone, staff_phone, accountant_phone, phone FROM miller_profiles WHERE miller_id=%s",
                    (target_id,),
                )
                prof = cur.fetchone()
                if prof:
                    for p in prof:
                        if p:
                            phone_num = clean_phone_number(p)
                            if phone_num:
                                break

            if phone_num:
                send_sms(phone_num, f"Reset Password: {reset_link}")
                return render_template("forgot_password.html", message="Reset link sent to your phone.")
            else:
                return render_template("forgot_password.html", error="No phone number linked to this account.")
        finally:
            con.close()

    return render_template("forgot_password.html")


@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    con = get_db()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT user_id FROM password_resets WHERE token_hash=%s AND used=0 AND expires_at > %s",
            (token_hash, datetime.now()),
        )
        res = cur.fetchone()

        if not res:
            return "Invalid or expired token."

        if request.method == "POST":
            new_pass = request.form["password"]
            cur.execute("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(new_pass), res[0]))
            cur.execute("UPDATE password_resets SET used=1 WHERE token_hash=%s", (token_hash,))
            con.commit()
            return redirect("/")
    finally:
        con.close()

    return render_template("reset_password.html", token=token)


# ------------------------------------------------------------------ #
#  ENTRYPOINT
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", host="0.0.0.0")
