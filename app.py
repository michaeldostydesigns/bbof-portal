import os
import io
import uuid
import base64
import bcrypt
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, render_template, Response
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required,
    get_jwt_identity, get_jwt, set_access_cookies, unset_jwt_cookies, decode_token
)
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, disconnect as sio_disconnect
from fpdf import FPDF
from fpdf.enums import XPos, YPos

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"]              = os.environ.get("SECRET_KEY", "bbof-change-me-in-production")
app.config["JWT_SECRET_KEY"]         = os.environ.get("JWT_SECRET", "bbof-jwt-secret-change-in-production")
app.config["JWT_TOKEN_LOCATION"]     = ["cookies"]
app.config["JWT_COOKIE_CSRF_PROTECT"]= False
app.config["JWT_ACCESS_TOKEN_EXPIRES"]= timedelta(days=7)
app.config["SQLALCHEMY_DATABASE_URI"]= os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR,'bbof.db')}")
app.config["SQLALCHEMY_ENGINE_OPTIONS"]= {"connect_args":{"check_same_thread": False}}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"]= False
app.config["MAX_CONTENT_LENGTH"]    = 10 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db       = SQLAlchemy(app)
jwt      = JWTManager(app)
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    logger=False, engineio_logger=False, manage_session=False)

# ── In-memory online presence  {sid: {id, name, profile_image, role}} ──────────
online_users: dict = {}

def get_online_list():
    seen = {}
    for info in online_users.values():
        uid = info["id"]
        if uid not in seen:
            seen[uid] = info
    return list(seen.values())

# ═══════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════

class User(db.Model):
    id           = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name         = db.Column(db.String(200), nullable=False)
    email        = db.Column(db.String(200), unique=True, nullable=False)
    phone        = db.Column(db.String(50))
    password_hash= db.Column(db.String(200), nullable=False)
    role         = db.Column(db.String(20), default="member")
    profile_image= db.Column(db.String(300))
    status       = db.Column(db.String(20), default="active")
    joined_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    payments     = db.relationship("Payment",     backref="user", cascade="all, delete-orphan")
    messages     = db.relationship("ChatMessage", backref="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id":self.id,"name":self.name,"email":self.email,"phone":self.phone,
                "role":self.role,"profile_image":self.profile_image,
                "status":self.status,"joined_at":self.joined_at.isoformat()}


class Payment(db.Model):
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    amount      = db.Column(db.Float, nullable=False)
    for_month   = db.Column(db.String(10), nullable=False)
    note        = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"user_id":self.user_id,
                "member_name":self.user.name if self.user else "",
                "amount":self.amount,"for_month":self.for_month,
                "note":self.note,"recorded_at":self.recorded_at.isoformat()}


class Donation(db.Model):
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    donor_name  = db.Column(db.String(200), nullable=False)
    amount      = db.Column(db.Float, nullable=False)
    source      = db.Column(db.String(100))
    note        = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"donor_name":self.donor_name,"amount":self.amount,
                "source":self.source,"note":self.note,"recorded_at":self.recorded_at.isoformat()}


class Expense(db.Model):
    id          = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title       = db.Column(db.String(300), nullable=False)
    amount      = db.Column(db.Float, nullable=False)
    category    = db.Column(db.String(100))
    note        = db.Column(db.Text)
    recorded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"title":self.title,"amount":self.amount,
                "category":self.category,"note":self.note,"recorded_at":self.recorded_at.isoformat()}


