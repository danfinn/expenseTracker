# app.py (With Edit Functionality)
import os
import re
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from PIL import Image
import pytesseract
import requests

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

# --- Master Data Lists ---
CURRENCIES = ['USD', 'MXN', 'EUR', 'GBP', 'JPY', 'CAD']
CATEGORIES = ['Food', 'Travel', 'Transportation', 'Housing', 'Utilities', 'Health', 'Entertainment', 'Shopping', 'Personal Care', 'Misc']

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
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='Misc')
    expense_date = db.Column(db.Date, nullable=False, default=date.today)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_amount = db.Column(db.Float, nullable=False)
    original_currency = db.Column(db.String(3), nullable=False)

with app.app_context():
    db.create_all()

# --- User Loader ---
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- Helper Functions ---
def convert_to_usd(amount, from_currency):
    if from_currency == 'USD':
        return amount
    try:
        api_url = f"https://open.er-api.com/v6/latest/{from_currency}"
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()
        if data.get("result") == "success":
            usd_rate = data['rates']['USD']
            return amount * usd_rate
        else:
            flash(f"Warning: Could not get exchange rate for {from_currency}. Amount saved without conversion.")
            return amount
    except requests.exceptions.RequestException as e:
        flash(f"Warning: Could not connect to currency API. Amount saved without conversion. Error: {e}")
        return amount

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
        login_user(new_user)
        flash('Registration successful! You are now logged in.')
        return redirect(url_for('index'))
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
        original_amount = float(request.form['amount'])
        original_currency = request.form['currency']
        usd_amount = convert_to_usd(original_amount, original_currency)
        new_expense = Expense(
            description=request.form['description'],
            amount=usd_amount,
            category=request.form['category'],
            expense_date=string_to_date(request.form['expense_date']),
            owner=current_user,
            original_amount=original_amount,
            original_currency=original_currency
        )
        db.session.add(new_expense)
        db.session.commit()
        return redirect(url_for('index'))

    else:
        today = date.today()
        monthly_total_query = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            extract('month', Expense.expense_date) == today.month,
            extract('year', Expense.expense_date) == today.year
        )
        monthly_total = monthly_total_query.scalar() or 0.0

        expenses = db.session.execute(db.select(Expense).where(Expense.user_id == current_user.id).order_by(Expense.expense_date.desc(), Expense.id.desc())).scalars().all()

        return render_template(
            'index.html',
            expenses=expenses,
            today=today.isoformat(),
            currencies=CURRENCIES,
            categories=CATEGORIES,
            monthly_total=monthly_total,
            current_month_name=today.strftime('%B')
        )

# --- NEW EDIT ROUTE ---
@app.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    expense_to_edit = db.get_or_404(Expense, expense_id)
    # Security check: ensure the expense belongs to the current user
    if expense_to_edit.owner != current_user:
        return "Forbidden", 403

    if request.method == 'POST':
        # Get updated data from the form
        original_amount = float(request.form['amount'])
        original_currency = request.form['currency']

        # Recalculate USD amount
        usd_amount = convert_to_usd(original_amount, original_currency)

        # Update the expense object with the new data
        expense_to_edit.description = request.form['description']
        expense_to_edit.expense_date = string_to_date(request.form['expense_date'])
        expense_to_edit.category = request.form['category']
        expense_to_edit.original_amount = original_amount
        expense_to_edit.original_currency = original_currency
        expense_to_edit.amount = usd_amount

        db.session.commit() # Commit the changes to the database
        flash('Expense updated successfully!')
        return redirect(url_for('index'))

    else: # GET request: show the edit form pre-filled with data
        return render_template(
            'edit_expense.html',
            expense=expense_to_edit,
            currencies=CURRENCIES,
            categories=CATEGORIES
        )

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
        original_amount = float(request.form['amount'])
        original_currency = request.form['currency']
        usd_amount = convert_to_usd(original_amount, original_currency)
        final_expense = Expense(
            description=request.form['description'],
            amount=usd_amount,
            category=request.form['category'],
            expense_date=string_to_date(request.form['expense_date']),
            owner=current_user,
            original_amount=original_amount,
            original_currency=original_currency
        )
        db.session.add(final_expense)
        db.session.commit()
        return redirect(url_for('index'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    try:
        raw_text = pytesseract.image_to_string(Image.open(filepath), lang='eng+spa')
    except Exception as e:
        flash(f"OCR failed: {e}")
        raw_text = "OCR failed. Please enter details manually."

    description, amount, expense_date = parse_receipt(raw_text)

    return render_template('review.html',
        description=description,
        amount=amount,
        expense_date=expense_date.isoformat(),
        raw_text=raw_text,
        currencies=CURRENCIES,
        categories=CATEGORIES
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

