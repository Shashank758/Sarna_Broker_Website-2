from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg2
import psycopg2.extras
import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta, date
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)  # Load environment variables from .env file

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ---------------- CONFIG ----------------
UPLOAD_FOLDER = "static/uploads/crops"
BILL_FOLDER = "static/uploads/bills"
PROFILE_FOLDER = "static/uploads/miller_docs" 

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BILL_FOLDER, exist_ok=True)
os.makedirs(PROFILE_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["BILL_FOLDER"] = BILL_FOLDER

# Session security
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 43200  # 12 hours
app.config["PROFILE_FOLDER"] = PROFILE_FOLDER 

# ---------------- SMS CONFIG ----------------
# Twilio credentials - set these as environment variables or hardcode below
# Option 1: Use environment variables (recommended for production)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

# Twilio credentials are loaded from environment variables only.
# SMS features are silently disabled when credentials are not set.

# ---------------- SMS HELPER FUNCTION ----------------
def send_sms(to_phone, message_text):
    """Send SMS using Twilio. Returns True if successful, False otherwise."""
    # Check if credentials are configured
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning(f"SMS not configured. Missing credentials.")
        logger.warning(f"   Account SID: {'Set' if TWILIO_ACCOUNT_SID else 'Missing'}")
        logger.warning(f"   Auth Token: {'Set' if TWILIO_AUTH_TOKEN else 'Missing'}")
        logger.warning(f"   Phone Number: {'Set' if TWILIO_PHONE_NUMBER else 'Missing'}")
        logger.info(f"   Would send to {to_phone}: {message_text}")
        return False
    
    if not to_phone:
        logger.warning("No phone number provided for SMS")
        return False
    
    try:
        # Ensure phone number has country code (assume +91 for India if not present)
        original_phone = to_phone
        if not to_phone.startswith('+'):
            if to_phone.startswith('91'):
                to_phone = '+' + to_phone
            else:
                to_phone = '+91' + to_phone.lstrip('0')
        
        logger.info(f"Attempting to send SMS to {to_phone} (original: {original_phone})")
        logger.info(f"   From: {TWILIO_PHONE_NUMBER}")
        logger.info(f"   Message: {message_text[:50]}...")
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_text,
            from_=TWILIO_PHONE_NUMBER,
            to=to_phone
        )
        logger.info(f"SMS sent successfully to {to_phone}")
        logger.info(f"   Message SID: {message.sid}")
        logger.info(f"   Status: {message.status}")
        return True
    except Exception as e:
        logger.error(f"Failed to send SMS to {to_phone}")
        logger.error(f"   Error Type: {type(e).__name__}")
        logger.error(f"   Error Message: {str(e)}")
        # Print more details for common errors
        if "Invalid" in str(e) or "not found" in str(e).lower():
            logger.warning(f"   Check your Twilio credentials (Account SID, Auth Token)")
        if "phone number" in str(e).lower() or "number" in str(e).lower():
            logger.warning(f"   Check the phone number format: {to_phone}")
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
    cur.execute("SELECT phone FROM buyer_profiles WHERE buyer_id=%s", (buyer_id,))
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
    cur.execute("SELECT owner_phone, phone FROM miller_profiles WHERE miller_id=%s", (miller_id,))
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


# ---------------- JINJA FILTERS ----------------
@app.template_filter('format_currency')
def format_currency(value):
    try:
        if value is None: return "0"
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return value

@app.template_filter('tojson')
def to_json_filter(value):
    import json
    def json_serial(obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError ("Type %s not serializable" % type(obj))
    return json.dumps(value, default=json_serial)


def get_phone_for_password_reset(user_id, role=None, is_staff=0, parent_miller_id=None):
    """Get a phone number suitable for password reset SMS.

    We try the user's own profile first, then fall back to parent miller profile (for staff).
    """
    # Buyer profile phone
    phone = get_buyer_phone(user_id)
    if phone:
        return phone

    # Miller profile phones (for miller + any other user types that still have a miller profile)
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT owner_phone, staff_phone, accountant_phone, phone FROM miller_profiles WHERE miller_id=%s",
        (user_id,),
    )
    r = cur.fetchone()
    con.close()
    if r:
        for p in (r[0], r[1], r[2], r[3]):
            p = clean_phone_number(p)
            if p:
                return p

    # Staff user: try parent miller profile's staff_phone first
    if parent_miller_id:
        con = get_db()
        cur = con.cursor()
        cur.execute(
            "SELECT staff_phone, accountant_phone, owner_phone, phone FROM miller_profiles WHERE miller_id=%s",
            (parent_miller_id,),
        )
        r = cur.fetchone()
        con.close()
        if r:
            for p in (r[0], r[1], r[2], r[3]):
                p = clean_phone_number(p)
                if p:
                    return p

    return None

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
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    
    # Use SSL for production (Render), but disable for local
    if "render" in db_url or "aws" in db_url:
        ssl_mode = "require"
    else:
        ssl_mode = "prefer" # Allow local without SSL

    try:
        con = psycopg2.connect(db_url, sslmode=ssl_mode)
    except psycopg2.OperationalError:
       # Fallback for local if prefer/require fails (e.g. windows local pg often needs disable)
       con = psycopg2.connect(db_url, sslmode="disable")
       
    con.autocommit = False
    return con


def log_activity(action, target_id=None, details=None, user_id=None, role=None):
    """Log any user activity to the database."""
    try:
        if not user_id:
            user_id = session.get("user_id")
        if not role:
            role = session.get("role")
            
        # If no user context, we can't log (or log as system?)
        if not user_id:
            return

        con = get_db()
        cur = con.cursor()
        # admin_logs table now has role column (admin_id is used as user_id)
        cur.execute("""
            INSERT INTO admin_logs (admin_id, role, action, target_id, details)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, role, action, target_id, details))
        con.commit()
        con.close()
    except Exception as e:
        print(f"Failed to log activity: {e}")



def upgrade_db():
    con = get_db()
    cur = con.cursor()

    # Get existing columns
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

    if "decision_at" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN decision_at TIMESTAMP")

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
    id SERIAL PRIMARY KEY,
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
        id SERIAL PRIMARY KEY,
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
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        bill_file TEXT,
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # MILLER STOCK
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # MILLER STOCK HISTORY
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
    )
    """)

    # BUYER BOOKINGS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS miller_bookings (
        id SERIAL PRIMARY KEY,
        stock_id INTEGER,
        buyer_id INTEGER,
        quantity DECIMAL(10,2),
        status VARCHAR(20) DEFAULT 'pending',
        order_id VARCHAR(20),
        price DECIMAL(10,2),
        loading_status VARCHAR(20) DEFAULT 'pending',
        loaded_qty DECIMAL(10,2) DEFAULT 0,
        loaded_at TIMESTAMP,
        reason TEXT,
        decision_at TIMESTAMP,
        close_reason TEXT,
        closed_by VARCHAR(20),
        qc_weight DECIMAL(10,2),
        qc_moisture DECIMAL(5,2),
        qc_remarks TEXT,
        qc_status VARCHAR(20) DEFAULT 'pending',
        qc_at TIMESTAMP,
        bill_document VARCHAR(255),
        truck_status VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # MILLER DEDUCTION OPTIONS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS miller_deduction_options (
        id SERIAL PRIMARY KEY,
        miller_id INTEGER,
        text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- MILLER PROFILE ----------------
    cur.execute("""
CREATE TABLE IF NOT EXISTS miller_profiles (
    id SERIAL PRIMARY KEY,
    miller_id INTEGER UNIQUE,
    mill_name TEXT,
    phone TEXT,
    address TEXT,
    document TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

    # DEFAULT ADMIN
    # DEFAULT ADMIN (SAFE)
    cur.execute("SELECT id FROM users WHERE role='admin'")
    if not cur.fetchone():
     cur.execute("""
        INSERT INTO users (name, email, password, role, status)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        "Admin",
        "admin@sarna.com",
        generate_password_hash("admin123"),
        "admin",
        "approved"
    ))


    con.commit()
    con.close()
    
def upgrade_miller_stock_status():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_stock",))
    cols = [c[0] for c in cur.fetchall()]

    if "status" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN status TEXT DEFAULT 'open'
        """)

    con.commit()
    con.close()


def upgrade_staff_system():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("users",))
    cols = [c[0] for c in cur.fetchall()]

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
        id SERIAL PRIMARY KEY,
        booking_id INTEGER,
        loaded_qty INTEGER,
        invoice_file TEXT,
        truck_number TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Add new fields to loading_invoices if they don't exist
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("loading_invoices",))
    cols = [c[0] for c in cur.fetchall()]

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
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_at TIMESTAMP")
    
    # Per-truck final invoice fields
    if "final_invoice_file" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN final_invoice_file TEXT")
    if "payment_status" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN payment_status TEXT DEFAULT 'pending'")
    if "payment_at" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN payment_at TIMESTAMP")

    con.commit()
    con.close()

def upgrade_loading_invoices_debit_note():
    """Add debit_note column to loading_invoices."""
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("loading_invoices",))
    cols = [c[0] for c in cur.fetchall()]

    if "debit_note" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN debit_note INTEGER")
    
    # Ensure qc_freight is there too just in case
    if "qc_freight" not in cols:
        cur.execute("ALTER TABLE loading_invoices ADD COLUMN qc_freight INTEGER")

    con.commit()
    con.close()



def upgrade_loading_invoices_extended_qc():
    """Add extended QC fields for specific crops."""
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("loading_invoices",))
    cols = [c[0] for c in cur.fetchall()]

    new_cols = [
        ("qc_broken", "REAL"),
        ("qc_karda", "REAL"),
        ("qc_oil", "REAL"),
        ("qc_mitti", "REAL"),
        ("qc_ssa", "REAL"),
        ("qc_claim", "REAL"),
    ]

    for col_name, col_type in new_cols:
        if col_name not in cols:
            cur.execute(f"ALTER TABLE loading_invoices ADD COLUMN {col_name} {col_type}")

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

@app.context_processor
def inject_pending_counts():
    """Inject notification counts into templates."""
    if not session.get("user_id"):
        return dict()
    
    counts = {}
    user_id = session.get("user_id")
    
    # Determine IDs
    miller_id = user_id
    if session.get("role") == "miller" and session.get("is_staff"):
        if session.get("parent_miller_id"):
            miller_id = session.get("parent_miller_id")
            
    buyer_id = user_id
    
    try:
        con = get_db()
        cur = con.cursor()
        
        # --- MILLER METRICS ---
        # 1. Pending Orders
        cur.execute("""
            SELECT count(*) FROM miller_bookings mb 
            JOIN miller_stock ms ON mb.stock_id = ms.id 
            WHERE ms.miller_id=%s AND mb.status='pending'
        """, (miller_id,))
        m_orders = cur.fetchone()[0]
        
        # 2. Pending QC (Verification OR Final Hisab upload)
        cur.execute("""
            SELECT count(*) FROM loading_invoices li
            JOIN miller_bookings mb ON li.booking_id = mb.id
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE ms.miller_id=%s 
              AND (li.qc_status IS NULL OR li.qc_status != 'verified' OR li.final_invoice_file IS NULL)
        """, (miller_id,))
        m_qc = cur.fetchone()[0]
        
        # 3. Pending Payments
        cur.execute("""
            SELECT count(*) FROM loading_invoices li
            JOIN miller_bookings mb ON li.booking_id = mb.id
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE ms.miller_id=%s 
              AND li.final_invoice_file IS NOT NULL 
              AND (li.payment_status IS NULL OR li.payment_status != 'paid')
        """, (miller_id,))
        m_pay = cur.fetchone()[0]
        
        # 4. Active (Approved) Orders - Strictly those in loading process (not yet loaded/completed)
        cur.execute("""
            SELECT count(*) FROM miller_bookings mb
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE ms.miller_id=%s 
              AND mb.status='approved' 
              AND (mb.loading_status IS NULL OR mb.loading_status IN ('pending', 'partial', 'active'))
        """, (miller_id,))
        m_active = cur.fetchone()[0]
        
        counts['miller_pending_orders'] = m_orders
        counts['miller_pending_qc'] = m_qc
        counts['miller_pending_payments'] = m_pay
        counts['miller_active_orders'] = m_active
        
        # Total counts
        counts['miller_total_pending'] = m_orders + m_qc + m_pay + m_active
        
        # --- BUYER METRICS ---
        # 1. Pending Requests
        cur.execute("SELECT count(*) FROM miller_bookings WHERE buyer_id=%s AND status='pending'", (buyer_id,))
        b_req = cur.fetchone()[0]
        
        # 2. Active Orders (Approved but not yet loaded/completed)
        cur.execute("""
            SELECT count(*) FROM miller_bookings 
            WHERE buyer_id=%s 
              AND status='approved' 
              AND (loading_status IS NULL OR loading_status IN ('pending', 'partial', 'active'))
        """, (buyer_id,))
        b_active = cur.fetchone()[0]
        
        counts['buyer_pending_requests'] = b_req
        counts['buyer_active_orders'] = b_active
        counts['buyer_total_pending'] = b_req + b_active

        # --- MILLER PROFILE (name + address for hero sections) ---
        if session.get("role") == "miller":
            cur.execute("SELECT mill_name, address FROM miller_profiles WHERE miller_id=%s", (miller_id,))
            mp = cur.fetchone()
            if mp:
                counts['miller_name'] = mp[0] or session.get("name", "Miller Login")
                counts['miller_address'] = mp[1]
            else:
                counts['miller_name'] = session.get("name", "Miller Login")
                counts['miller_address'] = None

        # --- BUYER PROFILE (name + address for hero sections) ---
        if session.get("role") == "buyer":
            cur.execute("SELECT shop_name, address FROM buyer_profiles WHERE buyer_id=%s", (buyer_id,))
            bp = cur.fetchone()
            if bp:
                counts['buyer_name'] = bp[0] or session.get("name", "Buyer")
                counts['buyer_address'] = bp[1]
            else:
                counts['buyer_name'] = session.get("name", "Buyer")
                counts['buyer_address'] = None

        con.close()
    except Exception as e:
        logger.error(f"Error in context processor: {e}")
        return dict()
        
    return counts
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

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

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
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("users",))
    cols = [c[0] for c in cur.fetchall()]

    if "status" not in cols:
        cur.execute(
            "ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'pending'"
        )

    con.commit()
    con.close()


def upgrade_password_resets_table():
    """Create password reset token table."""
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.commit()
    con.close()




def upgrade_miller_stock_auto_approve():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_stock",))
    cols = [c[0] for c in cur.fetchall()]

    if "auto_approve" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN auto_approve INTEGER DEFAULT 0
        """)

    if "auto_approve_min_qty" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN auto_approve_min_qty INTEGER DEFAULT 0
        """)

    con.commit()
    con.close()



def upgrade_miller_booking_truck_status():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

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
            ADD COLUMN loaded_at TIMESTAMP
        """)

    con.commit()
    con.close()

