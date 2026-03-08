# app.py
import os
import re
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import easyocr

# --- Initialization & Config ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-very-secret-key-for-a-stable-app'
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- Extensions Setup ---
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- OCR LAZY INITIALIZATION ---
ocr_reader = None

def get_ocr_reader():
    """
    Creates and returns a single instance of the EasyOCR reader based on
    the OCR_LANGUAGE environment variable.
    """
    global ocr_reader
    if ocr_reader is None:
        # Read the desired language from the environment, defaulting to 'en'
        lang_code = os.environ.get('OCR_LANGUAGE', 'en').lower()

        print(f"Initializing EasyOCR for the first time with language: '{lang_code}'...")
        # Now loading only ONE language model to conserve memory
        ocr_reader = easyocr.Reader([lang_code])
        print("EasyOCR Initialized.")
    return ocr_reader

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    expenses = db.relationship('Expense', backref='owner', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

with app.app_context():
    db.create_all()

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- Helper Functions ---
def parse_receipt(text):
    lines = text.split('\n')
    description = "Unknown"
    for line in lines:
        if line.strip():
            description = line.strip()
            break
    prices = re.findall(r'\d+\.\d{2}', text)
    amount = max([float(p) for p in prices]) if prices else 0.0
    expense_date = date.today()
    date_pattern = r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    found_dates = re.findall(date_pattern, text)
    for d_str in found_dates:
        try:
            dt_object = datetime.strptime(d_str.replace('-', '/'), '%m/%d/%y').date()
            expense_date = dt_object
            break
        except ValueError:
            try:
                dt_object = datetime.strptime(d_str.replace('-', '/'), '%m/%d/%Y').date()
                expense_date = dt_object
                break
            except ValueError:
                continue
    return description, amount, expense_date

def string_to_date(date_string):
    return datetime.strptime(date_string, '%Y-%m-%d').date()

# --- Public Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        user = db.session.scalar(db.select(User).where(User.username == request.form['username']))
        if user is None or not user.check_password(request.form['password']):
            flash('Invalid username or password')
            return redirect(url_for('login'))
        login_user(user, remember=True)
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        if db.session.scalar(db.select(User).where(User.username == request.form['username'])):
            flash('Username already exists')
            return redirect(url_for('register'))
        new_user = User(username=request.form['username'])
        new_user.set_password(request.form['password'])
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please log in.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Main Protected Application Routes ---
@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        new_expense = Expense(
            description=request.form['description'],
            amount=float(request.form['amount']),
            expense_date=string_to_date(request.form['expense_date']),
            owner=current_user
        )
        db.session.add(new_expense)
        db.session.commit()
        return redirect(url_for('index'))
    else: # GET request
        expenses = db.session.execute(db.select(Expense).where(Expense.user_id == current_user.id).order_by(Expense.expense_date.desc(), Expense.id.desc())).scalars().all()
        return render_template('index.html', expenses=expenses, today=date.today().isoformat())

@app.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense_to_delete = db.get_or_404(Expense, expense_id)
    if expense_to_delete.owner != current_user:
        return "Forbidden", 403
    db.session.delete(expense_to_delete)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_receipt():
    if request.method == 'POST':
        if 'receipt' not in request.files or request.files['receipt'].filename == '':
            return redirect(request.url)
        file = request.files['receipt']
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            return redirect(url_for('review_scan', filename=filename))
    return render_template('upload.html')

@app.route('/review/<filename>', methods=['GET', 'POST'])
@login_required
def review_scan(filename):
    if request.method == 'POST':
        final_expense = Expense(
            description=request.form['description'],
            amount=float(request.form['amount']),
            expense_date=string_to_date(request.form['expense_date']),
            owner=current_user
        )
        db.session.add(final_expense)
        db.session.commit()
        return redirect(url_for('index'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    reader = get_ocr_reader()
    result = reader.readtext(filepath, detail=0, paragraph=True)
    raw_text = '\n'.join(result)

    description, amount, expense_date = parse_receipt(raw_text)

    return render_template('review.html',
        description=description,
        amount=amount,
        expense_date=expense_date.isoformat(),
        raw_text=raw_text
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