class NewsPost(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title      = db.Column(db.String(300), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    image      = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"title":self.title,"content":self.content,
                "image":self.image,"created_at":self.created_at.isoformat()}


class GalleryImage(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url        = db.Column(db.String(300), nullable=False)
    caption    = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"url":self.url,
                "caption":self.caption,"created_at":self.created_at.isoformat()}


class ChatMessage(db.Model):
    id         = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = db.Column(db.String(36), db.ForeignKey("user.id"), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {"id":self.id,"user_id":self.user_id,
                "user_name": self.user.name if self.user else "Unknown",
                "user_profile_image": self.user.profile_image if self.user else None,
                "content":self.content,"created_at":self.created_at.isoformat()}


class Settings(db.Model):
    id            = db.Column(db.Integer, primary_key=True, default=1)
    lives_impacted= db.Column(db.Integer, default=0)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def save_data_url(data_url, prefix="img"):
    import re
    from PIL import Image as PILImage
    m = re.match(r"^data:(image/(png|jpeg|jpg|gif|webp));base64,(.+)$", data_url, re.DOTALL)
    if not m:
        raise ValueError("Invalid image data")
    ext  = "jpg" if m.group(2) in ("jpeg","jpg") else m.group(2)
    raw  = base64.b64decode(m.group(3))
    if len(raw) > 5*1024*1024:
        raise ValueError("Image too large (max 5MB)")
    img  = PILImage.open(io.BytesIO(raw))
    img.thumbnail((1600,1600))
    fname= f"{prefix}-{uuid.uuid4().hex[:10]}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, fname)
    img.save(path, quality=85)
    return f"/static/uploads/{fname}"


def get_finance_summary():
    total_dues      = db.session.query(db.func.sum(Payment.amount)).scalar()  or 0
    total_donations = db.session.query(db.func.sum(Donation.amount)).scalar() or 0
    total_expenses  = db.session.query(db.func.sum(Expense.amount)).scalar()  or 0
    return {
        "total_dues":      round(total_dues, 2),
        "total_donations": round(total_donations, 2),
        "total_expenses":  round(total_expenses, 2),
        "net_wealth":      round(total_dues + total_donations - total_expenses, 2),
    }


def require_admin():
    claims = get_jwt()
    if claims.get("role") != "admin":
        return jsonify(error="Admins only."), 403
    return None


def fmt_ghs(n):
    return f"GHS {n:,.2f}"


def fmt_dt(dt_val):
    if dt_val is None:
        return "-"
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val)
        except Exception:
            return dt_val
    return dt_val.strftime("%d %b %Y")


# ═══════════════════════════════════════════════════════════════
# PDF GENERATORS
# ═══════════════════════════════════════════════════════════════

FOREST  = (20, 51, 41)
GOLD    = (201, 162, 75)
LIGHT   = (246, 243, 234)
INK     = (26, 26, 26)
SOFT    = (100, 100, 100)
DANGER  = (179, 67, 58)
WHITE   = (255, 255, 255)
ROW_ALT = (245, 242, 234)


class BBOFPDF(FPDF):
    """Base PDF class with B.BOF branding."""

    def header(self):
        logo = os.path.join(BASE_DIR, "static", "logo.png")
        if os.path.exists(logo):
            self.image(logo, x=12, y=8, w=22)
        self.set_font("Helvetica", "B", 17)
        self.set_text_color(*FOREST)
        self.set_xy(38, 9)
        self.cell(0, 8, "BOWRA_B OUTREACH FOUNDATION")
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(*SOFT)
        self.set_xy(38, 17)
        self.cell(0, 6, "Smile With The Needy")
        self.set_draw_color(*FOREST)
        self.set_line_width(0.7)
        self.line(12, 27, 200, 27)
        self.ln(22)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*SOFT)
        ts = datetime.now().strftime("%d %B %Y at %H:%M")
        self.cell(0, 8, f"Generated by B.BOF Portal  ·  {ts}  ·  Page {self.page_no()}", align="C")

    def section_title(self, txt):
        self.set_fill_color(*FOREST)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, f"  {txt}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def kv_row(self, label, value, alt=False):
        if alt:
            self.set_fill_color(*ROW_ALT)
        else:
            self.set_fill_color(*WHITE)
        self.set_text_color(*SOFT)
        self.set_font("Helvetica", "B", 9)
        self.cell(55, 7, label, fill=True)
        self.set_text_color(*INK)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 7, str(value), fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table_header(self, cols):
        """cols: list of (label, width)"""
        self.set_fill_color(*FOREST)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        for label, w in cols:
            self.cell(w, 7, label, border=0, fill=True)
        self.ln()

    def table_row(self, cells, alt=False):
        """cells: list of (text, width)"""
        if alt:
            self.set_fill_color(*ROW_ALT)
        else:
            self.set_fill_color(*WHITE)
        self.set_text_color(*INK)
        self.set_font("Helvetica", "", 9)
        for txt, w in cells:
            self.cell(w, 7, str(txt or "-"), fill=True)
        self.ln()

    def summary_box(self, label, value, accent=False):
        x, y = self.get_x(), self.get_y()
        if accent:
            self.set_fill_color(*GOLD)
            self.set_text_color(*FOREST)
        else:
            self.set_fill_color(*LIGHT)
            self.set_text_color(*FOREST)
        self.rect(x, y, 90, 18, "F")
        self.set_font("Helvetica", "B", 8)
        self.set_xy(x+3, y+2)
        self.cell(84, 5, label.upper())
        self.set_font("Helvetica", "B", 13)
        self.set_xy(x+3, y+8)
        self.cell(84, 8, value)