def upgrade_buyer_profile_table():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS buyer_profiles (
        id SERIAL PRIMARY KEY,
        buyer_id INTEGER UNIQUE,
        shop_name TEXT,
        phone TEXT,
        address TEXT,
        document TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Ensure new columns exist for richer trader profile
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("buyer_profiles",))
    cols = [c[0] for c in cur.fetchall()]
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


def upgrade_miller_booking_bill():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

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

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

    if "qc_weight" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_weight INTEGER")
    if "qc_moisture" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_moisture REAL")
    if "qc_remarks" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_remarks TEXT")
    if "qc_status" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_status TEXT DEFAULT 'pending'")
    if "qc_at" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN qc_at TIMESTAMP")

    con.commit()
    con.close()



def upgrade_miller_booking_order_id():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

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
                SET order_id=%s
                WHERE id=%s
            """, (order_id, booking[0]))

    con.commit()
    con.close()
def upgrade_miller_payment_fields():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

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
            ADD COLUMN payment_at TIMESTAMP
        """)

    con.commit()
    con.close()
def upgrade_payments_table():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        booking_id INTEGER,
        miller_id INTEGER,
        buyer_id INTEGER,
        amount INTEGER,
        status TEXT DEFAULT 'pending',
        paid_at TIMESTAMP,
        invoice_file TEXT
    )
    """)

    con.commit()
    con.close()
def upgrade_miller_stock_reserved_qty():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_stock",))
    cols = [c[0] for c in cur.fetchall()]

    if "reserved_qty" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN reserved_qty INTEGER DEFAULT 0
        """)

    con.commit()
    con.close()



def upgrade_miller_stock_note():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_stock",))
    cols = [c[0] for c in cur.fetchall()]

    if "note" not in cols:
        cur.execute("""
            ALTER TABLE miller_stock
            ADD COLUMN note TEXT
        """)

    con.commit()
    con.close()

def upgrade_miller_stock_new_fields():
    """Add weight_deduction, payment_duration, extra_condition columns to miller_stock."""
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_stock",))
    cols = [c[0] for c in cur.fetchall()]

    new_cols = [
        ("weight_deduction", "TEXT"),
        ("payment_duration", "TEXT"),
        ("extra_condition", "TEXT")
    ]

    for col_name, col_type in new_cols:
        if col_name not in cols:
            cur.execute(f"ALTER TABLE miller_stock ADD COLUMN {col_name} {col_type}")
            print(f"✅ Added {col_name} to miller_stock")

    con.commit()
    con.close()



def upgrade_miller_booking_price():
    """Add price column to miller_bookings and backfill."""
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", ("miller_bookings",))
    cols = [c[0] for c in cur.fetchall()]

    if "price" not in cols:
        cur.execute("ALTER TABLE miller_bookings ADD COLUMN price REAL")
        
        # Backfill existing bookings with current stock price
        cur.execute("""
            UPDATE miller_bookings
            SET price = (
                SELECT price FROM miller_stock 
                WHERE miller_stock.id = miller_bookings.stock_id
            )
            WHERE price IS NULL
        """)

    con.commit()
    con.close()



