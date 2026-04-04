from flask import Flask, render_template, request, redirect, session, jsonify
import random
import requests
from datetime import datetime
import sqlite3

app = Flask(__name__)
app.secret_key = "secret123"

# -------- EMAILJS CONFIG -------- #
EMAILJS_SERVICE_ID = "service_rgxfs9o"
EMAILJS_TEMPLATE_ID = "template_7m5vyuj"
EMAILJS_USER_ID = "QTYpAJGfLL6Wx5GRt"
EMAILJS_PRIVATE_KEY = "S8ZCy-j38GyIAqvBSFjPU"

otp_store = {}

# -------- SQLITE DATABASE INIT -------- #
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
    
    c.execute("INSERT OR IGNORE INTO users (email, role) VALUES (?, ?)", ('brothersreddy2009@gmail.com', 'student'))
    c.execute("INSERT OR IGNORE INTO users (email, role) VALUES (?, ?)", ('mandaraju766@gmail.com', 'student'))
    c.execute("INSERT OR IGNORE INTO users (email, role) VALUES (?, ?)", ('mandasriramachandraraghavaredd@gmail.com', 'manager')) 
    
    conn.commit()
    conn.close()

init_db()

# -------- OTP FUNCTION -------- #
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
    except Exception as e:
        print("Email Error:", e)
        return False

def generate_otp():
    return str(random.randint(1000, 9999))

# -------- ROUTES -------- #
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login/<role>', methods=['GET', 'POST'])
def login(role):
    if request.method == 'POST':
        email = request.form['email']
        
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=? AND role=?", (email, role)).fetchone()
        conn.close()

        if not user:
            return render_template('login.html', step="enter", role=role, error="Email not registered.")

        otp = generate_otp()
        otp_store[email] = otp

        if send_otp_email(email, otp):
            return render_template('login.html', step="verify", email=email, role=role)
        else:
            return render_template('login.html', step="enter", role=role, error="Failed to send OTP.")

    return render_template('login.html', step="enter", role=role)

@app.route('/verify', methods=['POST'])
def verify():
    email = request.form['email']
    otp = request.form['otp']
    role = request.form['role']

    if otp_store.get(email) == otp:
        if role == 'student':
            session['user'] = email
            return redirect('/dashboard')
        else:
            session['manager'] = email
            return redirect('/scanner')

    return render_template('login.html', step="verify", email=email, role=role, error="Invalid OTP.")

# -------- STUDENT DASHBOARD -------- #
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/')
    return render_template('dashboard.html', email=session['user'])

@app.route('/generate-qr/<event>')
def generate_qr(event):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    current_hour = datetime.now().hour
    if event == 'food' and current_hour < 13:
        return jsonify({"error": "Unlocks at 1:00 PM"}), 403
    if event == 'dj' and current_hour < 17:
        return jsonify({"error": "Unlocks at 5:00 PM"}), 403

    email = session['user']
    
    # Static token mapping 1 ticket per user per event
    token = f"{email}-{event}"
    
    conn = get_db()
    existing = conn.execute("SELECT status FROM qr_codes WHERE token=?", (token,)).fetchone()
    
    # Conflict Resolution: Ensure one-time generation/use
    if not existing:
        conn.execute("INSERT INTO qr_codes (token, email, event, status, created_at) VALUES (?, ?, ?, ?, ?)", 
                     (token, email, event, "unused", datetime.now()))
        conn.commit()
    elif existing['status'] == 'used':
        conn.close()
        return jsonify({"error": "Ticket already used. Access Denied."})
        
    conn.close()
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={token}"
    return jsonify({"qr": qr_url})

# -------- MANAGER SCANNER -------- #
@app.route('/scanner')
def scanner():
    if 'manager' not in session:
        return redirect('/')
    return render_template('scanner.html')

@app.route('/validate', methods=['POST'])
def validate():
    token = request.json['token']
    
    conn = get_db()
    qr_data = conn.execute("SELECT * FROM qr_codes WHERE token=?", (token,)).fetchone()
    
    if not qr_data:
        conn.close()
        return jsonify({"status": "Invalid QR"})

    # MAJOR CONFLICT RESOLVED: Blocks counterfeiting and reveals who shared the pass
    if qr_data['status'] == 'used':
        conn.close()
        return jsonify({"status": "Already Used", "student": qr_data['email']})

    # Mark as used permanently
    conn.execute("UPDATE qr_codes SET status='used' WHERE token=?", (token,))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "Accepted", "student": qr_data['email']})

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