def generate_member_pdf(user, payments):
    pdf = BBOFPDF()
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    # Report title
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*FOREST)
    pdf.cell(0, 9, "MEMBER FINANCIAL REPORT", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*SOFT)
    pdf.cell(0, 5, f"Prepared on {datetime.now().strftime('%d %B %Y')}", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    # Member info
    pdf.section_title("Member Information")
    pdf.kv_row("Full Name",    user.name,                      alt=False)
    pdf.kv_row("Email",        user.email,                     alt=True)
    pdf.kv_row("Phone",        user.phone or "-",              alt=False)
    pdf.kv_row("Date Joined",  fmt_dt(user.joined_at),         alt=True)
    pdf.kv_row("Status",       user.status.upper(),            alt=False)
    pdf.kv_row("Member ID",    user.id[:12] + "...",             alt=True)
    pdf.ln(6)

    # Financial summary
    total = sum(p.amount for p in payments)
    pdf.section_title("Dues Summary")
    pdf.ln(2)
    x0 = pdf.get_x()
    pdf.summary_box("Total dues paid", fmt_ghs(total), accent=True)
    pdf.set_xy(x0 + 93, pdf.get_y())
    pdf.summary_box("Total payments", str(len(payments)), accent=False)
    pdf.ln(22)

    # Payments table
    pdf.section_title("Payment History")
    if not payments:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*SOFT)
        pdf.cell(0, 10, "No payments have been recorded for this member.", align="C",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        cols = [("Month", 38), ("Amount (GHS)", 44), ("Note", 72), ("Date Recorded", 34)]
        pdf.table_header(cols)
        for i, p in enumerate(payments):
            pdf.table_row([
                (p.for_month, 38),
                (f"{p.amount:,.2f}", 44),
                (p.note or "-", 72),
                (fmt_dt(p.recorded_at), 34),
            ], alt=(i % 2 == 1))

    pdf.ln(4)
    # Totals row
    pdf.set_fill_color(*FOREST)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(154, 8, "TOTAL PAID", fill=True)
    pdf.cell(34, 8, fmt_ghs(total), fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


def generate_all_pdf(summary, members, donations, expenses, settings):
    pdf = BBOFPDF()
    pdf.set_margins(12, 12, 12)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*FOREST)
    pdf.cell(0, 9, "FOUNDATION FINANCIAL REPORT", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*SOFT)
    pdf.cell(0, 5, f"Prepared on {datetime.now().strftime('%d %B %Y')}", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    # Financial summary boxes
    pdf.section_title("Financial Summary")
    pdf.ln(2)
    x0 = pdf.get_x()
    y0 = pdf.get_y()

    boxes = [
        ("Total Dues Collected",    fmt_ghs(summary["total_dues"]),      False),
        ("Total Donations Received",fmt_ghs(summary["total_donations"]), False),
        ("Total Expenses",          fmt_ghs(summary["total_expenses"]),  False),
        ("NET WEALTH",              fmt_ghs(summary["net_wealth"]),      True),
    ]
    for i, (lbl, val, acc) in enumerate(boxes):
        col = i % 2
        row = i // 2
        pdf.set_xy(x0 + col * 95, y0 + row * 22)
        pdf.summary_box(lbl, val, accent=acc)
    pdf.set_y(y0 + 44 + 4)
    pdf.ln(4)

    # Organisation stats
    total_members  = len(members)
    active_members = sum(1 for m in members if m.status == "active")
    lives          = settings.lives_impacted if settings else 0

    pdf.section_title("Organisation Statistics")
    pdf.kv_row("Total Members",   total_members,  alt=False)
    pdf.kv_row("Active Members",  active_members, alt=True)
    pdf.kv_row("Lives Impacted",  f"{lives:,}",   alt=False)
    pdf.ln(6)

    # Donations
    pdf.section_title("Donations Received")
    if not donations:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*SOFT)
        pdf.cell(0, 8, "No donations recorded.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        cols = [("Donor Name", 58), ("Amount (GHS)", 38), ("Source", 38), ("Date", 30), ("Note", 24)]
        pdf.table_header(cols)
        for i, d in enumerate(donations):
            pdf.table_row([
                (d.donor_name, 58), (f"{d.amount:,.2f}", 38),
                (d.source or "-", 38), (fmt_dt(d.recorded_at), 30), (d.note or "-", 24),
            ], alt=(i % 2 == 1))
        # Totals
        don_total = sum(d.amount for d in donations)
        pdf.set_fill_color(*FOREST)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(96, 7, "TOTAL", fill=True)
        pdf.cell(38, 7, f"{don_total:,.2f}", fill=True)
        pdf.cell(54, 7, "", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # Expenses
    pdf.section_title("Expenses Recorded")
    if not expenses:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*SOFT)
        pdf.cell(0, 8, "No expenses recorded.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        cols = [("Title", 64), ("Amount (GHS)", 36), ("Category", 40), ("Date", 30), ("Note", 18)]
        pdf.table_header(cols)
        for i, e in enumerate(expenses):
            pdf.table_row([
                (e.title, 64), (f"{e.amount:,.2f}", 36),
                (e.category or "-", 40), (fmt_dt(e.recorded_at), 30), (e.note or "-", 18),
            ], alt=(i % 2 == 1))
        exp_total = sum(e.amount for e in expenses)
        pdf.set_fill_color(*DANGER)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(100, 7, "TOTAL EXPENSES", fill=True)
        pdf.cell(36, 7, f"{exp_total:,.2f}", fill=True)
        pdf.cell(52, 7, "", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    # Member dues breakdown
    pdf.section_title("Member Dues Breakdown")
    cols = [("Member Name", 70), ("Email", 70), ("Status", 28), ("Total Paid (GHS)", 20)]
    pdf.table_header(cols)
    grand = 0
    for i, m in enumerate(members):
        paid = sum(p.amount for p in m.payments)
        grand += paid
        pdf.table_row([
            (m.name, 70), (m.email, 70),
            (m.status.upper(), 28), (f"{paid:,.2f}", 20),
        ], alt=(i % 2 == 1))
    # Grand total
    pdf.set_fill_color(*GOLD)
    pdf.set_text_color(*FOREST)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(168, 7, "GRAND TOTAL - ALL MEMBERS", fill=True)
    pdf.cell(20, 7, f"{grand:,.2f}", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


# ═══════════════════════════════════════════════════════════════
# SPA ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/")
@app.route("/login")
@app.route("/dashboard")
@app.route("/dashboard/<path:p>")
@app.route("/admin")
@app.route("/admin/<path:p>")
def spa(p=None):
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/api/auth/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""
    user     = User.query.filter_by(email=email).first()
    if not user or user.status == "terminated":
        return jsonify(error="Invalid email or password."), 401
    if not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return jsonify(error="Invalid email or password."), 401
    token = create_access_token(identity=user.id,
                                additional_claims={"role": user.role, "name": user.name})
    resp  = jsonify(user=user.to_dict())
    set_access_cookies(resp, token)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    resp = jsonify(ok=True)
    unset_jwt_cookies(resp)
    return resp


@app.route("/api/me")
@jwt_required(optional=True)
def me():
    uid  = get_jwt_identity()
    if not uid:
        return jsonify(user=None)
    user = User.query.get(uid)
    return jsonify(user=user.to_dict() if user else None)


# ═══════════════════════════════════════════════════════════════
# PUBLIC ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/api/news")
def public_news():
    posts = NewsPost.query.order_by(NewsPost.created_at.desc()).limit(20).all()
    return jsonify(posts=[p.to_dict() for p in posts])


@app.route("/api/gallery")
def public_gallery():
    imgs = GalleryImage.query.order_by(GalleryImage.created_at.desc()).limit(24).all()
    return jsonify(images=[i.to_dict() for i in imgs])


@app.route("/api/settings")
def public_settings():
    s = Settings.query.get(1)
    if not s:
        s = Settings(id=1, lives_impacted=0); db.session.add(s); db.session.commit()
    return jsonify(settings={"lives_impacted": s.lives_impacted})


# ═══════════════════════════════════════════════════════════════
# MEMBER ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/api/finance")
@jwt_required()
def finance():
    return jsonify(summary=get_finance_summary())


@app.route("/api/payments/mine")
@jwt_required()
def my_payments():
    uid      = get_jwt_identity()
    payments = Payment.query.filter_by(user_id=uid).order_by(Payment.recorded_at.desc()).all()
    return jsonify(payments=[p.to_dict() for p in payments])


@app.route("/api/members/count")
@jwt_required()
def members_count():
    total  = User.query.filter_by(role="member").count()
    active = User.query.filter_by(role="member", status="active").count()
    return jsonify(total=total, active=active)


@app.route("/api/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    uid  = get_jwt_identity()
    user = User.query.get(uid)
    if not user:
        return jsonify(error="User not found."), 404
    data = request.get_json()
    if data.get("name"):           user.name  = data["name"]
    if data.get("phone") is not None: user.phone = data["phone"]
    if data.get("password"):
        if len(data["password"]) < 6:
            return jsonify(error="Password must be at least 6 characters."), 400
        user.password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    if data.get("profile_image_data_url"):
        try:
            user.profile_image = save_data_url(data["profile_image_data_url"], "profile")
        except Exception as e:
            return jsonify(error=str(e)), 400
    db.session.commit()
    return jsonify(user=user.to_dict())


@app.route("/api/chat/history")
@jwt_required()
def chat_history():
    msgs = ChatMessage.query.order_by(ChatMessage.created_at.asc()).limit(100).all()
    return jsonify(messages=[m.to_dict() for m in msgs])


# ═══════════════════════════════════════════════════════════════
# ADMIN – MEMBERS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/members", methods=["GET"])
@jwt_required()
def admin_get_members():
    err = require_admin()
    if err: return err
    members = User.query.order_by(User.joined_at.desc()).all()
    return jsonify(members=[m.to_dict() for m in members])


@app.route("/api/admin/members", methods=["POST"])
@jwt_required()
def admin_create_member():
    err = require_admin()
    if err: return err
    data  = request.get_json()
    if not data.get("name") or not data.get("email") or not data.get("password"):
        return jsonify(error="Name, email, and password are required."), 400
    email = data["email"].lower().strip()
    if User.query.filter_by(email=email).first():
        return jsonify(error="A member with this email already exists."), 409
    user  = User(name=data["name"], email=email, phone=data.get("phone"),
                 password_hash=bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode(),
                 role="admin" if data.get("role") == "admin" else "member")
    db.session.add(user); db.session.commit()
    return jsonify(member=user.to_dict()), 201


@app.route("/api/admin/members/<mid>", methods=["PUT"])
@jwt_required()
def admin_update_member(mid):
    err = require_admin()
    if err: return err
    user = User.query.get_or_404(mid)
    data = request.get_json()
    if data.get("name"):            user.name   = data["name"]
    if data.get("phone") is not None: user.phone  = data["phone"]
    if data.get("role"):            user.role   = "admin" if data["role"]=="admin" else "member"
    if data.get("status"):          user.status = "terminated" if data["status"]=="terminated" else "active"
    if data.get("password"):        user.password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    return jsonify(member=user.to_dict())


@app.route("/api/admin/members/<mid>", methods=["DELETE"])
@jwt_required()
def admin_delete_member(mid):
    err = require_admin()
    if err: return err
    user = User.query.get_or_404(mid)
    db.session.delete(user); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – PAYMENTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/payments", methods=["GET"])
@jwt_required()
def admin_get_payments():
    err = require_admin()
    if err: return err
    return jsonify(payments=[p.to_dict() for p in Payment.query.order_by(Payment.recorded_at.desc()).all()])


@app.route("/api/admin/payments", methods=["POST"])
@jwt_required()
def admin_create_payment():
    err = require_admin()
    if err: return err
    data = request.get_json()
    if not data.get("user_id") or not data.get("amount") or not data.get("for_month"):
        return jsonify(error="Member, amount, and month are required."), 400
    p = Payment(user_id=data["user_id"], amount=float(data["amount"]),
                for_month=data["for_month"], note=data.get("note"))
    db.session.add(p); db.session.commit()
    return jsonify(payment=p.to_dict()), 201


@app.route("/api/admin/payments/<pid>", methods=["DELETE"])
@jwt_required()
def admin_delete_payment(pid):
    err = require_admin()
    if err: return err
    p = Payment.query.get_or_404(pid)
    db.session.delete(p); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – DONATIONS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/donations", methods=["GET"])
@jwt_required()
def admin_get_donations():
    err = require_admin()
    if err: return err
    return jsonify(donations=[d.to_dict() for d in Donation.query.order_by(Donation.recorded_at.desc()).all()])


@app.route("/api/admin/donations", methods=["POST"])
@jwt_required()
def admin_create_donation():
    err = require_admin()
    if err: return err
    data = request.get_json()
    if not data.get("donor_name") or not data.get("amount"):
        return jsonify(error="Donor name and amount are required."), 400
    d = Donation(donor_name=data["donor_name"], amount=float(data["amount"]),
                 source=data.get("source"), note=data.get("note"))
    db.session.add(d); db.session.commit()
    return jsonify(donation=d.to_dict()), 201


@app.route("/api/admin/donations/<did>", methods=["DELETE"])
@jwt_required()
def admin_delete_donation(did):
    err = require_admin()
    if err: return err
    d = Donation.query.get_or_404(did)
    db.session.delete(d); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – EXPENSES
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/expenses", methods=["GET"])
@jwt_required()
def admin_get_expenses():
    err = require_admin()
    if err: return err
    return jsonify(expenses=[e.to_dict() for e in Expense.query.order_by(Expense.recorded_at.desc()).all()])


@app.route("/api/admin/expenses", methods=["POST"])
@jwt_required()
def admin_create_expense():
    err = require_admin()
    if err: return err
    data = request.get_json()
    if not data.get("title") or not data.get("amount"):
        return jsonify(error="Title and amount are required."), 400
    e = Expense(title=data["title"], amount=float(data["amount"]),
                category=data.get("category"), note=data.get("note"))
    db.session.add(e); db.session.commit()
    return jsonify(expense=e.to_dict()), 201


@app.route("/api/admin/expenses/<eid>", methods=["DELETE"])
@jwt_required()
def admin_delete_expense(eid):
    err = require_admin()
    if err: return err
    e = Expense.query.get_or_404(eid)
    db.session.delete(e); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – NEWS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/news", methods=["POST"])
@jwt_required()
def admin_create_news():
    err = require_admin()
    if err: return err
    data  = request.get_json()
    if not data.get("title") or not data.get("content"):
        return jsonify(error="Title and content are required."), 400
    image = None
    if data.get("image_data_url"):
        try:    image = save_data_url(data["image_data_url"], "news")
        except Exception as ex: return jsonify(error=str(ex)), 400
    p = NewsPost(title=data["title"], content=data["content"], image=image)
    db.session.add(p); db.session.commit()
    return jsonify(post=p.to_dict()), 201


@app.route("/api/admin/news/<nid>", methods=["DELETE"])
@jwt_required()
def admin_delete_news(nid):
    err = require_admin()
    if err: return err
    p = NewsPost.query.get_or_404(nid)
    db.session.delete(p); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – GALLERY
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/gallery", methods=["POST"])
@jwt_required()
def admin_upload_gallery():
    err = require_admin()
    if err: return err
    data = request.get_json()
    if not data.get("image_data_url"):
        return jsonify(error="An image is required."), 400
    try:    url = save_data_url(data["image_data_url"], "gallery")
    except Exception as ex: return jsonify(error=str(ex)), 400
    img = GalleryImage(url=url, caption=data.get("caption"))
    db.session.add(img); db.session.commit()
    return jsonify(image=img.to_dict()), 201


@app.route("/api/admin/gallery/<gid>", methods=["DELETE"])
@jwt_required()
def admin_delete_gallery(gid):
    err = require_admin()
    if err: return err
    img = GalleryImage.query.get_or_404(gid)
    db.session.delete(img); db.session.commit()
    return jsonify(ok=True)


# ═══════════════════════════════════════════════════════════════
# ADMIN – SETTINGS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/settings", methods=["PUT"])
@jwt_required()
def admin_update_settings():
    err = require_admin()
    if err: return err
    data = request.get_json()
    try:
        val = int(data.get("lives_impacted", 0))
        if val < 0: raise ValueError
    except (ValueError, TypeError):
        return jsonify(error="Please provide a valid non-negative number."), 400
    s = Settings.query.get(1)
    if not s: s = Settings(id=1); db.session.add(s)
    s.lives_impacted = val
    db.session.commit()
    return jsonify(settings={"lives_impacted": s.lives_impacted})


# ═══════════════════════════════════════════════════════════════
# ADMIN – PDF REPORTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/admin/reports/member/<mid>")
@jwt_required()
def report_member(mid):
    err = require_admin()
    if err: return err
    user     = User.query.get_or_404(mid)
    payments = Payment.query.filter_by(user_id=mid).order_by(Payment.recorded_at.asc()).all()
    pdf_bytes= generate_member_pdf(user, payments)
    safe_name = user.name.replace(" ", "-").replace("/", "-")
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="BBOF-Report-{safe_name}.pdf"'})


@app.route("/api/admin/reports/all")
@jwt_required()
def report_all():
    err = require_admin()
    if err: return err
    summary   = get_finance_summary()
    members   = User.query.filter_by(role="member").order_by(User.name).all()
    donations = Donation.query.order_by(Donation.recorded_at.desc()).all()
    expenses  = Expense.query.order_by(Expense.recorded_at.desc()).all()
    settings  = Settings.query.get(1)
    pdf_bytes = generate_all_pdf(summary, members, donations, expenses, settings)
    ts = datetime.now().strftime("%Y%m%d")
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="BBOF-Financial-Report-{ts}.pdf"'})


# ═══════════════════════════════════════════════════════════════
# SOCKET.IO EVENTS  (chat + presence)
# ═══════════════════════════════════════════════════════════════

def _auth_socket():
    """Returns User or None."""
    try:
        token = request.cookies.get("access_token_cookie")
        if not token:
            return None
        decoded = decode_token(token)
        uid     = decoded["sub"]
        user    = User.query.get(uid)
        return user if (user and user.status == "active") else None
    except Exception:
        return None


@socketio.on("connect")
def on_connect():
    with app.app_context():
        user = _auth_socket()
        if not user:
            return False   # reject
        online_users[request.sid] = {
            "id": user.id, "name": user.name,
            "profile_image": user.profile_image, "role": user.role,
        }
        join_room("chat")
        # Send history to this client
        msgs = ChatMessage.query.order_by(ChatMessage.created_at.asc()).limit(100).all()
        emit("chat_history", [m.to_dict() for m in msgs])
        # Broadcast updated presence list to everyone
        emit("users_online", get_online_list(), room="chat")


@socketio.on("disconnect")
def on_disconnect():
    online_users.pop(request.sid, None)
    with app.app_context():
        socketio.emit("users_online", get_online_list(), room="chat")


@socketio.on("send_message")
def on_send_message(data):
    with app.app_context():
        if request.sid not in online_users:
            return
        content = (data.get("content") or "").strip()
        if not content or len(content) > 1000:
            return
        uid  = online_users[request.sid]["id"]
        user = User.query.get(uid)
        if not user:
            return
        msg  = ChatMessage(user_id=uid, content=content)
        db.session.add(msg); db.session.commit()
        db.session.refresh(msg)
        emit("new_message", msg.to_dict(), room="chat")


# ═══════════════════════════════════════════════════════════════
# SEED
# ═══════════════════════════════════════════════════════════════

def seed():
    with app.app_context():
        db.create_all()
        if not Settings.query.get(1):
            db.session.add(Settings(id=1, lives_impacted=0)); db.session.commit()
        if not User.query.filter_by(email="admin@bbof.org").first():
            admin = User(name="Foundation Admin", email="admin@bbof.org",
                         password_hash=bcrypt.hashpw(b"Admin@123", bcrypt.gensalt()).decode(),
                         role="admin")
            db.session.add(admin); db.session.commit()
            print("✅  Admin created: admin@bbof.org / Admin@123")


if __name__ == "__main__":
    seed()
    socketio.run(app, debug=False, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