def generate_next_order_id():
    """Generate next order ID in format S10001, S10002, etc."""
    con = get_db()
    cur = con.cursor()
    
    # Get the highest order number
    cur.execute("""
        SELECT order_id FROM miller_bookings 
        WHERE order_id IS NOT NULL AND order_id LIKE 'S%'
        ORDER BY CAST(SUBSTRING(order_id, 2) AS INTEGER) DESC
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


def upgrade_admin_logs_table():
    """Create admin_logs table to track admin actions."""
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id SERIAL PRIMARY KEY,
            admin_id INTEGER,
            action VARCHAR(50),
            target_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    con.commit()
    con.close()
    print("✅ Verified admin_logs table.")

def upgrade_admin_logs_extended():
    """Add role column to admin_logs if not exists."""
    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("ALTER TABLE admin_logs ADD COLUMN role VARCHAR(20)")
        print("✅ Added role column to admin_logs.")
    except Exception:
        con.rollback() # Column likely exists
    con.commit()
    con.close()


def run_migrations():
    """Run all database migrations in a single connection to speed up startup."""
    try:
        print("🔄 Starting database migrations...")
        
        # Core Initialization
        init_db()
        upgrade_db()
        upgrade_users_table()
        upgrade_password_resets_table()
        upgrade_admin_logs_table()
        upgrade_admin_logs_extended()
        
        # Features
        upgrade_partial_loading()
        upgrade_staff_system()
        upgrade_loading_invoices()
        upgrade_loading_invoices_debit_note()
        upgrade_loading_invoices_extended_qc()
        
        # Miller Stock
        upgrade_miller_stock_status()
        upgrade_miller_stock_auto_approve()
        upgrade_miller_stock_reserved_qty()
        upgrade_miller_stock_note()
        upgrade_miller_stock_new_fields()
        
        # Buyer Profile
        upgrade_buyer_profile_table()
        
        # Miller Bookings
        upgrade_miller_booking_truck_status()
        upgrade_miller_booking_bill()
        upgrade_miller_booking_qc()
        upgrade_miller_booking_order_id()
        upgrade_miller_booking_price()
        upgrade_miller_payment_fields()
        
        # Payments
        upgrade_payments_table()
        
        # Miller Profile & Address Schema (Custom Logic)
        con = get_db()
        cur = con.cursor()

        # 1. Miller Profile Upgrades
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='miller_profiles'")
        miller_cols = [c[0] for c in cur.fetchall()]
        
        # List of all miller profile columns to ensure exist
        miller_fields = [
            ("mill_name", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"), 
            ("document", "TEXT"),
            ("owner_phone", "TEXT"),
            ("accountant_phone", "TEXT"),
            ("staff_phone", "TEXT"),
            ("gst_doc", "TEXT"),
            ("mandi_doc", "TEXT"),
            ("other_doc", "TEXT"),
            ("owner_name", "TEXT"),
            ("gst_number", "TEXT"),
            ("mandi_number", "TEXT")
        ]
        
        for col, col_type in miller_fields:
            if col not in miller_cols:
                cur.execute(f"ALTER TABLE miller_profiles ADD COLUMN {col} {col_type}")
                print(f"✅ Added {col} to miller_profiles")

        # 2. Address Schema Upgrades
        tables = ["miller_profiles", "buyer_profiles"]
        address_cols = [
            ("pincode", "TEXT"),
            ("house_no", "TEXT"),
            ("area", "TEXT"),
            ("locality", "TEXT"),
            ("landmark", "TEXT"),
            ("city", "TEXT"),
            ("state", "TEXT"),
            ("country", "TEXT DEFAULT 'India'"),
            ("gst_number", "TEXT"),
            ("mandi_number", "TEXT")
        ]

        for table in tables:
            # Re-fetch columns for each table as we iterate
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'")
            existing_cols = [c[0] for c in cur.fetchall()]

            for col_name, col_type in address_cols:
                if col_name not in existing_cols:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                    print(f"✅ Added {col_name} to {table}")

        con.commit()
        con.close()
        print("✅ Database migrations completed successfully.")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        # We catch the error to prevent app crash, logging it for review




# ---------------- AUTH ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    # Show a one-time success message after password reset
    if request.method == "GET" and request.args.get("reset") == "1":
        return render_template(
            "login.html",
            success="✅ Password updated successfully. Please login with your new password."
        )

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template(
                "login.html",
                error="Please enter email and password"
            )

        con = get_db()
        try:
            cur = con.cursor()
            print(f"DEBUG: Login attempt for email: {email}")
            cur.execute(
                "SELECT id, name, email, password, role, status, is_staff, parent_miller_id FROM users WHERE lower(email)=%s",
                (email.lower(),)
            )
            user = cur.fetchone()
            if not user:
                print(f"DEBUG: User not found: {email}")
            else:
                print(f"DEBUG: User found: {user[2]}, Role: {user[4]}, Status: {user[5]}")
        finally:
            con.close()

        if not user:
            return render_template(
                "login.html",
                error="Invalid credentials"
            )

        # Password check: support both hashed (werkzeug) and legacy plaintext
        stored_pw = user[3]
        if stored_pw and (stored_pw.startswith("pbkdf2:") or stored_pw.startswith("scrypt:")):
            pw_ok = check_password_hash(stored_pw, password)
        else:
            pw_ok = (stored_pw == password)

        if not pw_ok:
            print(f"DEBUG: Password mismatch for user: {email}")
            return render_template(
                "login.html",
                error="Invalid credentials"
            )

        if user[5] != "approved":
            print(f"DEBUG: User {email} is not approved. Status: {user[5]}")
            return render_template(
                "login.html",
                error="⛔ Your account is not approved by admin yet"
            )

        session["user_id"] = user[0] 
        session["role"] = user[4]
        session["is_staff"] = user[6] if user[6] else 0
        session["parent_miller_id"] = user[7] if user[7] else None

        if user[4] == "farmer":
            log_activity("User Login", user[0], f"Role: {user[4]}", user_id=user[0], role=user[4])
            return redirect("/my_commodity")
        elif user[4] == "buyer":
            log_activity("User Login", user[0], f"Role: {user[4]}", user_id=user[0], role=user[4])
            return redirect("/market")
        elif user[4] == "miller":
            log_activity("User Login", user[0], f"Role: {user[4]}", user_id=user[0], role=user[4])
            return redirect("/miller")
        else:
            log_activity("User Login", user[0], "Role: Admin", user_id=user[0], role="admin")
            return redirect("/admin")

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Send OTP via SMS (to phone saved in user profile) for password reset."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            return render_template("forgot_password.html", error="Please enter your email")

        con = get_db()
        cur = con.cursor()
        cur.execute(
            "SELECT id, role, COALESCE(is_staff,0), parent_miller_id FROM users WHERE lower(email)=%s",
            (email,),
        )
        row = cur.fetchone()

        # Avoid leaking whether the email exists
        generic_success = "If the account exists, you will receive an OTP on SMS."

        if not row:
            con.close()
            return render_template(
                "forgot_password.html",
                success=generic_success,
                email=email,
                show_otp_form=True,
            )

        user_id, role, is_staff, parent_miller_id = row

        phone = get_phone_for_password_reset(
            user_id=user_id,
            role=role,
            is_staff=is_staff,
            parent_miller_id=parent_miller_id,
        )

        if not phone:
            con.close()
            return render_template(
                "forgot_password.html",
                error="Phone number not found in your profile. Please contact admin.",
                email=email,
            )

        # Invalidate previous active OTPs
        cur.execute("UPDATE password_resets SET used=1 WHERE user_id=%s AND used=0", (user_id,))

        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = hashlib.sha256(otp.encode("utf-8")).hexdigest()
        expires_at = (datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

        # Reuse token_hash column to store OTP hash (no schema change)
        cur.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at, used) VALUES (%s,%s,%s,0)",
            (user_id, otp_hash, expires_at),
        )
        con.commit()
        con.close()

        sms_text = f"Saarna Canvessars OTP for password reset: {otp}. Valid for 10 minutes."
        sent = send_sms(phone, sms_text)
        if not sent:
            return render_template(
                "forgot_password.html",
                error="Could not send OTP SMS right now. Please try again later.",
                email=email,
            )

        return render_template(
            "forgot_password.html",
            success=generic_success,
            email=email,
            show_otp_form=True,
        )

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Reset password using OTP (sent via SMS)."""
    prefill_email = (request.args.get("email") or "").strip().lower()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        otp = (request.form.get("otp") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not email or not otp:
            return render_template(
                "reset_password.html",
                error="Please enter email and OTP.",
                email=email,
            )

        if not password or not confirm:
            return render_template(
                "reset_password.html",
                error="Please enter and confirm your new password.",
                email=email,
            )

        if password != confirm:
            return render_template(
                "reset_password.html",
                error="Passwords do not match.",
                email=email,
            )

        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT id FROM users WHERE lower(email)=%s", (email,))
        u = cur.fetchone()
        if not u:
            con.close()
            return render_template(
                "reset_password.html",
                error="Invalid email or OTP.",
                email=email,
            )

        user_id = u[0]

        cur.execute(
            """
            SELECT id, token_hash
            FROM password_resets
            WHERE user_id=%s AND used=0 AND expires_at > CURRENT_TIMESTAMP
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            con.close()
            return render_template(
                "reset_password.html",
                error="OTP is invalid or expired. Please request a new OTP.",
                email=email,
            )

        reset_id, otp_hash = row
        entered_hash = hashlib.sha256(otp.encode("utf-8")).hexdigest()

        if entered_hash != otp_hash:
            con.close()
            return render_template(
                "reset_password.html",
                error="Invalid email or OTP.",
                email=email,
            )

        cur.execute("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(password), user_id))
        cur.execute("UPDATE password_resets SET used=1 WHERE id=%s", (reset_id,))
        con.commit()
        con.close()

        return redirect("/?reset=1")

    return render_template("reset_password.html", email=prefill_email)


# Backward-compatible old link route (no longer used):
@app.route("/reset-password/<token>")
def reset_password_link_fallback(token):
    return redirect("/reset-password")


@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        role = request.form["role"]
        
        # Phone number
        phone = request.form.get("phone", "").strip()

        # Address fields
        pincode = request.form.get("pincode", "").strip()
        house_no = request.form.get("house_no", "").strip()
        area = request.form.get("area", "").strip()
        locality = request.form.get("locality", "").strip()
        landmark = request.form.get("landmark", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        country = request.form.get("country", "India").strip()
        
        # Compliance fields
        gst_number = request.form.get("gst_number", "").strip()
        mandi_number = request.form.get("mandi_number", "").strip()

        # Construct legacy address string for backward compatibility
        # Format: House No, Area, Locality (if any), Landmark (if any), City, State - Pincode
        address_parts = [house_no, area]
        if locality: address_parts.append(locality)
        if landmark: address_parts.append(landmark)
        address_parts.append(city)
        address_parts.append(state)
        address_parts.append(f"- {pincode}")
        
        full_address = ", ".join(filter(None, address_parts))

        # firm_name is now same as name
        firm_name = name
        
        # File uploads
        gst_doc = request.files.get("gst_doc")
        mandi_doc = request.files.get("mandi_doc")
        other_doc = request.files.get("other_doc")
        
        con = get_db()
        cur = con.cursor()
        
        try:
            cur.execute("""
            INSERT INTO users (name,email,password,role)
            VALUES (%s,%s,%s,%s)
            RETURNING id
    """, (name, email, generate_password_hash(password), role))
            
            user_id = cur.fetchone()[0]  # was lastrowid
            
            # Helper to save file
            def save_doc(file_obj, prefix):
                if file_obj and file_obj.filename:
                    filename = secure_filename(file_obj.filename)
                    base, ext = os.path.splitext(filename)
                    new_filename = f"{prefix}_{user_id}_{base}{ext}"
                    file_obj.save(os.path.join(app.config["PROFILE_FOLDER"], new_filename))
                    return new_filename
                return None

            gst_filename = save_doc(gst_doc, "gst")
            mandi_filename = save_doc(mandi_doc, "mandi")
            other_filename = save_doc(other_doc, "other")
            
            if role == "miller":
                cur.execute("""
                    INSERT INTO miller_profiles
                    (miller_id, mill_name, owner_name, owner_phone, address, gst_doc, mandi_doc, other_doc,
                     pincode, house_no, area, locality, landmark, city, state, country, gst_number, mandi_number)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s, %s, %s)
                """, (user_id, firm_name, name, phone, full_address, gst_filename, mandi_filename, other_filename,
                      pincode, house_no, area, locality, landmark, city, state, country, gst_number, mandi_number))
                
            elif role == "buyer":
                # For buyer, map firm_name -> shop_name, mandi_doc -> license_doc
                cur.execute("""
                    INSERT INTO buyer_profiles
                    (buyer_id, shop_name, owner_name, phone, address, gst_doc, license_doc, other_doc,
                     pincode, house_no, area, locality, landmark, city, state, country, gst_number, mandi_number)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s, %s, %s)
                """, (user_id, firm_name, name, phone, full_address, gst_filename, mandi_filename, other_filename,
                      pincode, house_no, area, locality, landmark, city, state, country, gst_number, mandi_number))
                
            con.commit()
            con.close()
            # We can't use session here as user is not logged in yet, so pass explicit user_id and role
            log_activity("User Registration", user_id, f"Name: {name}, Role: {role}", user_id=user_id, role=role)
            return redirect("/")
            
        except Exception as e:
            con.rollback()
            con.close()
            logger.error(f"Registration Error: {e}")
            return render_template("register.html", error="Registration failed. Email might be taken.")
    return render_template("register.html")

# Run migrations at startup
try:
    from app import run_migrations
    run_migrations()
except ImportError:
    # If this script is run as 'app', it will fail to import from itself
    run_migrations()
except Exception as e:
    print(f"Startup migration alert: {e}")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/profile")
def profile():
    if not session.get("user_id"):
        return redirect("/")
    
    user_id = session.get("user_id")
    con = get_db()
    cur = con.cursor()
    
    cur.execute("SELECT name, email, role FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    con.close()
    
    if not user:
        return redirect("/")
        
    return render_template("profile.html", user=user)

@app.route("/switch_role/<target_role>")
def switch_role(target_role):
    if not session.get("user_id"):
        return redirect("/")
    
    current_role = session.get("role")
    
    # Security: Only allow switching between miller and buyer
    if current_role in ["miller", "buyer"] and target_role in ["miller", "buyer"]:
        session["role"] = target_role

        # Auto-create target profile if missing, copying from existing profile
        user_id = session.get("user_id")
        try:
            con = get_db()
            cur = con.cursor()

            if target_role == "buyer":
                cur.execute("SELECT id FROM buyer_profiles WHERE buyer_id=%s", (user_id,))
                if not cur.fetchone():
                    # Copy from miller profile if available
                    cur.execute("SELECT mill_name, owner_name, owner_phone, address, city, state FROM miller_profiles WHERE miller_id=%s", (user_id,))
                    mp = cur.fetchone()
                    if mp:
                        cur.execute("""INSERT INTO buyer_profiles (buyer_id, shop_name, owner_name, phone, address, city, state)
                                      VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                   (user_id, mp[0], mp[1], mp[2], mp[3], mp[4], mp[5]))
                    else:
                        cur.execute("SELECT name FROM users WHERE id=%s", (user_id,))
                        u = cur.fetchone()
                        uname = u[0] if u else ''
                        cur.execute("""INSERT INTO buyer_profiles (buyer_id, shop_name, owner_name)
                                      VALUES (%s,%s,%s)""",
                                   (user_id, uname, uname))
                    con.commit()

            elif target_role == "miller":
                cur.execute("SELECT id FROM miller_profiles WHERE miller_id=%s", (user_id,))
                if not cur.fetchone():
                    # Copy from buyer profile if available
                    cur.execute("SELECT shop_name, owner_name, phone, address, city, state FROM buyer_profiles WHERE buyer_id=%s", (user_id,))
                    bp = cur.fetchone()
                    if bp:
                        cur.execute("""INSERT INTO miller_profiles (miller_id, mill_name, owner_name, owner_phone, address, city, state)
                                      VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                   (user_id, bp[0], bp[1], bp[2], bp[3], bp[4], bp[5]))
                    else:
                        cur.execute("SELECT name FROM users WHERE id=%s", (user_id,))
                        u = cur.fetchone()
                        uname = u[0] if u else ''
                        cur.execute("""INSERT INTO miller_profiles (miller_id, mill_name, owner_name)
                                      VALUES (%s,%s,%s)""",
                                   (user_id, uname, uname))
                    con.commit()

            con.close()
        except Exception as e:
            logger.error(f"Error auto-creating profile on role switch: {e}")

        if target_role == "buyer":
            return redirect("/market")
        elif target_role == "miller":
            return redirect("/miller")
            
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
        VALUES (%s,%s,%s,%s,%s,%s,%s)
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
    cur.execute("SELECT * FROM crops WHERE farmer_id=%s", (get_effective_user_id(),))
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
            (miller_id, crop, quantity, price, condition, bag_type, deduction, note, auto_approve_min_qty)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            miller_id,
            request.form["crop"],
            100000, # Default quantity since input is removed
            request.form["price"],
            request.form["condition"],
            request.form["bag_type"],
            request.form["deduction"],
            request.form.get("note", ""),
            request.form.get("auto_approve_min_qty", 0)
        ))
        new_stock_id = cur.fetchone()[0]
        # Ensure the stock is visible in buyer market (market filters status='open')
        cur.execute("UPDATE miller_stock SET status='open' WHERE id=%s", (new_stock_id,))
        con.commit()
        
        # 📱 Send SMS to all buyers about new stock
        crop = request.form["crop"]
        price = request.form["price"]
        note = request.form.get("note", "")
        buyer_phones = get_all_buyer_phones()
        message = f"🆕 New stock available! {crop} - Price: ₹{price}/unit. {note} Check the market for details."
        for phone in buyer_phones:
            send_sms(phone, message)
        
        log_activity("Stock Posted", new_stock_id, f"Crop: {crop}, Qty: 100000, Price: {price}, Note: {note}")
        return redirect(url_for('miller_dashboard'))

    # ✅ LIVE STOCKS
    cur.execute("""
    SELECT 
        id, miller_id, crop, quantity, price, condition, bag_type, deduction, created_at, status, note, reserved_qty, auto_approve_min_qty,
        weight_deduction, payment_duration, extra_condition
    FROM miller_stock
    WHERE miller_id=%s
    ORDER BY created_at DESC
""", (miller_id,))
    stocks = cur.fetchall()

    # ✅ BUYER BOOKINGS
    cur.execute("""
SELECT
    mb.id,              -- 0 booking_id
    COALESCE(bp.shop_name, u.name),  -- 1 buyer_name (from profile)
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

    COALESCE(p.status,'pending')     AS payment_status,  -- 16
    p.invoice_file                 AS final_invoice,   -- 17
    p.paid_at                      AS payment_at,      -- 18
    bp.address,                                        -- 19 (NEW)
    bp.city,                                           -- 20 (NEW)
    bp.state,                                          -- 21 (NEW)
    mb.deadline_at                                     -- 22 (NEW)

FROM miller_bookings mb
JOIN users u ON mb.buyer_id = u.id
JOIN miller_stock ms ON mb.stock_id = ms.id
LEFT JOIN payments p ON p.booking_id = mb.id
LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
WHERE ms.miller_id=%s
ORDER BY mb.created_at DESC
""", (miller_id,))
    bookings = cur.fetchall()

    # ✅ PRE-FILTER BOOKINGS FOR TABS
    pending_list = [b for b in bookings if b[4] == 'pending']
    approved_loading = [b for b in bookings if b[4] == 'approved' and b[8] != 'closed']
    final_invoice_uploaded = [b for b in bookings if b[17] and b[16] != 'paid'] # Using COALESCE(p.status) and p.invoice_file indices
    payment_completed = [b for b in bookings if b[16] == 'paid']

    # ✅ FETCH DEDUCTION OPTIONS
    cur.execute("SELECT id, text FROM miller_deduction_options WHERE miller_id=%s ORDER BY created_at DESC", (miller_id,))
    deduction_options = cur.fetchall()

    # 🔹 FETCH PER-TRUCK LOADING INVOICES WITH QC DATA AND FINAL INVOICE
    cur.execute("""
    SELECT li.id, li.booking_id, li.loaded_qty, li.invoice_file, li.truck_number, li.created_at,
           li.qc_weight, li.qc_moisture, li.qc_remarks, li.qc_status, li.qc_at,
           li.final_invoice_file, li.payment_status, li.payment_at, li.qc_freight
    FROM loading_invoices li
    JOIN miller_bookings mb ON li.booking_id = mb.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    WHERE ms.miller_id = %s
    ORDER BY li.created_at ASC
""", (miller_id,))
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
        "payment_at": r[13],
        "qc_freight": r[14]
    })

    # 🔹 COUNT PENDING PAYMENTS (For Dashboard Stats)
    cur.execute("""
        SELECT COUNT(*)
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE ms.miller_id = %s
          AND li.final_invoice_file IS NOT NULL
          AND (li.payment_status IS NULL OR li.payment_status != 'paid')
    """, (miller_id,))
    total_pending_payments = cur.fetchone()[0] or 0

    # 🔹 CALCULATE DASHBOARD COUNTS
    active_stocks_count = len(stocks)
    pending_bookings_count = len(pending_list)
    approved_bookings_count = len(approved_loading)
    # QC Pending: Verification OR Final Hisab upload
    qc_pending_count = sum(1 for r in rows if (r[9] != 'verified' or r[11] is None))

    con.close()

    # Fetch Miller Address & Name
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT address, mill_name FROM miller_profiles WHERE miller_id=%s", (miller_id,))
    addr_row = cur.fetchone()
    con.close()
    miller_address = addr_row[0] if addr_row else None
    miller_name = addr_row[1] if addr_row else "Miller Login"

    return render_template(
    "miller.html",
    stocks=stocks,
    bookings=bookings,
    pending_list=pending_list,
    approved_loading=approved_loading,
    final_invoice_uploaded=final_invoice_uploaded,
    payment_completed=payment_completed,
    invoices_map=invoices_map,
    deduction_options=deduction_options,
    total_pending_payments=total_pending_payments,
    active_stocks_count=active_stocks_count,
    pending_bookings_count=pending_bookings_count,
    approved_bookings_count=approved_bookings_count,
    qc_pending_count=qc_pending_count,
    miller_address=miller_address,
    miller_name=miller_name
)

@app.route("/miller/delete_stock/<int:id>", methods=["POST"])
def delete_miller_stock(id):
    if session.get("role") != "miller":
        return redirect("/")
    
    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()
    
    # Check if stock exists and belongs to miller
    cur.execute("SELECT id FROM miller_stock WHERE id=%s AND miller_id=%s", (id, miller_id))
    if not cur.fetchone():
        con.close()
        return redirect("/miller")
        
    # Check if there are active bookings
    cur.execute("SELECT count(*) FROM miller_bookings WHERE stock_id=%s AND status IN ('pending', 'approved')", (id,))
    active_count = cur.fetchone()[0]
    
    if active_count > 0:
        flash("Cannot delete stock with active bookings!", "error")
    else:
        cur.execute("DELETE FROM miller_stock WHERE id=%s", (id,))
        con.commit()
        flash("Stock deleted successfully.", "success")
        
    con.close()
    return redirect("/miller")

@app.route("/miller/add_deduction_option", methods=["POST"])
def miller_add_deduction_option():
    if session.get("role") != "miller":
        return redirect("/")
    
    text = request.form.get("text")
    if text:
        con = get_db()
        cur = con.cursor()
        cur.execute("INSERT INTO miller_deduction_options (miller_id, text) VALUES (%s,%s)", 
                   (get_effective_user_id(), text))
        con.commit()
        con.close()
    
    return redirect("/miller#post-stock") # Return to post stock section

@app.route("/miller/delete_deduction_option/<int:id>", methods=["POST"])
def miller_delete_deduction_option(id):
    if session.get("role") != "miller":
        return redirect("/")
        
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM miller_deduction_options WHERE id=%s AND miller_id=%s", 
               (id, get_effective_user_id()))
    con.commit()
    con.close()
    
    return redirect("/miller#post-stock")

@app.route("/miller/profile", methods=["GET", "POST"])
def miller_profile_page():
    if session.get("role") != "miller":
        return redirect("/")
    
    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()
    
    if request.method == "POST":
        owner_name = request.form.get("owner_name")
        mill_name = request.form.get("mill_name")
        owner_phone = request.form.get("owner_phone")
        accountant_phone = request.form.get("accountant_phone")
        staff_phone = request.form.get("staff_phone")
        address = request.form.get("address")
        city = request.form.get("city")
        state = request.form.get("state")
        
        # New compliance fields
        gst_number = request.form.get("gst_number")
        mandi_number = request.form.get("mandi_number")
        
        # Handle file uploads
        gst_doc = request.files.get("gst_doc")
        mandi_doc = request.files.get("mandi_doc")
        other_doc = request.files.get("other_doc")
        
        gst_filename = None
        mandi_filename = None
        other_filename = None

        if gst_doc and gst_doc.filename:
            gst_filename = secure_filename(gst_doc.filename)
            gst_doc.save(os.path.join(app.config["PROFILE_FOLDER"], gst_filename))
            
        if mandi_doc and mandi_doc.filename:
            mandi_filename = secure_filename(mandi_doc.filename)
            mandi_doc.save(os.path.join(app.config["PROFILE_FOLDER"], mandi_filename))
            
        if other_doc and other_doc.filename:
            other_filename = secure_filename(other_doc.filename)
            other_doc.save(os.path.join(app.config["PROFILE_FOLDER"], other_filename))
        
        # Check if profile exists
        cur.execute("SELECT id FROM miller_profiles WHERE miller_id=%s", (miller_id,))
        exists = cur.fetchone()
        
        if exists:
            # Update
            query = """
                UPDATE miller_profiles 
                SET owner_name=%s, mill_name=%s, owner_phone=%s, accountant_phone=%s, staff_phone=%s, address=%s, city=%s, state=%s,
                    gst_number=%s, mandi_number=%s
            """
            params = [owner_name, mill_name, owner_phone, accountant_phone, staff_phone, address, city, state, gst_number, mandi_number]
            
            if gst_filename:
                query += ", gst_doc=%s"
                params.append(gst_filename)
            if mandi_filename:
                query += ", document=%s" # Note: column is 'document'
                params.append(mandi_filename)
            if other_filename:
                query += ", other_doc=%s"
                params.append(other_filename)
                
            query += " WHERE miller_id=%s"
            params.append(miller_id)
            
            cur.execute(query, tuple(params))
        else:
            # Insert
            cur.execute("""
                INSERT INTO miller_profiles 
                (miller_id, owner_name, mill_name, owner_phone, accountant_phone, staff_phone, address, city, state, gst_doc, document, other_doc, gst_number, mandi_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (miller_id, owner_name, mill_name, owner_phone, accountant_phone, staff_phone, address, city, state, gst_filename, mandi_filename, other_filename, gst_number, mandi_number))
            
        # Sync name to users table so it reflects everywhere
        cur.execute("UPDATE users SET name=%s WHERE id=%s", (mill_name, miller_id))
        con.commit()
        flash("Profile updated successfully", "success")
        log_activity("Profile Updated", miller_id, "Miller Profile Updated")
        con.close()
        return redirect("/miller/profile")

    # Fetch Profile
    # Explicitly select columns to match template indices + city/state
    # 0:id, 1:miller_id, 2:mill_name, 3:owner_name, 4:address, 5:city, 6:state, 
    # 7:owner_phone, 8:accountant_phone, 9:staff_phone, 10:gst_doc, 11:mandi_doc (document), 12:other_doc
    cur.execute("""
        SELECT id, miller_id, mill_name, owner_name, address, city, state, 
               owner_phone, accountant_phone, staff_phone, gst_doc, document, other_doc,
               gst_number, mandi_number
        FROM miller_profiles 
        WHERE miller_id=%s
    """, (miller_id,))
    profile = cur.fetchone()
    con.close()
    
    return render_template("miller_profile.html", profile=profile)

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
            COALESCE(bp.shop_name, u.name), -- 1 buyer (from profile)
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

            COALESCE(p.status,'pending') AS payment_status, -- 16
            p.invoice_file,                                -- 17
            p.paid_at                                     -- 18
        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
        WHERE
            ms.miller_id = %s
            AND mb.status = 'approved'
        ORDER BY mb.created_at DESC
    """, (miller_id,))

    approved = cur.fetchall()

    # ✅ Fetch per-truck invoices (WITH QC AND FINAL INVOICE)
    cur.execute("""
        SELECT
            id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
            qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
            final_invoice_file, payment_status, payment_at, qc_freight,
            qc_broken, qc_karda, qc_oil, qc_mitti, qc_ssa, qc_claim
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
            "payment_at": r[13],
            "qc_freight": r[14],
            "qc_broken": r[15],
            "qc_karda": r[16],
            "qc_oil": r[17],
            "qc_mitti": r[18],
            "qc_ssa": r[19],
            "qc_claim": r[20]
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

        COALESCE(p.status,'pending'),
        p.invoice_file,
        p.paid_at
    FROM miller_bookings mb
    JOIN users u ON mb.buyer_id = u.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    LEFT JOIN payments p ON p.booking_id = mb.id
    WHERE ms.miller_id=%s
    ORDER BY mb.created_at DESC
    """, (miller_id,))

    bookings = cur.fetchall()

    # 2️⃣ Fetch per-truck invoices (WITH QC AND FINAL INVOICE)
    cur.execute("""
    SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
           qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
           final_invoice_file, payment_status, payment_at, qc_freight,
           qc_broken, qc_karda, qc_oil, qc_mitti, qc_ssa, qc_claim
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
            "payment_at": r[13],
            "qc_freight": r[14],
            "qc_broken": r[15],
            "qc_karda": r[16],
            "qc_oil": r[17],
            "qc_mitti": r[18],
            "qc_ssa": r[19],
            "qc_claim": r[20]
        })

    # 3️⃣ FILTER BOOKINGS WITH PENDING QC ACTIONS (Verification or Hisab)
    completed_loading_qc = []
    
    for b in bookings:
        # Show in QC list if there are any trucks that haven't been verified OR haven't had a hisab uploaded
        listing_invoices = invoices_map.get(b[0], [])
        has_pending_truck_action = any(
            inv['qc_status'] != 'verified' or not inv['final_invoice_file'] 
            for inv in listing_invoices
        )
        
        if has_pending_truck_action:
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
            COALESCE(bp.shop_name, u.name), -- 1 buyer_name (from profile)
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

            COALESCE(p.status,'pending') AS payment_status, -- 16
            p.invoice_file                 AS final_invoice, -- 17
            p.paid_at                      AS payment_at,    -- 18
            COALESCE(mb.price, ms.price)     AS price          -- 19

        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
        WHERE
            ms.miller_id = %s
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
            li.payment_at,
            li.qc_freight,
            li.qc_broken,
            li.qc_karda,
            li.qc_oil,
            li.qc_mitti,
            li.qc_ssa,
            li.qc_claim
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE ms.miller_id = %s
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
            "payment_at": r[13],
            "qc_freight": r[14],
            "qc_broken": r[15],
            "qc_karda": r[16],
            "qc_oil": r[17],
            "qc_mitti": r[18],
            "qc_ssa": r[19],
            "qc_claim": r[20]
        })

    # Filter: Only show bookings that have AT LEAST ONE TRUCK needing Final Invoice
    # (i.e. qc_verified AND final_invoice_file IS NULL)
    final_bookings = []
    
    for b in all_bookings:
        booking_id = b[0]
        trucks = invoices_map.get(booking_id, [])
        
        # Check if any truck needs an invoice (QC Verified but No Final Invoice)
        # OR if we want to show all trucks for a booking if at least one needs attention.
        # User said: "remove mark payment done button if the final invoice is uploaded... not more show in final hisab"
        
        # So we should only pass trucks that need invoice.
        # But our template iterates bookings then trucks.
        # We'll filter the trucks list for each booking first.
        
        pending_invoice_trucks = [
            t for t in trucks 
            if t['qc_status'] == 'verified' and not t['final_invoice_file']
        ]
        
        # Also include trucks that might be pending QC? Conventionally Final Hisab waits for QC.
        # But if the user says "Final Hisab", it usually implies QC is done.
        # Let's show bookings that have pending_invoice_trucks.
        
        if pending_invoice_trucks:
            # We clone the booking simple object? 
            # No, 'invoices_map' is separate. We can just check if we have relevant trucks.
            # But we should probably ONLY show the relevant trucks in the template?
            # Or show the whole booking but mark trucks? 
            # The prompt says "remove ... not more show in final hisab".
            # So I should Filter the trucks inside the invoices_map for this view?
            # Creating a new map for this view might be safer.
            pass
            
    # Redoing the logic to be cleaner:
    # 1. Create filtered map 'on_final_hisab_map'
    # 2. Only include bookings that have entries in this map.
    
    on_final_hisab_map = {}
    filtered_bookings = []
    
    for b in all_bookings:
        booking_id = b[0]
        orig_trucks = invoices_map.get(booking_id, [])
        
        # Trucks that should appear in Final Hisab:
        # QC Verified AND No Final Invoice
        # (If QC not verified, it's in QC page. If Invoice Uploaded, it's in Pending Payment page)
        
        relevant_trucks = [
            t for t in orig_trucks 
            if t['qc_status'] == 'verified' and not t['final_invoice_file']
        ]
        
        if relevant_trucks:
            on_final_hisab_map[booking_id] = relevant_trucks
            filtered_bookings.append(b)
            
    con.close()

    return render_template(
        "miller_final_hisab.html",
        all_bookings=filtered_bookings,
        invoices_map=on_final_hisab_map
    )

