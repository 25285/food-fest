from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
from datetime import datetime, timedelta
import jwt
import uuid
import requests
import random

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this"
JWT_SECRET = "jwt_secret_change_this"

# -------- EMAIL CONFIG -------- #
EMAILJS_SERVICE_ID = "service_rgxfs9o"
EMAILJS_TEMPLATE_ID = "template_7m5vyuj"
EMAILJS_USER_ID = "QTYpAJGfLL6Wx5GRt"
EMAILJS_PRIVATE_KEY = "S8ZCy-j38GyIAqvBSFjPU"

otp_store = {}

# -------- DATABASE -------- #
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
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

    # Sample users
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", ('student@gmail.com', 'student'))
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", ('manager@gmail.com', 'manager'))

    conn.commit()
    conn.close()

init_db()

# -------- OTP -------- #
def generate_otp():
    return str(random.randint(1000, 9999))

def send_otp_email(email, otp):
    try:
        payload = {
            "service_id": EMAILJS_SERVICE_ID,
            "template_id": EMAILJS_TEMPLATE_ID,
            "user_id": EMAILJS_USER_ID,
            "accessToken": EMAILJS_PRIVATE_KEY,
            "template_params": {"to_email": email, "otp": otp}
        }
        response = requests.post("https://api.emailjs.com/api/v1.0/email/send", json=payload)
        return response.status_code == 200
    except:
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
        otp_store[email] = otp

        if send_otp_email(email, otp):
            return render_template('login.html', step="verify", email=email, role=role)
        else:
            return render_template('login.html', step="enter", role=role, error="OTP failed")

    return render_template('login.html', step="enter", role=role)

# -------- VERIFY OTP -------- #
@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if request.method == 'GET':
        return "Use POST to verify OTP"

    email = request.form['email']
    otp = request.form['otp']
    role = request.form['role']

    if otp_store.get(email) == otp:
        login_user(email, role)

        if role == 'student':
            return redirect('/dashboard')
        else:
            return redirect('/scanner')

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
    current_hour = datetime.now().hour

    # 🍱 Lunch
    if event == 'food' and current_hour >= 15:
        return jsonify({"error": "Lunch QR closed after 3 PM"}), 403

    # 🎧 DJ
    if event == 'dj' and not (17 <= current_hour < 18):
        return jsonify({"error": "DJ QR only available between 5 PM and 6 PM"}), 403

    payload = {
        "email": email,
        "event": event,
        "exp": datetime.utcnow() + timedelta(hours=6),
        "iat": datetime.utcnow()
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    conn = get_db()

    existing = conn.execute(
    "SELECT token FROM qr_codes WHERE email=? AND event=? AND status='unused'",
    (email, event)
    ).fetchone()

    if existing:
        token = existing['token']  # reuse same QR
    else:
        token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
        conn.execute(
            "INSERT INTO qr_codes VALUES (?, ?, ?, ?, ?)",
            (token, email, event, "unused", datetime.now())
        )
    conn.commit()

    if not existing:
        conn.execute(
            "INSERT INTO qr_codes VALUES (?, ?, ?, ?, ?)",
            (token, email, event, "unused", datetime.now())
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
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
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
        "time": str(datetime.now())
    })

# -------- LOGOUT -------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# -------- RUN -------- #
if __name__ == '__main__':
    app.run(debug=True)
