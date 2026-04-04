import os
import random
import sqlite3
import requests
import jwt
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, session, jsonify

app = Flask(__name__)

# -------- CONFIG & SECRETS -------- #
# Set these in your Render Environment Variables!
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_key_change_this")
JWT_SECRET = os.environ.get("JWT_SECRET", "jwt_secret_change_this")

# Email Config (ROTATE YOUR PRIVATE KEY IN EMAILJS DASHBOARD!)
EMAILJS_SERVICE_ID = os.environ.get("EMAILJS_SERVICE_ID", "service_rgxfs9o")
EMAILJS_TEMPLATE_ID = os.environ.get("EMAILJS_TEMPLATE_ID", "template_7m5vyuj")
EMAILJS_USER_ID = os.environ.get("EMAILJS_USER_ID", "QTYpAJGfLL6Wx5GRt")
EMAILJS_PRIVATE_KEY = os.environ.get("EMAILJS_PRIVATE_KEY", "YOUR_NEW_PRIVATE_KEY")

# Change this to your local timezone (e.g., "Asia/Kolkata", "America/New_York", "Europe/London")
# Render uses UTC by default, which messes up your hour-based event logic.
LOCAL_TZ = ZoneInfo(os.environ.get("TZ", "Asia/Kolkata"))

# -------- DATABASE -------- #
def get_db():
    # check_same_thread=False is required for Gunicorn workers
    conn = sqlite3.connect('database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable Write-Ahead Logging for better concurrent performance
    conn.execute('pragma journal_mode=wal')
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        role TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS qr_codes (
        token TEXT PRIMARY KEY,
        email TEXT,
        event TEXT,
        status TEXT,
        created_at TIMESTAMP
    )''')

    # New Table to securely store OTPs across server workers
    c.execute('''CREATE TABLE IF NOT EXISTS otps (
        email TEXT PRIMARY KEY,
        otp TEXT,
        created_at TIMESTAMP
    )''')

    # Sample users
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", ('student@gmail.com', 'student'))
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", ('manager@gmail.com', 'manager'))

    conn.commit()
    conn.close()

init_db()

# -------- OTP LOGIC -------- #
def generate_otp():
    return str(random.randint(1000, 9999))

def save_otp(email, otp):
    conn = get_db()
    # REPLACE INTO updates the OTP if the user requests a new one
    conn.execute("REPLACE INTO otps (email, otp, created_at) VALUES (?, ?, ?)", 
                 (email, otp, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()

def send_otp_email(email, otp):
    try:
        payload = {
            "service_id": EMAILJS_SERVICE_ID,
            "template_id": EMAILJS_TEMPLATE_ID,
            "user_id": EMAILJS_USER_ID,
            "accessToken": EMAILJS_PRIVATE_KEY,
            "template_params": {"to_email": email, "otp": otp}
        }
        # Added timeout=5 to prevent server freeze if EmailJS is slow
        response = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"Email error: {e}")
        return False

# -------- AUTH HELPERS -------- #
def login_user(email, role):
    session['email'] = email
    session['role'] = role

def is_student():
    return session.get('role') == 'student'

def is_manager():
    return session.get('role') == 'manager'

# -------- ROUTES -------- #
@app.route('/')
def index():
    return render_template('index.html')

# -------- LOGIN -------- #
@app.route('/login/<role>', methods=['GET', 'POST'])
def login(role):
    if request.method == 'POST':
        email = request.form['email']

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=? AND role=?", (email, role)).fetchone()
        conn.close()

        if not user:
            return render_template('login.html', step="enter", role=role, error="User not found")

        otp = generate_otp()
        save_otp(email, otp) # Saves to Database instead of Memory

        if send_otp_email(email, otp):
            return render_template('login.html', step="verify", email=email, role=role)
        else:
            return render_template('login.html', step="enter", role=role, error="Failed to send OTP. Try again.")

    return render_template('login.html', step="enter", role=role)

# -------- VERIFY OTP -------- #
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'GET':
        return redirect('/')

    email = request.form['email']
    otp = request.form['otp']
    role = request.form['role']

    conn = get_db()
    stored_otp_record = conn.execute("SELECT otp FROM otps WHERE email=?", (email,)).fetchone()

    if stored_otp_record and stored_otp_record['otp'] == otp:
        # Clear the OTP from DB after successful use
        conn.execute("DELETE FROM otps WHERE email=?", (email,))
        conn.commit()
        conn.close()

        login_user(email, role)

        if role == 'student':
            return redirect('/dashboard')
        else:
            return redirect('/scanner')

    conn.close()
    return render_template('login.html', step="verify", email=email, role=role, error="Invalid OTP")

# -------- DASHBOARD -------- #
@app.route('/dashboard')
def dashboard():
    if not is_student():
        return redirect('/')
    return render_template('dashboard.html', email=session['email'])

# -------- GENERATE QR -------- #
@app.route('/generate-qr/<event>')
def generate_qr(event):
    if not is_student():
        return jsonify({"error": "Unauthorized"}), 401

    email = session['email']
    
    # Use Local Timezone to enforce rules correctly
    current_time = datetime.now(LOCAL_TZ)
    current_hour = current_time.hour

    # 🍱 Lunch: allowed till 3 PM
    if event == 'food' and current_hour >= 15:
        return jsonify({"error": "Lunch QR closed after 3 PM"}), 403

    # 🎧 DJ: only between 5 PM and 6 PM
    if event == 'dj' and not (17 <= current_hour < 18):
        return jsonify({"error": "DJ QR only available between 5 PM and 6 PM"}), 403

    conn = get_db()

    # CHECK IF USER ALREADY HAS A QR FOR THIS EVENT to prevent infinite generation
    existing = conn.execute("SELECT token FROM qr_codes WHERE email=? AND event=?", (email, event)).fetchone()

    if existing:
        token = existing['token']
    else:
        # Create secure JWT token if one doesn't exist
        payload = {
            "email": email,
            "event": event,
            "exp": datetime.utcnow() + timedelta(hours=6),
            "iat": datetime.utcnow()
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        
        conn.execute(
            "INSERT INTO qr_codes (token, email, event, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, email, event, "unused", datetime.now(timezone.utc))
        )
        conn.commit()

    conn.close()

    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={token}"
    return jsonify({"qr": qr_url})

# -------- SCANNER -------- #
@app.route('/scanner')
def scanner():
    if not is_manager():
        return redirect('/')
    return render_template('scanner.html')

# -------- VALIDATE QR -------- #
@app.route('/validate', methods=['POST'])
def validate():
    if not is_manager():
        return jsonify({"error": "Unauthorized"}), 401

    token = request.json.get('token')

    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return jsonify({"status": "Expired QR"})
    except jwt.InvalidTokenError:
        return jsonify({"status": "Invalid QR"})

    conn = get_db()
    qr_data = conn.execute("SELECT * FROM qr_codes WHERE token=?", (token,)).fetchone()

    if not qr_data:
        conn.close()
        return jsonify({"status": "Not Found"})

    if qr_data['status'] == 'used':
        conn.close()
        return jsonify({
            "status": "Already Used",
            "email": qr_data['email']
        })

    # mark used
    conn.execute("UPDATE qr_codes SET status='used' WHERE token=?", (token,))
    conn.commit()
    conn.close()

    return jsonify({
        "status": "Accepted",
        "email": qr_data['email'],
        "event": qr_data['event'],
        "time": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    })

# -------- LOGOUT -------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# -------- RUN -------- #
if __name__ == '__main__':
    # Removed the duplicate if __name__ block
    app.run(debug=True)