@app.route("/miller/pending-payments")
def miller_pending_payments_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # Fetch all bookings
    cur.execute("""
        SELECT
            mb.id, u.name, ms.crop, mb.quantity, mb.status, mb.reason, mb.decision_at,
            mb.loaded_qty, mb.loading_status, mb.close_reason, mb.order_id,
            mb.qc_weight, mb.qc_moisture, mb.qc_remarks, mb.qc_status, mb.qc_at,
            COALESCE(p.status,'pending'), p.invoice_file, p.paid_at, ms.price
        FROM miller_bookings mb
        JOIN users u ON mb.buyer_id = u.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        WHERE ms.miller_id = %s
        ORDER BY mb.created_at DESC
    """, (miller_id,))

    all_bookings = cur.fetchall()

    # Fetch invoices
    cur.execute("""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at, qc_freight
        FROM loading_invoices
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()

    invoices_map = {}
    for r in rows:
        invoices_map.setdefault(r[1], []).append({
            "id": r[0], "qty": r[2], "file": r[3], "truck_number": r[4], "date": r[5],
            "qc_weight": r[6], "qc_moisture": r[7], "qc_remarks": r[8], "qc_status": r[9] or "pending", "qc_at": r[10],
            "final_invoice_file": r[11], "payment_status": r[12] or "pending", "payment_at": r[13],
            "qc_freight": r[14]
        })

    # FILTER: Trucks that have Final Invoice BUT correspond to Payment Pending
    pending_payment_map = {}
    filtered_bookings = []

    for b in all_bookings:
        booking_id = b[0]
        orig_trucks = invoices_map.get(booking_id, [])
        
        relevant_trucks = [
            t for t in orig_trucks
            if t['final_invoice_file'] and t['payment_status'] != 'paid'
        ]

        if relevant_trucks:
            pending_payment_map[booking_id] = relevant_trucks
            filtered_bookings.append(b)

    con.close()

    return render_template(
        "miller_pending_payments.html",
        all_bookings=filtered_bookings,
        invoices_map=pending_payment_map
    )



@app.route("/miller/post-stock", methods=["GET", "POST"])
def miller_post_stock_page():
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    if request.method == "POST":
        crop = request.form["crop"]
        price = request.form["price"]
        qty = request.form["quantity"] # Get quantity from form!
        # condition = request.form["condition"] 
        condition = "Net Weight" # Default per user request to remove block
        
        # Defaults for removed fields
        bag_type = "Standard" 
        deduction = request.form.get("deduction", "")
        note = ""
        auto_approve_min_qty = 0
        
        duration = request.form.get("duration")
        try:
            duration = int(duration) if duration else None
        except ValueError:
            duration = None

        # New fields
        weight_deduction = request.form.get("weight_deduction", "")
        payment_duration = request.form.get("payment_duration", "")
        extra_condition = request.form.get("extra_condition", "")

        # Create new stock entry
        cur.execute("""
            INSERT INTO miller_stock (miller_id, crop, quantity, price, condition, bag_type, deduction, note, auto_approve_min_qty, duration, weight_deduction, payment_duration, extra_condition)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (miller_id, crop, qty, price, condition, bag_type, deduction, note, auto_approve_min_qty, duration, weight_deduction, payment_duration, extra_condition))
        
        con.commit()
        con.close()
        flash("Stock posted successfully!", "success")
        return redirect("/miller")

    # Fetch deduction options for the form
    cur.execute("SELECT * FROM miller_deduction_options WHERE miller_id=%s ORDER BY created_at DESC", (miller_id,))
    deduction_options = cur.fetchall()
    
    con.close()
    return render_template("post_stock.html", deduction_options=deduction_options)




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
            COALESCE(bp.shop_name, u.name), -- 1 buyer (from profile)
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
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
        WHERE
            ms.miller_id = %s
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

    # 1️⃣ Fetch bookings that have AT LEAST ONE truck marked as paid
    cur.execute("""
        SELECT DISTINCT
            mb.id,              -- 0 booking_id
            COALESCE(bp.shop_name, u.name), -- 1 buyer (from profile)
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

            -- columns 16-18 kept for compatibility but might be null
            'paid' AS payment_status,   -- 16 (forcing 'paid' for display purposes)
            NULL AS final_invoice,      -- 17
            MAX(li.payment_at) AS payment_at, -- 18 (latest payment)
            COALESCE(mb.price, ms.price)  -- 19
            
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON mb.buyer_id = u.id
        JOIN loading_invoices li ON li.booking_id = mb.id
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
        WHERE
            mb.stock_id IN (SELECT id FROM miller_stock WHERE miller_id=%s)
            AND li.payment_status='paid'
        GROUP BY
            mb.id, u.name, bp.shop_name, ms.crop, mb.quantity, mb.status, mb.reason,
            mb.decision_at, mb.loaded_qty, mb.loading_status, mb.close_reason,
            mb.order_id, mb.qc_weight, mb.qc_moisture, mb.qc_remarks,
            mb.qc_status, mb.qc_at, COALESCE(mb.price, ms.price)
        ORDER BY MAX(li.payment_at) DESC
    """, (miller_id,))

    payment_completed = cur.fetchall()

    # 2️⃣ Fetch per-truck invoices for these bookings
    cur.execute("""
        SELECT li.id, li.booking_id, li.loaded_qty, li.invoice_file, li.truck_number, li.created_at,
               li.qc_weight, li.qc_moisture, li.qc_remarks, li.qc_status, li.qc_at,
               li.final_invoice_file, li.payment_status, li.payment_at, li.qc_freight,
               li.qc_broken, li.qc_karda, li.qc_oil, li.qc_mitti, li.qc_ssa, li.qc_claim
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE ms.miller_id = %s AND li.payment_status = 'paid'
        ORDER BY li.payment_at DESC
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
            "payment_at": r[13],
            "qc_freight": r[14],
            "qc_broken": r[15],
            "qc_karda": r[16],
            "qc_oil": r[17],
            "qc_mitti": r[18],
            "qc_ssa": r[19],
            "qc_claim": r[20]
        })

    con.close()

    return render_template(
        "miller_payment_completed.html",
        payment_completed=payment_completed,
        invoices_map=invoices_map
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
        WHERE mb.id=%s AND ms.miller_id=%s AND mb.loading_status IN ('loaded', 'partial_closed')
    """, (booking_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/miller")

    # ✅ Check if payment record exists
    cur.execute("SELECT id FROM payments WHERE booking_id=%s", (booking_id,))
    existing_payment = cur.fetchone()
    
    if existing_payment:
        # Update existing payment record
        cur.execute("""
            UPDATE payments
            SET invoice_file=%s,
                status='pending'
            WHERE booking_id=%s
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
                %s
            FROM miller_bookings mb
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE mb.id=%s
        """, (filename, booking_id))
    
    # 📱 Send SMS to buyer about final invoice
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, mb.loaded_qty, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
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
        WHERE p.booking_id=%s AND ms.miller_id=%s AND p.invoice_file IS NOT NULL
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
        WHERE booking_id=%s
    """, (booking_id,))
    
    # 📱 Send SMS to buyer about payment completion
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, p.amount
        FROM miller_bookings mb
        JOIN payments p ON p.booking_id = mb.id
        WHERE mb.id=%s
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

    return redirect(request.referrer or "/miller")

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
        WHERE mb.id=%s AND ms.miller_id=%s
    """, (booking_id, get_effective_user_id()))

    if not cur.fetchone():
        con.close()
        return redirect("/miller")

    # ✅ Update final invoice (keep payment status as is)
    cur.execute("""
        UPDATE payments
        SET invoice_file=%s
        WHERE booking_id=%s
    """, (filename, booking_id))
    
    # 📱 Send SMS to buyer about invoice update
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id
        FROM miller_bookings mb
        WHERE mb.id=%s
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
        WHERE li.id=%s AND ms.miller_id=%s AND li.qc_status='verified'
    """, (invoice_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    invoice_db_id, booking_id, truck_number, loaded_qty, order_id = row

    # ✅ Update truck final invoice
    cur.execute("""
        UPDATE loading_invoices
        SET final_invoice_file=%s,
            payment_status='pending'
        WHERE id=%s
    """, (filename, invoice_id))

    # 📱 Send SMS to buyer about truck final invoice
    cur.execute("""
        SELECT mb.buyer_id, ms.crop, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
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
        WHERE li.id=%s AND ms.miller_id=%s
    """, (invoice_id, get_effective_user_id()))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    invoice_db_id, booking_id, order_id = row

    # ✅ Update truck final invoice (keep payment status as is)
    cur.execute("""
        UPDATE loading_invoices
        SET final_invoice_file=%s
        WHERE id=%s
    """, (filename, invoice_id))

    # 📱 Send SMS to buyer about invoice update
    cur.execute("""
        SELECT mb.buyer_id
        FROM miller_bookings mb
        WHERE mb.id=%s
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
        WHERE li.id=%s AND ms.miller_id=%s AND li.final_invoice_file IS NOT NULL
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
        WHERE id=%s
    """, (invoice_id,))

    # 📱 Send SMS to buyer about payment completion
    cur.execute("""
        SELECT mb.buyer_id, ms.price
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
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
    log_activity("Truck Payment Done", invoice_id, f"Payment marked for truck {invoice_id} of Order {order_id}")
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
        WHERE mb.id=%s AND ms.miller_id=%s AND mb.loading_status='loaded'
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
            SET bill_document=%s
            WHERE id=%s
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
        "SELECT * FROM miller_profiles WHERE miller_id=%s",
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
                    SET mill_name=%s, owner_phone=%s, accountant_phone=%s, staff_phone=%s, 
                        address=%s, gst_doc=%s, mandi_doc=%s, other_doc=%s
                    WHERE miller_id=%s
                """, (mill_name, owner_phone, accountant_phone, staff_phone, address, 
                      gst_filename, mandi_filename, other_filename, miller_id))
            else:
                cur.execute("""
                    INSERT INTO miller_profiles
                    (miller_id, mill_name, owner_phone, accountant_phone, staff_phone, 
                     address, gst_doc, mandi_doc, other_doc)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (miller_id, mill_name, owner_phone, accountant_phone, staff_phone, 
                      address, gst_filename, mandi_filename, other_filename))

            con.commit()
            con.close()
            return redirect("/miller/profile")
        except Exception as e:
            con.rollback()
            con.close()
            logger.error(f"Error saving miller profile: {str(e)}")
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
    cur.execute("SELECT id FROM users WHERE email=%s", (email,))
    if cur.fetchone():
        con.close()
        return redirect("/miller")

    cur.execute("""
        INSERT INTO users
        (name, email, password, role, status, is_staff, parent_miller_id)
        VALUES (%s, %s, %s, 'miller', 'approved', 1, %s)
    """, (name, email, generate_password_hash(password), parent_miller_id))

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
        "SELECT * FROM buyer_profiles WHERE buyer_id=%s",
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
        
        # New compliance fields
        gst_number = request.form.get("gst_number", "").strip()
        mandi_number = request.form.get("mandi_number", "").strip()

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
                SET shop_name=%s, owner_name=%s, phone=%s, address=%s, gst_doc=%s, license_doc=%s, other_doc=%s,
                    gst_number=%s, mandi_number=%s
                WHERE buyer_id=%s
            """, (
                shop_name, owner_name, phone, address,
                gst_filename, lic_filename, other_filename,
                gst_number, mandi_number,
                session["user_id"]
            ))
        else:
            cur.execute("""
                INSERT INTO buyer_profiles
                (buyer_id, shop_name, owner_name, phone, address, gst_doc, license_doc, other_doc, gst_number, mandi_number)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                session["user_id"], shop_name, owner_name, phone, address,
                gst_filename, lic_filename, other_filename,
                gst_number, mandi_number
            ))

        # Sync name to users table so it reflects everywhere
        cur.execute("UPDATE users SET name=%s WHERE id=%s", (shop_name, session["user_id"]))
        con.commit()
        log_activity("Profile Updated", session["user_id"], "Buyer Profile Updated", user_id=session["user_id"], role="buyer")
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
        WHERE id=%s AND buyer_id=%s AND status='approved'
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
            SET quantity = quantity + %s,
                reserved_qty = reserved_qty - %s
            WHERE id=%s
        """, (remaining_qty, remaining_qty, stock_id))

    # Close booking partially
    cur.execute("""
        UPDATE miller_bookings
        SET
            loading_status='partial_closed',
            close_reason=%s,
            closed_by='buyer',
            decision_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (reason, booking_id))
    
    # 📱 Send SMS to miller about partial closure
    cur.execute("""
        SELECT ms.miller_id, mb.order_id, ms.crop, mb.quantity, mb.loaded_qty
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
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

@app.route("/miller/close_remaining/<int:booking_id>", methods=["POST"])
def miller_close_remaining(booking_id):
    if session.get("role") != "miller":
        return redirect("/")

    reason = request.form.get("reason", "").strip()
    if not reason:
        return redirect(request.referrer or "/miller")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # Fetch booking and verify ownership
    cur.execute("""
        SELECT mb.stock_id, mb.quantity, mb.loaded_qty, mb.status
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s AND ms.miller_id=%s AND mb.status='approved'
    """, (booking_id, miller_id))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect(request.referrer or "/miller")

    stock_id, booked_qty, loaded_qty, status = row
    loaded_qty = loaded_qty or 0
    remaining_qty = booked_qty - loaded_qty

    # Return remaining stock to miller (quantity + reserved_qty adjustment)
    if remaining_qty > 0:
        cur.execute("""
            UPDATE miller_stock
            SET quantity = quantity + %s,
                reserved_qty = reserved_qty - %s
            WHERE id=%s
        """, (remaining_qty, remaining_qty, stock_id))

    # Close booking partially
    cur.execute("""
        UPDATE miller_bookings
        SET
            loading_status='partial_closed',
            close_reason=%s,
            closed_by='miller',
            decision_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (reason, booking_id))
    
    # 📱 Send SMS to buyer about partial closure
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, ms.crop, mb.quantity, mb.loaded_qty
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
    """, (booking_id,))
    close_info = cur.fetchone()
    if close_info:
        buyer_id, order_id, crop, total_qty, loaded_qty = close_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            remaining = total_qty - (loaded_qty or 0)
            message = f"⚠️ Order {order_id} close by miller. {crop} - Remaining: {remaining} qty. Reason: {reason}"
            send_sms(buyer_phone, message)

    con.commit()
    log_activity("Booking Partial Close", booking_id, f"Miller Closed Remaining. Reason: {reason}")
    con.close()

    return redirect(request.referrer or "/miller")

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
            SELECT quantity FROM miller_bookings WHERE id=%s
        )
        WHERE id = (
            SELECT stock_id FROM miller_bookings WHERE id=%s
        )
    """, (id, id))

    # Calculate deadline based on stock duration
    cur.execute("""
        SELECT s.duration 
        FROM miller_stock s
        JOIN miller_bookings b ON s.id = b.stock_id
        WHERE b.id = %s
    """, (id,))
    row = cur.fetchone()
    duration = row[0] if row else None
    
    deadline_expr = "NULL"
    if duration:
        deadline_expr = f"CURRENT_TIMESTAMP + INTERVAL '{duration} days'"

    # Approve booking
    cur.execute(f"""
        UPDATE miller_bookings
        SET status='approved',
            decision_at=CURRENT_TIMESTAMP,
            deadline_at={deadline_expr}
        WHERE id=%s
    """, (id,))
    
    # 📱 Send SMS to buyer about approval
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, ms.crop, mb.quantity
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
    """, (id,))
    booking_info = cur.fetchone()
    if booking_info:
        buyer_id, order_id, crop, qty = booking_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"✅ Order {order_id} approved! {crop} - Qty: {qty}. Please proceed with loading."
            send_sms(buyer_phone, message)

    con.commit()
    log_activity("Booking Approved", id, f"Booking Approved for Order {order_id}")
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
    SELECT stock_id, quantity FROM miller_bookings WHERE id=%s
    """, (id,))
    row = cur.fetchone()

    if row:
        stock_id, qty = row
        cur.execute("UPDATE miller_stock SET quantity=quantity+%s WHERE id=%s", (qty, stock_id))

    cur.execute("""
    UPDATE miller_bookings
    SET status='declined', reason=%s, decision_at=CURRENT_TIMESTAMP
    WHERE id=%s
    """, (reason, id))
    
    # 📱 Send SMS to buyer about decline
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, ms.crop
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.id=%s
    """, (id,))
    booking_info = cur.fetchone()
    if booking_info:
        buyer_id, order_id, crop = booking_info
        buyer_phone = get_buyer_phone(buyer_id)
        if buyer_phone:
            message = f"❌ Order {order_id} declined. {crop} - Reason: {reason}"
            send_sms(buyer_phone, message)

    con.commit()
    log_activity("Booking Declined", id, f"Booking Declined. Reason: {reason}")
    con.close()
    return redirect("/miller")

# ---------------- UPDATE MILLER STOCK ----------------
@app.route("/update_miller_stock/<int:id>", methods=["POST"])
def update_miller_stock(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT price,quantity FROM miller_stock WHERE id=%s", (id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/miller")
    old_price, old_qty = row

    new_price = request.form["price"]
    new_qty = request.form["quantity"]
    deduction = request.form["deduction"]
    weight_deduction = request.form.get("weight_deduction", "")
    payment_duration = request.form.get("payment_duration", "")
    extra_condition = request.form.get("extra_condition", "")

    cur.execute("""
        UPDATE miller_stock_history
        SET
        new_price=%s,
        new_quantity=%s,
        updated_at=CURRENT_TIMESTAMP
        WHERE stock_id=%s AND id=(SELECT MAX(id) FROM miller_stock_history WHERE stock_id=%s)
    """, (
        new_price,
        new_qty,
        id,
        id
    ))
    
    cur.execute("""
        UPDATE miller_stock
        SET deduction=%s,
            price=%s,
            quantity=%s,
            weight_deduction=%s,
            payment_duration=%s,
            extra_condition=%s,
            status='open'
        WHERE id=%s AND miller_id=%s
    """, (
        deduction,
        new_price,
        new_qty,
        weight_deduction,
        payment_duration,
        extra_condition,
        id,
        get_effective_user_id()
    ))

    # Record history
    cur.execute("""
    INSERT INTO miller_stock_history
    (stock_id,miller_id,old_price,new_price,old_quantity,new_quantity)
    VALUES (%s,%s,%s,%s,%s,%s)
    """, (
        id,
        get_effective_user_id(),
        old_price,
        new_price,
        old_qty,
        new_qty
    ))

    con.commit()
    log_activity("Stock Updated", id, f"Updated Stock {id} - Price: {new_price}, Qty: {new_qty}")
    
    # 📱 Send SMS to all buyers about stock update
    cur.execute("SELECT crop, note FROM miller_stock WHERE id=%s", (id,))
    crop_result = cur.fetchone()
    if crop_result:
        crop = crop_result[0]
        note = crop_result[1] or ""
        buyer_phones = get_all_buyer_phones()
        message = f"📢 Stock updated! {crop} - New Price: ₹{new_price}/unit. {note} Check the market for details."
        for phone in buyer_phones:
            send_sms(phone, message)
    
    con.close()
    return redirect("/miller")

@app.route("/miller/toggle_stock_status/<int:id>", methods=["POST"])
def toggle_stock_status(id):
    if session.get("role") != "miller":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # Check current status and ownership
    cur.execute("SELECT status, quantity FROM miller_stock WHERE id=%s AND miller_id=%s", (id, get_effective_user_id()))
    row = cur.fetchone()

    if row:
        current_status = row[0]
        qty = row[1]
        
        # Toggle logic
        new_status = 'inactive' if current_status == 'open' else 'open'
        
        # Prevent opening if quantity is 0
        if new_status == 'open' and qty <= 0:
            flash("Cannot set to Live. Quantity is 0.", "error")
        else:
            cur.execute("UPDATE miller_stock SET status=%s WHERE id=%s", (new_status, id))
            con.commit()
            status_msg = "Live" if new_status == 'open' else "Inactive"
            flash(f"Stock marked as {status_msg}.", "success")

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
        COALESCE(mp.mill_name, users.name),  -- 10 (miller name from profile)
        miller_stock.note,         -- 11
        mp.address,                -- 12 (miller location)
        mp.city,                   -- 13
        mp.state,                  -- 14
        mp.pincode,                -- 15
        miller_stock.weight_deduction,  -- 16
        miller_stock.payment_duration,  -- 17
        miller_stock.extra_condition    -- 18
    FROM miller_stock
    JOIN users ON miller_stock.miller_id = users.id
    LEFT JOIN miller_profiles mp ON users.id = mp.miller_id
    WHERE miller_stock.status = 'open' AND miller_stock.quantity > 0
    ORDER BY miller_stock.created_at DESC
    """)
    miller_stocks = cur.fetchall()

    cur.execute("""
SELECT
    mb.id,                 -- 0
    ms.crop,               -- 1
    mb.quantity,           -- 2
    mb.loaded_qty,         -- 3
    (mb.quantity - COALESCE(mb.loaded_qty, 0)), -- 4 remaining
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
    COALESCE(p.status,'pending') AS payment_status,  -- 17
    p.invoice_file              AS final_invoice,   -- 18
    p.paid_at                   AS payment_at,      -- 19
    COALESCE(mp.mill_name, u.name)   AS miller_name,     -- 20 (from profile)
    mb.deadline_at                                  -- 21 (NEW)
FROM miller_bookings mb
JOIN miller_stock ms ON mb.stock_id = ms.id
JOIN users u ON ms.miller_id = u.id
LEFT JOIN payments p ON p.booking_id = mb.id
LEFT JOIN miller_profiles mp ON u.id = mp.miller_id
WHERE mb.buyer_id=%s
ORDER BY mb.created_at DESC
""", (session["user_id"],))

    my_bookings = cur.fetchall()

    active_bookings = [
        b for b in my_bookings
        if b[8] in ('pending', 'partial') and b[10] == 'approved'
    ]

    requested_bookings = [
        b for b in my_bookings
        if b[10] == 'pending'
    ]

    partial_closed_bookings = [
        b for b in my_bookings
        if b[8] == 'partial_closed'
    ]

    loaded_bookings = [
        b for b in my_bookings
        if b[8] in ('loaded', 'partial_closed')
    ]

    rejected_bookings = [
        b for b in my_bookings
        if b[10] in ('cancelled', 'declined')
    ]

    # Create map for booking info to enrich invoices
    bookings_info = {b[0]: b for b in my_bookings}
    
    debit_notes_list = []
    payments_list = []

    # Fetch per-truck loading invoices WITH QC DATA AND FINAL INVOICE
    all_booking_ids = [b[0] for b in my_bookings]
    invoices_map = {}
    if all_booking_ids:
        placeholders = ",".join(["%s"] * len(all_booking_ids))
        cur.execute(f"""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at, qc_freight
        FROM loading_invoices
        WHERE booking_id IN ({placeholders})
        ORDER BY created_at ASC
        """, all_booking_ids)
        rows = cur.fetchall()
        for r in rows:
            inv_data = {
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
                "payment_at": r[13],
                "qc_freight": r[14]
            }
            invoices_map.setdefault(r[1], []).append(inv_data)

            # Enrich with booking info for lists
            b_info = bookings_info.get(r[1])
            if b_info:
                # b_info structure based on query:
                # 0:id, 1:crop, 2:qty, 3:loaded, 4:rem, 5:truck_st, 6:loaded_at, ... 9:order_id, ... 20:miller_name
                # Need to verify indices from query
                # Query: SELECT mb.id (0), ms.crop (1), ... mb.order_id (9), ... u.name (20)
                # See lines 2673-2700 approx
                
                enriched_inv = inv_data.copy()
                enriched_inv.update({
                    "order_id": b_info[9],
                    "miller_name": b_info[20],
                    "crop": b_info[1],
                    # Calculate amount roughly (price not in my_bookings query! need to add price?)
                    # Price is not in my_bookings query currently.
                    # It is in miller_stocks but my_bookings query joins miller_stock.
                    # Let's check my_bookings query again.
                })
                
                if inv_data["final_invoice_file"] and inv_data["payment_status"] != 'paid':
                    debit_notes_list.append(enriched_inv)
                elif inv_data["payment_status"] == 'paid':
                    payments_list.append(enriched_inv)
        
        logger.debug(f"DEBUG: Debit Notes Count: {len(debit_notes_list)}")
        logger.debug(f"DEBUG: Payments Count: {len(payments_list)}")
        if debit_notes_list:
             logger.debug(f"DEBUG: Sample Debit Note: {debit_notes_list[0]['id']} - {debit_notes_list[0]['final_invoice_file']}")

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

    # Fetch Buyer Details for Header
    user_id = session.get("user_id")
    cur.execute("""
        SELECT COALESCE(bp.shop_name, u.name) AS display_name,
               bp.address, bp.city, bp.state 
        FROM users u 
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id 
        WHERE u.id = %s
    """, (user_id,))
    buyer_info = cur.fetchone()
    buyer_name = buyer_info[0] if buyer_info else "Trader"
    buyer_address = f"{buyer_info[1]}, {buyer_info[2]}" if buyer_info and buyer_info[1] else "Address not set"

    con.close()
    
    return render_template(
        "market.html",
        crops=crops,
        miller_stocks=miller_stocks,
        my_bookings=active_bookings,
        requested_bookings=requested_bookings,
        partial_closed_bookings=partial_closed_bookings,
        loaded_bookings=loaded_bookings,
        invoices_map=invoices_map,
        total_booked=total_booked,
        total_loaded=total_loaded,
        total_remaining=total_remaining,
        rejected_bookings=rejected_bookings,
        debit_note_invoices=debit_notes_list,
        paid_invoices=payments_list,
        buyer_name=buyer_name,
        buyer_address=buyer_address
    )
