from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import PyPDF2
import datetime

app = Flask(__name__)
app.config['SECRET_KEY']              = 'campusprint2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

db            = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'jpg', 'jpeg', 'png'}

# ── Subscription Plans ───────────────────────────
PLANS = {
    'basic'   : {'price': 799,  'bw': 150, 'color': 30},
    'standard': {'price': 1500, 'bw': 350, 'color': 100},
    'premium' : {'price': 2499, 'bw': 700, 'color': 250},
}

# ── Database Models ──────────────────────────────
class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100))
    email         = db.Column(db.String(100), unique=True)
    phone         = db.Column(db.String(15))
    password_hash = db.Column(db.String(200))
    bw_credits    = db.Column(db.Integer, default=0)
    color_credits = db.Column(db.Integer, default=0)
    plan          = db.Column(db.String(20), default='none')
    sub_end       = db.Column(db.DateTime)

    def set_password(self, p):
        self.password_hash = generate_password_hash(p)

    def check_password(self, p):
        return check_password_hash(self.password_hash, p)

    def sub_active(self):
        if not self.sub_end:
            return False
        return datetime.datetime.utcnow() < self.sub_end

class Order(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'))
    filename   = db.Column(db.String(200))
    pages      = db.Column(db.Integer)
    print_type = db.Column(db.String(10))
    copies     = db.Column(db.Integer)
    amount     = db.Column(db.Float)
    paid_by    = db.Column(db.String(20))
    status     = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))

# ── Helpers ──────────────────────────────────────
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED

def count_pages(filepath):
    ext = filepath.rsplit('.', 1)[1].lower()
    if ext == 'pdf':
        with open(filepath, 'rb') as f:
            return len(PyPDF2.PdfReader(f).pages)
    return 1

def calculate_price(pages, print_type, copies):
    rate = 3 if print_type == 'bw' else 8
    return pages * rate * copies

# ── Routes ───────────────────────────────────────
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name, email = request.form['name'], request.form['email']
        phone, pw   = request.form['phone'], request.form['password']
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already registered!')
        u = User(name=name, email=email, phone=phone)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        login_user(u)
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form['email']).first()
        if u and u.check_password(request.form['password']):
            login_user(u)
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Wrong email or password!')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    orders = Order.query.filter_by(user_id=current_user.id)\
             .order_by(Order.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', user=current_user, orders=orders)

@app.route('/subscribe')
@login_required
def subscribe():
    return render_template('subscribe.html')

@app.route('/activate/<plan>')
@login_required
def activate(plan):
    if plan not in PLANS:
        return redirect(url_for('subscribe'))
    p = PLANS[plan]
    current_user.plan          = plan
    current_user.bw_credits    += p['bw']
    current_user.color_credits += p['color']
    current_user.sub_end       = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/upload', methods=['GET','POST'])
@login_required
def upload():
    if request.method == 'POST':
        file       = request.files['file']
        print_type = request.form.get('print_type', 'bw')
        copies     = int(request.form.get('copies', 1))

        if file and allowed_file(file.filename):
            filepath = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(filepath)
            pages = count_pages(filepath)
            price = calculate_price(pages, print_type, copies)
            total_pages = pages * copies

            # Check if user has enough credits
            has_credits = False
            if print_type == 'bw' and current_user.bw_credits >= total_pages:
                has_credits = True
            elif print_type == 'color' and current_user.color_credits >= total_pages:
                has_credits = True

            return render_template('result.html',
                filename=file.filename, pages=pages,
                print_type=print_type, copies=copies,
                price=price, has_credits=has_credits,
                filepath=filepath)

        return '<h2>Invalid file type!</h2>'
    return render_template('upload.html')

@app.route('/use-credits', methods=['POST'])
@login_required
def use_credits():
    filename   = request.form['filename']
    print_type = request.form['print_type']
    pages      = int(request.form['pages'])
    copies     = int(request.form['copies'])
    filepath   = request.form['filepath']
    total      = pages * copies

    # Deduct credits
    if print_type == 'bw':
        current_user.bw_credits -= total
    else:
        current_user.color_credits -= total

    # Save order
    order = Order(
        user_id    = current_user.id,
        filename   = filename,
        pages      = pages,
        print_type = print_type,
        copies     = copies,
        amount     = 0,
        paid_by    = 'credits',
        status     = 'printing'
    )
    db.session.add(order)
    db.session.commit()

    return render_template('success.html', order=order)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print('Database ready!')
    app.run(debug=True)