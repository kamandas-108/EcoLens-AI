import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "eco-lens-secret")
# Allow CORS for frontend (Adjust origins as needed for production)
CORS(app, supports_credentials=True)

# Configure Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

# Database Setup
def get_db_connection():
    conn = psycopg2.connect(
        os.getenv("NEON_DB_STRING"),
        cursor_factory=RealDictCursor
    )
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            eco_score INT DEFAULT 0,
            items_recycled INT DEFAULT 0,
            co2_saved INT DEFAULT 0,
            plastic_saved INT DEFAULT 0
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

# Initialize DB on startup
try:
    init_db()
except Exception as e:
    print(f"Database init error: {e}")

# --- Authentication Routes ---
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    username, password = data.get('username'), data.get('password')
    
    if not username or not password:
        return jsonify({"error": "Missing fields"}), 400
        
    hashed_pw = generate_password_hash(password)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
            (username, hashed_pw)
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message": "User created successfully"}), 201
    except psycopg2.IntegrityError:
        return jsonify({"error": "Username already exists"}), 409

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username, password = data.get('username'), data.get('password')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    conn.close()
    
    if user and check_password_hash(user['password_hash'], password):
        # We will use simple token return for stateless API
        return jsonify({"message": "Logged in", "user_id": user['id'], "username": user['username']}), 200
    
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, eco_score, items_recycled, co2_saved, plastic_saved FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    conn.close()
    
    if user:
        # Determine Rank
        rank = "Bronze"
        if user['eco_score'] > 50: rank = "Silver"
        if user['eco_score'] > 150: rank = "Gold"
        if user['eco_score'] > 500: rank = "Planet Hero"
        user['rank'] = rank
        return jsonify(user), 200
    return jsonify({"error": "User not found"}), 404

# --- AI Core Routes ---
@app.route('/api/analyze', methods=['POST'])
def analyze_waste():
    if 'image' not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    file = request.files['image']
    user_id = request.form.get('user_id')
    img = Image.open(file.stream)
    
    prompt = """
    Analyze this waste item. Return ONLY a valid JSON object (without markdown or code blocks) with this exact structure:
    {
      "type": "Material type (e.g., PET Plastic)",
      "confidence": "Percentage (e.g., 96%)",
      "category": "Broad category (e.g., Plastic, Paper)",
      "recyclable": true or false,
      "disposal_steps": ["Step 1", "Step 2"],
      "co2_saved_grams": integer estimate of CO2 saved,
      "difficulty": "Easy", "Moderate", or "Difficult",
      "score_awarded": integer (e.g., 10),
      "reuse_ideas": ["Idea 1", "Idea 2"]
    }
    """
    
    try:
        response = model.generate_content([prompt, img])
        result_text = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(result_text)
        
        # Update user stats if logged in
        if user_id:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE users 
                SET eco_score = eco_score + %s,
                    items_recycled = items_recycled + 1,
                    co2_saved = co2_saved + %s,
                    plastic_saved = plastic_saved + %s
                WHERE id = %s
            """, (
                data.get('score_awarded', 0), 
                data.get('co2_saved_grams', 0),
                1 if 'Plastic' in data.get('category', '') else 0,
                user_id
            ))
            conn.commit()
            conn.close()
            
        return jsonify(data), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chat', methods=['POST'])
def ai_chat():
    data = request.json
    user_message = data.get('message', '')
    waste_context = data.get('context', 'waste recycling')
    
    prompt = f"Context: The user is asking about {waste_context}. Question: {user_message}. Be brief, helpful, and eco-friendly."
    try:
        response = model.generate_content(prompt)
        return jsonify({"reply": response.text}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Use host='0.0.0.0' for deployment
    app.run(debug=True, host='0.0.0.0', port=5000)