# ================= BUYER ORDER PAGES =================

def get_buyer_orders(filter_type):
    con = get_db()
    cur = con.cursor()

    where = ""
    if filter_type == "active":
        where = "AND LOWER(mb.status)='approved' AND LOWER(mb.loading_status) IN ('pending','partial')"
    elif filter_type == "requested":
        where = "AND LOWER(mb.status)='pending'"
    elif filter_type == "partial":
        where = "AND LOWER(mb.loading_status)='partial_closed'"
    elif filter_type == "loaded":
        where = "AND LOWER(mb.loading_status) IN ('loaded', 'partial_closed')"
    elif filter_type == "rejected":
        where = "AND LOWER(mb.status) IN ('declined', 'cancelled')"

    cur.execute(f"""
        SELECT
            mb.id,
            mb.order_id,
            ms.crop,
            mb.quantity,
            COALESCE(mb.loaded_qty,0),
            mb.loaded_at,
            mb.loading_status,

            mb.qc_weight,
            mb.qc_moisture,
            mb.qc_remarks,
            mb.qc_status,
            mb.qc_at,

            COALESCE(p.status,'na') AS payment_status,
            p.invoice_file,
            p.paid_at,

            u.name AS miller_name,
            mb.close_reason,
            mp.city,
            mp.state

        FROM miller_bookings mb
JOIN miller_stock ms ON mb.stock_id = ms.id
JOIN users u ON ms.miller_id = u.id
LEFT JOIN miller_profiles mp ON u.id = mp.miller_id
LEFT JOIN payments p ON p.booking_id = mb.id

        WHERE mb.buyer_id=%s
        {where}
        ORDER BY mb.created_at DESC
    """, (session["user_id"],))

    rows = cur.fetchall()

    cur.execute("""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at, qc_freight
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
            "payment_at": i[13],
            "qc_freight": i[14]
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
            "miller_city": r[17],
            "miller_state": r[18],

            "invoices": invoices_map.get(r[0], [])
        })

    con.close()
    return orders
def get_miller_orders_by_type(filter_type):
    con = get_db()
    cur = con.cursor()

    where = ""
    if filter_type == "approved":
        where = "AND LOWER(mb.status)='approved' AND LOWER(mb.loading_status) IN ('pending','partial')"
    elif filter_type == "qc":
        where = "AND LOWER(mb.loading_status)='loaded' AND LOWER(mb.qc_status)='pending'"
    elif filter_type == "final":
        where = "AND LOWER(mb.loading_status)='loaded' AND COALESCE(p.invoice_file,'') != '' AND COALESCE(LOWER(p.status),'pending')='pending'"
    elif filter_type == "rejected":
        where = "AND LOWER(mb.status) IN ('declined','cancelled')"

    cur.execute(f"""
        SELECT
            mb.id,                -- 0
            mb.order_id,          -- 1
            COALESCE(bp.shop_name, u.name), -- 2 buyer (from profile)
            ms.crop,              -- 3
            mb.quantity,          -- 4 booked
            COALESCE(mb.loaded_qty,0), -- 5 loaded
            (mb.quantity - COALESCE(mb.loaded_qty,0)), -- 6 remaining
            mb.loading_status,    -- 7
            mb.qc_status,         -- 8
            mb.qc_weight,         -- 9
            mb.qc_moisture,       -- 10
            mb.qc_at,             -- 11
            COALESCE(p.status,'pending'), -- 12 payment_status
            p.invoice_file,       -- 13 final_invoice
            mb.close_reason       -- 14
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON mb.buyer_id = u.id
        LEFT JOIN payments p ON p.booking_id = mb.id
        LEFT JOIN buyer_profiles bp ON u.id = bp.buyer_id
        WHERE ms.miller_id=%s
        {where}
        ORDER BY mb.created_at DESC
    """, (get_effective_user_id(),))

    rows = cur.fetchall()

    # 🔹 Fetch per-truck invoices WITH FINAL INVOICE
    cur.execute("""
        SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at, qc_freight
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
            "payment_at": i[13],
            "qc_freight": i[14]
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


@app.route("/buyer/requested")
def buyer_requested():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("requested")
    return render_template("buyer_requested.html", page_title="Requested Orders", orders=orders)


@app.route("/buyer/active")
def buyer_active():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("active")
    return render_template("buyer_active.html", page_title="Active Orders", orders=orders)


@app.route("/buyer/rejected")
def buyer_rejected():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("rejected")
    return render_template("buyer_rejected.html", page_title="Rejected Orders", orders=orders)


@app.route("/buyer/debit_note/<int:invoice_id>")
def buyer_debit_note(invoice_id):
    if session.get("role") != "buyer":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    # Fetch invoice details with order info, including booking_id and buyer_id for security
    cur.execute("""
        SELECT li.id, li.truck_number, li.loaded_qty, li.final_invoice_file, li.payment_status,
               mb.order_id, ms.crop, COALESCE(mb.price, ms.price), u.name, mb.id,
               li.qc_weight, li.qc_moisture, li.qc_remarks, li.qc_freight,
               li.qc_broken, li.qc_karda, li.qc_oil, li.qc_mitti, li.qc_ssa, li.qc_claim
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON ms.miller_id = u.id
        WHERE li.id = %s AND mb.buyer_id = %s
    """, (invoice_id, session["user_id"]))
    
    inv = cur.fetchone()
    con.close()
    
    if not inv:
        return "Invoice not found or unauthorized", 404
        
    invoice_data = {
        "id": inv[0],
        "truck_number": inv[1],
        "qty": inv[2],
        "final_invoice": inv[3],
        "status": inv[4] or "pending",
        "order_id": inv[5],
        "crop": inv[6],
        "price": inv[7],
        "miller_name": inv[8],
        "booking_id": inv[9],
        "qc_weight": inv[10],
        "qc_moisture": inv[11],
        "qc_remarks": inv[12],
        "qc_freight": inv[13],
        "qc_broken": inv[14],
        "qc_karda": inv[15],
        "qc_oil": inv[16],
        "qc_mitti": inv[17],
        "qc_ssa": inv[18],
        "qc_claim": inv[19],
        "total_amount": round((inv[2] or 0) * (inv[7] or 0), 2)
    }
    
    return render_template("buyer_debit_note.html", invoice=invoice_data)


@app.route("/buyer/debit_notes")
def buyer_debit_notes_list():
    if session.get("role") != "buyer":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    # Fetch all invoices that have a final invoice file (debit note)
    cur.execute("""
        SELECT li.id, li.truck_number, li.loaded_qty, li.final_invoice_file, li.payment_status,
               mb.order_id, ms.crop, COALESCE(mb.price, ms.price), u.name, mb.id, li.created_at
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON ms.miller_id = u.id
        WHERE mb.buyer_id = %s AND li.final_invoice_file IS NOT NULL
        ORDER BY li.created_at DESC
    """, (session["user_id"],))
    
    rows = cur.fetchall()
    con.close()
    
    invoices = []
    for r in rows:
        status = r[4] or "pending"
        # Calculate amount
        amount = round((r[2] or 0) * (r[7] or 0), 2)
        
        invoices.append({
            "id": r[0],
            "truck_number": r[1],
            "qty": r[2],
            "final_invoice": r[3],
            "status": status,
            "order_id": r[5],
            "crop": r[6],
            "miller_name": r[8],
            "booking_id": r[9],
            "date": r[10],
            "amount": amount
        })
        
    return render_template("buyer_debit_notes_list.html", invoices=invoices)


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
        li.loaded_qty,
        COALESCE(mb.price, ms.price),
        (li.loaded_qty * COALESCE(mb.price, ms.price)) AS total_amount,
        li.final_invoice_file,
        li.payment_at,
        COALESCE(mp.mill_name, u.name) AS miller_name,
        li.truck_number,
        mp.city,
        mp.state
    FROM loading_invoices li
    JOIN miller_bookings mb ON li.booking_id = mb.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users u ON ms.miller_id = u.id
    LEFT JOIN miller_profiles mp ON u.id = mp.miller_id
    WHERE mb.buyer_id=%s AND li.payment_status='paid'
    ORDER BY li.payment_at DESC
    """, (session["user_id"],))

    payments = cur.fetchall()
    con.close()

    return render_template("buyer_payments.html", payments=payments)

@app.route("/book_miller_stock/<int:stock_id>", methods=["POST"])
def book_miller_stock(stock_id):
    if session.get("role") != "buyer":
        flash("Unauthorized access.", "error")
        return redirect("/market")

    try:
        qty = float(request.form["quantity"])
    except (TypeError, ValueError):
        flash("Invalid quantity entered.", "error")
        return redirect("/market")

    if qty <= 0:
        flash("Quantity must be greater than 0.", "error")
        return redirect("/market")

    logger.debug(f"DEBUG: Booking request for stock_id: {stock_id}, qty: {qty}")
    
    con = get_db()
    cur = con.cursor()

    # Check if stock exists and has enough quantity
    cur.execute("""
        SELECT quantity, status, price, auto_approve_min_qty
        FROM miller_stock
        WHERE id=%s
    """, (stock_id,))
    row = cur.fetchone()

    if not row:
        logger.debug("DEBUG: Stock not found")
        flash("Stock not found.", "error")
    elif row[1] != 'open':
        logger.debug(f"DEBUG: Stock status is {row[1]}, not open")
        flash("Stock is closed or unavailable.", "error")
    elif row[0] < qty:
        logger.debug(f"DEBUG: Insufficient stock. Available: {row[0]}, Requested: {qty}")
        flash(f"Insufficient stock quantity. Available: {row[0]}", "error")
    else:
        current_price = row[2]
        auto_approve_min_qty = row[3] or 0
        
        # Auto-approve logic: If threshold > 0 AND booking qty <= threshold
        is_auto_approved = (auto_approve_min_qty > 0 and qty <= auto_approve_min_qty)
        logger.debug(f"DEBUG: Auto-approve: {is_auto_approved} (threshold: {auto_approve_min_qty})")
        
        initial_status = 'approved' if is_auto_approved else 'pending'

        # Generate order ID
        try:
            order_id = generate_next_order_id()
            logger.debug(f"DEBUG: Generated order_id: {order_id}")
            
            # Create booking
            cur.execute("""
                INSERT INTO miller_bookings
                (stock_id, buyer_id, quantity, status, order_id, price)
                VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
    """, (stock_id, session["user_id"], qty, initial_status, order_id, current_price))
            
            booking_id = cur.fetchone()[0]  # was lastrowid
            logger.debug(f"DEBUG: Created booking_id: {booking_id}")
            
            # If auto-approved, we should probably set decision_at as well?
            if is_auto_approved:
                cur.execute("UPDATE miller_bookings SET decision_at=CURRENT_TIMESTAMP WHERE id=%s", (booking_id,))
            
            # DEDUCT quantity immediately from stock
            cur.execute("""
                UPDATE miller_stock
                SET quantity = quantity - %s
                WHERE id=%s
            """, (qty, stock_id))
            
            # Close stock if quantity reaches 0
            cur.execute("""
                UPDATE miller_stock
                SET status='closed'
                WHERE id=%s AND quantity <= 0
            """, (stock_id,))
            
            # 📱 Send SMS to miller about new booking
            cur.execute("""
                SELECT ms.miller_id, ms.crop
                FROM miller_stock ms
                WHERE ms.id = %s
            """, (stock_id,))
            stock_info = cur.fetchone()
            if stock_info:
                miller_id, crop = stock_info
                miller_phone = get_miller_phone(miller_id)
                if miller_phone:
                    if is_auto_approved:
                        message = f"✅ New Auto-Approved Order! {order_id}: {crop} - Qty: {qty}. Status: Approved."
                    else:
                        message = f"🆕 New booking received! Order {order_id}: {crop} - Qty: {qty}. Please review and approve."
                    send_sms(miller_phone, message)
            
            con.commit()
            log_activity("Stock Booked", booking_id, f"Order {order_id} placed. Qty: {qty}")
            if is_auto_approved:
                flash(f"Order {order_id} placed successfully and Auto-Approved!", "success")
            else:
                flash(f"Order {order_id} placed successfully! Waiting for miller approval.", "success")
        except Exception as e:
            con.rollback()
            logger.error(f"Error booking stock: {e}")
            flash("An error occurred while placing the order.", "error")
            flash(str(e), "error") # DEBUG: Show actual error
            logger.error(f"Error booking stock: {e}")

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
    WHERE id=%s AND buyer_id=%s AND status IN ('pending','approved') AND loaded_qty=0
    """, (id, get_effective_user_id()))
    row = cur.fetchone()

    if row:
        stock_id, qty, loaded = row
        loaded = loaded or 0
        remaining = max(0, qty - loaded)

        if remaining > 0:
            cur.execute(
                "UPDATE miller_stock SET quantity=quantity+%s WHERE id=%s",
                (remaining, stock_id)
            )

        # Keep original booked qty; mark cancelled while preserving loaded part
        cur.execute("""
            UPDATE miller_bookings
            SET status='cancelled',
                loading_status='cancelled',
                decision_at=CURRENT_TIMESTAMP
            WHERE id=%s
        """, (id,))
        
        # 📱 Send SMS to miller about cancellation
        cur.execute("""
            SELECT ms.miller_id, mb.order_id, ms.crop, mb.quantity
            FROM miller_bookings mb
            JOIN miller_stock ms ON mb.stock_id = ms.id
            WHERE mb.id=%s
        """, (id,))
        cancel_info = cur.fetchone()
        if cancel_info:
            miller_id, order_id, crop, qty = cancel_info
            miller_phone = get_miller_phone(miller_id)
            if miller_phone:
                message = f"❌ Order {order_id} cancelled by buyer. {crop} - Qty: {qty}. Stock returned to inventory."
                send_sms(miller_phone, message)
        
        con.commit()
        log_activity("Booking Cancelled", id, f"Order {order_id} cancelled by buyer")

    con.close()
    return redirect("/market")

@app.route("/buyer/update_loading/<int:id>", methods=["POST"])
def buyer_update_loading(id):
    if session.get("role") != "buyer":
        return redirect("/market")

    try:
        load_qty = float(request.form.get("load_qty", 0) or 0)
    except (TypeError, ValueError):
        load_qty = 0

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
        WHERE id=%s AND buyer_id=%s AND status='approved'
    """, (id, session["user_id"]))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/market")

    total_qty, loaded_qty, stock_id = row

    try:
        total_qty = float(total_qty or 0)
    except (TypeError, ValueError):
        total_qty = 0

    try:
        loaded_qty = float(loaded_qty or 0)
    except (TypeError, ValueError):
        loaded_qty = 0

    remaining = total_qty - loaded_qty

    # Remove artificial cap on load_qty
    # if load_qty > remaining:
    #     load_qty = remaining

    new_loaded = loaded_qty + load_qty

    # Float-safe completion check (still marks as loaded if fully loaded)
    EPS = 1e-6
    # Loading status logic: 'loaded' if >= total booked. 
    # If overloaded, it is also 'loaded'.
    loading_status = "loaded" if new_loaded >= (total_qty - EPS) else "partial"
    truck_status = loading_status

    # 🔹 Update booking
    cur.execute("""
        UPDATE miller_bookings
        SET loaded_qty=%s,
            loading_status=%s,
            truck_status=%s,
            loaded_at=CURRENT_TIMESTAMP
        WHERE id=%s AND buyer_id=%s
    """, (new_loaded, loading_status, truck_status, id, session["user_id"]))

    # 🔹 Save per-truck invoice
    truck_number_val = truck_number if truck_number else None
    cur.execute("""
        INSERT INTO loading_invoices
        (booking_id, loaded_qty, invoice_file, truck_number)
        VALUES (%s, %s, %s, %s)
    """, (id, load_qty, filename, truck_number_val))

    # 🔹 MOVE RESERVED → USED STOCK
    # Only deduct from reserved_qty up to the original booked amount
    remaining_reserved = max(0, total_qty - loaded_qty)
    deduct_from_reserved = min(load_qty, remaining_reserved)

    cur.execute("""
        UPDATE miller_stock
        SET
            quantity = quantity - %s,
            reserved_qty = reserved_qty - %s
        WHERE id=%s
    """, (load_qty, deduct_from_reserved, stock_id))

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
        WHERE mb.id=%s
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
        WHERE li.id=%s AND mb.buyer_id=%s
    """, (invoice_id, session["user_id"]))

    row = cur.fetchone()
    if not row:
        con.close()
        return redirect("/market")

    # ✅ Update the invoice file (+ truck number)
    truck_number_val = truck_number if truck_number else None
    cur.execute("""
        UPDATE loading_invoices
        SET invoice_file=%s,
            truck_number=%s
        WHERE id=%s
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
    WHERE mb.id=%s AND mb.buyer_id=%s AND p.status='paid'
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
        WHERE li.id=%s AND ms.miller_id=%s
    """, (invoice_id, miller_id))
    if not cur.fetchone():
        con.close()
        return redirect(request.referrer or "/miller")

    qc_weight = request.form.get("qc_weight") or None
    qc_moisture = request.form.get("qc_moisture") or None
    qc_remarks = request.form.get("qc_remarks") or ""
    qc_freight = request.form.get("qc_freight") or None
    
    # New QC Fields
    qc_broken = request.form.get("qc_broken") or None
    qc_karda = request.form.get("qc_karda") or None
    qc_oil = request.form.get("qc_oil") or None
    qc_mitti = request.form.get("qc_mitti") or None
    qc_ssa = request.form.get("qc_ssa") or None
    qc_claim = request.form.get("qc_claim") or None

    def safe_float(val):
        try:
            return float(val) if val not in (None, "",) else None
        except ValueError:
            return None

    qc_weight_val = safe_float(qc_weight)
    qc_moisture_val = safe_float(qc_moisture)
    qc_freight_val = safe_float(qc_freight)
    
    qc_broken_val = safe_float(qc_broken)
    qc_karda_val = safe_float(qc_karda)
    qc_oil_val = safe_float(qc_oil)
    qc_mitti_val = safe_float(qc_mitti)
    qc_ssa_val = safe_float(qc_ssa)
    qc_claim_val = safe_float(qc_claim)

    # Update QC for this specific invoice (truck)
    cur.execute("""
        UPDATE loading_invoices
        SET qc_weight=%s,
            qc_moisture=%s,
            qc_remarks=%s,
            qc_status='verified',
            qc_at=CURRENT_TIMESTAMP,
            qc_freight=%s,
            qc_broken=%s,
            qc_karda=%s,
            qc_oil=%s,
            qc_mitti=%s,
            qc_ssa=%s,
            qc_claim=%s
        WHERE id=%s
    """, (qc_weight_val, qc_moisture_val, qc_remarks, qc_freight_val,
          qc_broken_val, qc_karda_val, qc_oil_val, qc_mitti_val, qc_ssa_val, qc_claim_val,
          invoice_id))
    
    # 📱 Send SMS to buyer about QC update
    cur.execute("""
        SELECT mb.buyer_id, mb.order_id, li.loaded_qty, li.truck_number
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        WHERE li.id=%s
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


@app.route("/miller/pending_payments")
def miller_pending_payments():
    """Miller view for trucks with Final Invoice uploaded but Payment Pending."""
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            li.id,
            mb.order_id,
            ms.crop,
            li.loaded_qty,
            li.truck_number,
            li.final_invoice_file,
            DATE(li.created_at) as date,
            u.name as buyer_name,
            COALESCE(mb.price, ms.price)
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users u ON mb.buyer_id = u.id
        WHERE ms.miller_id = %s
          AND li.final_invoice_file IS NOT NULL
          AND (li.payment_status IS NULL OR li.payment_status != 'paid')
        ORDER BY li.created_at DESC
    """, (miller_id,))
    
    rows = cur.fetchall()
    invoices = []
    for r in rows:
        invoices.append({
            "id": r[0],
            "order_id": r[1],
            "crop": r[2],
            "loaded_qty": r[3],
            "truck_number": r[4],
            "final_invoice_file": r[5],
            "created_at": r[6],
            "buyer_name": r[7],
            "price": r[8]
        })

    con.close()
    return render_template("miller_pending_payments.html", invoices=invoices)


@app.route("/miller/mark_paid/<int:invoice_id>", methods=["POST"])
def miller_mark_paid(invoice_id):
    """Allow Miller to manually mark an invoice/truck as Paid."""
    if session.get("role") != "miller":
        return redirect("/")

    miller_id = get_effective_user_id()
    con = get_db()
    cur = con.cursor()

    # Verify ownership
    cur.execute("""
        SELECT li.id 
        FROM loading_invoices li
        JOIN miller_bookings mb ON li.booking_id = mb.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE li.id=%s AND ms.miller_id=%s
    """, (invoice_id, miller_id))
    
    if not cur.fetchone():
        con.close()
        return redirect(request.referrer or "/miller")

    # Update payment status
    cur.execute("""
        UPDATE loading_invoices
        SET payment_status='paid',
            payment_at=CURRENT_TIMESTAMP
        WHERE id=%s
    """, (invoice_id,))
    
    con.commit()
    con.close()
    
    flash("Payment marked as received.", "success")
    return redirect(request.referrer or "/miller/pending_payments")


# ---------------- ADMIN ----------------
@app.route("/admin")
def admin():
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    # Optimized counters
    cur.execute("SELECT role, status, COUNT(*) FROM users GROUP BY role, status")
    roles_data = cur.fetchall()
    
    farmer_count = sum(r[2] for r in roles_data if r[0] == 'farmer')
    buyer_count  = sum(r[2] for r in roles_data if r[0] == 'buyer')
    miller_count = sum(r[2] for r in roles_data if r[0] == 'miller')
    
    approved_users = sum(r[2] for r in roles_data if r[1] == 'approved')
    pending_users = sum(r[2] for r in roles_data if r[1] == 'pending')
    blocked_users = sum(r[2] for r in roles_data if r[1] == 'blocked')

    # Latest bookings (limited to 50 for overview stats)
    cur.execute("""
    SELECT
        mb.id,                 -- 0 Booking ID
        buyer.name,            -- 1 Buyer
        miller.name,           -- 2 Miller
        ms.crop,               -- 3 Crop
        mb.quantity,           -- 4 Qty
        COALESCE(mb.price, ms.price), -- 5 Price
        (mb.quantity * COALESCE(mb.price, ms.price)), -- 6 Total
        mb.status,             -- 7 Booking status
        mb.truck_status,       -- 8 Loading status
        mb.loaded_at,          -- 9 Loaded date
        mb.truck_remark,       -- 10 Remark
        mb.order_id            -- 11 Order ID
    FROM miller_bookings mb
    JOIN users buyer ON mb.buyer_id = buyer.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users miller ON ms.miller_id = miller.id
    ORDER BY mb.created_at DESC
    LIMIT 50
""")
    bookings = cur.fetchall()

    # Total revenue from all-time approved bookings (using aggregate query)
    cur.execute("""
        SELECT SUM(mb.quantity * COALESCE(mb.price, ms.price))
        FROM miller_bookings mb
        JOIN miller_stock ms ON mb.stock_id = ms.id
        WHERE mb.status = 'approved'
    """)
    total_revenue = cur.fetchone()[0] or 0

    # Total stock qty (aggregate)
    cur.execute("SELECT SUM(quantity) FROM miller_stock")
    total_stock_qty = cur.fetchone()[0] or 0

    # Counts for status summary
    pending_bookings = sum(1 for b in bookings if b[7] == 'pending')
    total_bookings_all_time = 0 # We can fetch this if needed, but using len(bookings) for 'recent' context or a separate query
    cur.execute("SELECT COUNT(*) FROM miller_bookings")
    total_bookings = cur.fetchone()[0] or 0

    con.close()

    return render_template(
        "admin.html",
        bookings=bookings,
        pending_bookings=pending_bookings,
        total_revenue=total_revenue,
        total_bookings=total_bookings,
        total_stock_qty=total_stock_qty,
        approved_users=approved_users,
        pending_users=pending_users,
        blocked_users=blocked_users,
        farmer_count=farmer_count,
        buyer_count=buyer_count,
        miller_count=miller_count
    )

@app.route("/admin/order/<int:booking_id>")
def admin_order_detail(booking_id):
    """Detailed view for a single order."""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    # Fetch core booking data
    cur.execute("""
        SELECT
            mb.id,                 -- 0 Booking ID
            buyer.name,            -- 1 Buyer
            miller.name,           -- 2 Miller
            ms.crop,               -- 3 Crop
            mb.quantity,           -- 4 Qty
            COALESCE(mb.price, ms.price), -- 5 Price
            (mb.quantity * COALESCE(mb.price, ms.price)), -- 6 Total
            mb.status,             -- 7 Booking status
            mb.truck_status,       -- 8 Truck status
            mb.loaded_at,          -- 9 Loaded date
            mb.order_id,           -- 10 Order ID
            mb.created_at,         -- 11 Created at
            mb.loading_status,     -- 12 Loading status
            mb.bill_document,      -- 13 Bill doc
            mb.truck_remark,       -- 14 Remark
            buyer.email,           -- 15 Buyer email
            miller.email,          -- 16 Miller email
            mb.buyer_id,           -- 17 Buyer ID
            ms.miller_id,          -- 18 Miller ID
            mb.stock_id            -- 19 Stock ID
        FROM miller_bookings mb
        JOIN users buyer ON mb.buyer_id = buyer.id
        JOIN miller_stock ms ON mb.stock_id = ms.id
        JOIN users miller ON ms.miller_id = miller.id
        WHERE mb.id = %s
    """, (booking_id,))
    booking = cur.fetchone()
    
    if not booking:
        con.close()
        flash("Order not found.", "danger")
        return redirect("/admin/bookings")
    
    # Fetch loading invoices (trucks)
    cur.execute("""
        SELECT id, loaded_qty, invoice_file, truck_number, created_at,
               qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
               final_invoice_file, payment_status, payment_at, qc_freight, debit_note,
               qc_broken, qc_karda, qc_oil, qc_mitti, qc_ssa, qc_claim
        FROM loading_invoices
        WHERE booking_id = %s
        ORDER BY created_at ASC
    """, (booking_id,))
    invoices = cur.fetchall()
    
    # Fetch payment record if exists
    cur.execute("SELECT * FROM payments WHERE booking_id = %s", (booking_id,))
    payment = cur.fetchone()
    
    con.close()
    
    return render_template("admin_order_detail.html", booking=booking, invoices=invoices, payment=payment)

@app.route("/admin/buyer/<int:buyer_id>")
def admin_buyer_profile(buyer_id):
    """Detailed view for a buyer profile."""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        SELECT u.name, u.email, p.shop_name, p.phone, p.address, p.gst_doc, p.license_doc, p.gst_number, p.mandi_number, p.document, u.id, p.created_at
        FROM users u
        LEFT JOIN buyer_profiles p ON u.id = p.buyer_id
        WHERE u.id = %s
    """, (buyer_id,))
    buyer = cur.fetchone()
    
    con.close()
    
    if not buyer:
        flash("Buyer profile not found.", "danger")
        return redirect("/admin/buyer-profiles")
        
    return render_template("admin_profile_view.html", profile=buyer, member_type="buyer")

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
    SELECT 
        miller_stock.id, miller_stock.miller_id, miller_stock.crop, miller_stock.quantity, miller_stock.price, 
        miller_stock.condition, miller_stock.bag_type, miller_stock.deduction, miller_stock.created_at, 
        miller_stock.status, miller_stock.note, miller_stock.reserved_qty, miller_stock.auto_approve_min_qty, 
        users.name,
        miller_stock.weight_deduction, miller_stock.payment_duration, miller_stock.extra_condition
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
        COALESCE(mb.price, ms.price), -- 5 Price
        (mb.quantity * COALESCE(mb.price, ms.price)), -- 6 Total
        mb.status,             -- 7 Booking status
        mb.truck_status,       -- 8 Truck status
        mb.loaded_at,          -- 9 Loaded date
        mb.truck_remark,       -- 10 Remark
        mb.order_id,           -- 11 Order ID
        mb.loading_status,     -- 12 Loading status
        mb.bill_document,      -- 13 Bill document
        mb.loaded_qty,         -- 14 Loaded quantity
        mb.created_at          -- 15 Created at
    FROM miller_bookings mb
    JOIN users buyer ON mb.buyer_id = buyer.id
    JOIN miller_stock ms ON mb.stock_id = ms.id
    JOIN users miller ON ms.miller_id = miller.id
    ORDER BY mb.created_at DESC
    """)
    bookings = cur.fetchall()

    # Fetch per-truck invoices
    invoices_map = {}
    if bookings:
        booking_ids = [b[0] for b in bookings]
        placeholders = ",".join(["%s"] * len(booking_ids))
        cur.execute(f"""
            SELECT id, booking_id, loaded_qty, invoice_file, truck_number, created_at,
                   qc_weight, qc_moisture, qc_remarks, qc_status, qc_at,
                   final_invoice_file, payment_status, payment_at, qc_freight, debit_note,
                   qc_broken, qc_karda, qc_oil, qc_mitti, qc_ssa, qc_claim
            FROM loading_invoices
            WHERE booking_id IN ({placeholders})
            ORDER BY created_at ASC
        """, booking_ids)
        rows = cur.fetchall()
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
                "payment_at": r[13],
                "qc_freight": r[14],
                "debit_note": r[15],
                "qc_broken": r[16],
                "qc_karda": r[17],
                "qc_oil": r[18],
                "qc_mitti": r[19],
                "qc_ssa": r[20],
                "qc_claim": r[21]
            })

    con.close()
    
    return render_template("admin_bookings.html", bookings=bookings, invoices_map=invoices_map)

@app.route("/admin/miller-profiles")
def admin_miller_profiles():
    """Miller Profiles Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        SELECT
            mp.miller_id,
            u.name,
            mp.mill_name,
            mp.owner_phone,
            mp.address,
            mp.gst_doc,
            mp.mandi_doc,
            mp.other_doc,
            mp.created_at,
            mp.gst_number,
            mp.mandi_number
        FROM miller_profiles mp
        JOIN users u ON mp.miller_id = u.id
        ORDER BY mp.created_at DESC
    """)
    miller_profiles = cur.fetchall()
    con.close()
    
    return render_template("admin_network.html", profiles=miller_profiles, network_type="miller")

@app.route("/admin/buyer-profiles")
def admin_buyer_profiles():
    """Buyer/Trader Profiles Page"""
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    
    cur.execute("""
        SELECT
        bp.buyer_id,
        u.name,
        bp.shop_name,
        bp.phone,
        bp.address,
        bp.document,
        bp.created_at,
        bp.gst_number,
        bp.mandi_number,
        bp.gst_doc,
        bp.license_doc
    FROM buyer_profiles bp
    JOIN users u ON bp.buyer_id = u.id
    ORDER BY bp.created_at DESC
    """)
    buyer_profiles = cur.fetchall()
    con.close()
    
    return render_template("admin_network.html", profiles=buyer_profiles, network_type="buyer")


@app.route("/admin/logs")
def admin_logs():
    if session.get("role") != "admin":
        return redirect("/")
    
    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT l.id, COALESCE(u.name, 'Unknown User'), l.role, l.action, l.target_id, l.details, l.created_at, l.admin_id
            FROM admin_logs l
            LEFT JOIN users u ON l.admin_id = u.id
            ORDER BY l.created_at DESC
            LIMIT 100
        """)
        logs = cur.fetchall()
        con.close()
    except psycopg2.errors.UndefinedTable:
        con.rollback()
        con.close()
        # Auto-fix: Run migration and retry
        print("⚠️ admin_logs table missing. Running migration...")
        upgrade_admin_logs_table()
        return redirect("/admin/logs")
    except Exception as e:
        con.close()
        return f"Error loading logs: {e}"
        
    return render_template("admin_logs.html", logs=logs)


@app.route("/admin/migrate_db")
def admin_migrate_db():
    if session.get("role") != "admin":
        return redirect("/")
    
    try:
        run_migrations()
        return "Migrations completed successfully. <a href='/admin/logs'>Go to Logs</a>"
    except Exception as e:
        return f"Migration failed: {e}"


@app.route("/admin/update_deduction/<int:stock_id>", methods=["POST"])
def admin_update_deduction(stock_id):
    if session.get("role") != "admin":
        return redirect("/")

    deduction = request.form.get("deduction", 0)

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE miller_stock
        SET deduction=%s
        WHERE id=%s
    """, (deduction, stock_id))

    con.commit()
    con.close()

    log_activity("update_deduction", stock_id, f"Stock {stock_id} deduction updated to {deduction}")
    return redirect("/admin/stock")
    
@app.route("/admin/approve_user/<int:id>")
def approve_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='approved' WHERE id=%s", (id,))
    con.commit()
    con.close()
    log_activity("approve_user", id, f"User {id} approved")
    return redirect("/admin/users")
@app.route("/admin/block_user/<int:id>")
def block_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='blocked' WHERE id=%s", (id,))
    con.commit()
    con.close()
    log_activity("block_user", id, f"User {id} blocked")
    return redirect("/admin/users")

@app.route("/admin/unblock_user/<int:id>")
def unblock_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='approved' WHERE id=%s", (id,))
    con.commit()
    con.close()
    log_activity("unblock_user", id, f"User {id} unblocked")
    return redirect("/admin/users")

@app.route("/admin/reject_user/<int:id>")
def reject_user(id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE users SET status='rejected' WHERE id=%s", (id,))
    con.commit()
    con.close()
    log_activity("reject_user", id, f"User {id} rejected")
    return redirect("/admin/users")
    
@app.route("/admin/miller/<int:miller_id>")
def admin_view_miller(miller_id):
    if session.get("role") != "admin":
        return redirect("/")

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT u.name, u.email, p.mill_name, p.owner_phone, p.address, p.gst_doc, p.mandi_doc, p.gst_number, p.mandi_number, p.other_doc, u.id, p.created_at
        FROM users u
        LEFT JOIN miller_profiles p ON u.id = p.miller_id
        WHERE u.id=%s
    """, (miller_id,))
    miller = cur.fetchone()

    con.close()
    return render_template("admin_profile_view.html", profile=miller, member_type="miller")
    
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
        WHERE id=%s
    """, (id,))

    con.commit()
    con.close()
    log_activity("approve_booking", id, f"Booking {id} approved")
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
        WHERE id=%s
    """, (id,))

    con.commit()
    con.close()
    log_activity("decline_booking", id, f"Booking {id} declined")
    return redirect("/admin/bookings")

@app.route("/admin/update_truck/<int:invoice_id>", methods=["POST"])
def admin_update_truck(invoice_id):
    """Admin update truck details (Freight & Debit Note)."""
    if session.get("role") != "admin":
        return redirect("/")

    qc_freight = request.form.get("qc_freight")
    truck_number = request.form.get("truck_number")
    loaded_qty = request.form.get("loaded_qty")
    
    qc_weight = request.form.get("qc_weight")
    qc_moisture = request.form.get("qc_moisture")
    qc_broken = request.form.get("qc_broken")
    qc_karda = request.form.get("qc_karda")
    qc_oil = request.form.get("qc_oil")
    qc_mitti = request.form.get("qc_mitti")
    qc_ssa = request.form.get("qc_ssa")
    qc_claim = request.form.get("qc_claim")
    qc_freight = request.form.get("qc_freight")
    qc_remarks = request.form.get("qc_remarks")

    def safe_float(val):
        try:
            return float(val) if val not in (None, "") else None
        except ValueError:
            return None

    loaded_qty_val = safe_float(loaded_qty)
    qc_weight_val = safe_float(qc_weight)
    qc_moisture_val = safe_float(qc_moisture)
    qc_broken_val = safe_float(qc_broken)
    qc_karda_val = safe_float(qc_karda)
    qc_oil_val = safe_float(qc_oil)
    qc_mitti_val = safe_float(qc_mitti)
    qc_ssa_val = safe_float(qc_ssa)
    qc_claim_val = safe_float(qc_claim)
    qc_freight_val = safe_float(qc_freight)

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        UPDATE loading_invoices
        SET truck_number=%s,
            loaded_qty=%s,
            qc_weight=%s,
            qc_moisture=%s,
            qc_broken=%s,
            qc_karda=%s,
            qc_oil=%s,
            qc_mitti=%s,
            qc_ssa=%s,
            qc_claim=%s,
            qc_freight=%s,
            qc_remarks=%s,
            qc_status='verified'
        WHERE id=%s
    """, (truck_number, loaded_qty_val, qc_weight_val, qc_moisture_val, 
          qc_broken_val, qc_karda_val, qc_oil_val, qc_mitti_val, qc_ssa_val, qc_claim_val,
          qc_freight_val, qc_remarks, invoice_id))

    con.commit()
    con.close()

    log_activity("update_truck", invoice_id, f"Truck details updated for Invoice {invoice_id}")
    flash("Truck details updated.", "success")
    return redirect(request.referrer or "/admin/bookings")






if __name__ == "__main__":
    init_db()
    try:
        run_migrations()
    except Exception as e:
        logger.warning(f"Migration warning: {e}")
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
