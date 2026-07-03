# Bowra_B Outreach Foundation – Web Portal

**B.BOF Member Portal** – A full-stack web application for the Bowra_B Outreach Foundation.

## Features

| Area | Functionality |
|------|---------------|
| **Public landing** | News feed, photo gallery, animated lives-impacted counter |
| **Member dashboard** | Overview stats, full dues history, gallery view, profile settings |
| **Admin dashboard** | Member CRUD, dues recording, donations, expenses, news posting, gallery management, lives-impacted control |
| **Finance tracking** | Total dues collected + donations − expenses = net wealth (live on both dashboards) |
| **Profile** | Upload photo, change name/phone/password |

---

## Tech stack

- **Backend:** Python / Flask + SQLAlchemy (SQLite for dev, PostgreSQL for prod)
- **Auth:** JWT cookies (HTTP-only, 7-day expiry)
- **Frontend:** Vanilla JS SPA (no framework, no build step)
- **Images:** PIL/Pillow (auto-resize to 1600px, stored in `static/uploads/`)

---

## Quick start (local)

```bash
# 1. Clone / unzip the project
cd bowra-bbof

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run (seeds DB + starts server)
python3 run.py
```

Visit **http://localhost:5000**

**Default admin credentials:**
- Email: `admin@bbof.org`
- Password: `Admin@123`  
⚠️ *Change this immediately after first login via Profile settings.*

---

## Environment variables

Create a `.env` file or set these in your hosting provider:

```env
SECRET_KEY=your-very-long-random-secret-key
JWT_SECRET=another-very-long-random-jwt-secret
DATABASE_URL=sqlite:///bbof.db          # or postgresql://user:pass@host/db
```

For production, use a strong random value for both secrets:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Deploy to Render (free tier)

1. Push this folder to a GitHub repository
2. Go to [render.com](https://render.com) → **New Web Service**
3. Connect your GitHub repo
4. Set:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
5. Add environment variables: `SECRET_KEY`, `JWT_SECRET`
6. Click **Deploy**

> **Note:** Render's free tier has an ephemeral filesystem, so uploaded images won't persist across deploys. For persistent uploads, integrate [Cloudinary](https://cloudinary.com) or [AWS S3](https://aws.amazon.com/s3/). For persistent DB, add a Render PostgreSQL database and set `DATABASE_URL`.

---

## Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

railway login
railway init
railway up
```

Set environment variables in the Railway dashboard.

---

## Deploy to VPS (Ubuntu)

```bash
# Install dependencies
sudo apt update && sudo apt install python3-pip python3-venv nginx -y

# Set up app
git clone <your-repo> /var/www/bbof
cd /var/www/bbof
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Create systemd service
sudo nano /etc/systemd/system/bbof.service
```

```ini
[Unit]
Description=BBOF Flask App
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/bbof
Environment="SECRET_KEY=your-secret"
Environment="JWT_SECRET=your-jwt-secret"
ExecStartPre=/var/www/bbof/venv/bin/python run.py
ExecStart=/var/www/bbof/venv/bin/gunicorn app:app --bind 127.0.0.1:5000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable bbof && sudo systemctl start bbof
```

Configure Nginx as a reverse proxy pointing to `127.0.0.1:5000`.

---

## First-time setup checklist

- [ ] Log in with `admin@bbof.org` / `Admin@123`
- [ ] Go to **Profile settings** and change the admin password
- [ ] Add members via **Members → + Add member**
- [ ] Post first news update via **News feed → + New post**
- [ ] Upload gallery photos via **Gallery → Upload new photo**
- [ ] Set the **Lives impacted** counter under that section
- [ ] Record any donations received via **Donations → + Record donation**

---

## Project structure

```
bbof/
├── app.py              ← Flask app + all API routes + DB models
├── run.py              ← Dev startup script (seeds DB + runs)
├── requirements.txt
├── Procfile            ← For Heroku/Render
├── static/
│   ├── logo.png        ← Foundation logo
│   └── uploads/        ← User-uploaded images (auto-created)
└── templates/
    └── index.html      ← Complete SPA frontend
```

---

## API reference (brief)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | — | Login |
| POST | `/api/auth/logout` | — | Logout |
| GET | `/api/me` | optional | Current user |
| GET | `/api/news` | — | Public news feed |
| GET | `/api/gallery` | — | Public gallery |
| GET | `/api/settings` | — | Lives impacted |
| GET | `/api/finance` | member | Financial summary |
| GET | `/api/payments/mine` | member | Own payment history |
| PUT | `/api/profile` | member | Update own profile |
| GET/POST | `/api/admin/members` | admin | List / create members |
| PUT/DELETE | `/api/admin/members/<id>` | admin | Edit / delete member |
| GET/POST | `/api/admin/payments` | admin | List / record dues |
| DELETE | `/api/admin/payments/<id>` | admin | Delete payment |
| GET/POST | `/api/admin/donations` | admin | List / record donations |
| DELETE | `/api/admin/donations/<id>` | admin | Delete donation |
| GET/POST | `/api/admin/expenses` | admin | List / record expenses |
| DELETE | `/api/admin/expenses/<id>` | admin | Delete expense |
| POST | `/api/admin/news` | admin | Post news update |
| DELETE | `/api/admin/news/<id>` | admin | Delete post |
| POST | `/api/admin/gallery` | admin | Upload gallery photo |
| DELETE | `/api/admin/gallery/<id>` | admin | Remove photo |
| PUT | `/api/admin/settings` | admin | Update lives impacted |

---

## New features (v2)

### PDF Financial Reports
Two report types, both downloadable as branded PDFs from the admin **Reports** page:

| Report | Contents |
|--------|----------|
| **Full Foundation Report** | Financial summary, organisation stats, all donations, all expenses, member dues breakdown |
| **Individual Member Report** | Member info, dues summary, full payment history |

Access: Admin sidebar → **Reports** → Download buttons (opens PDF in new tab).

### Community Chat
Real-time chat room shared by all members and admins:
- **Live messages** delivered instantly via WebSocket (Socket.IO)
- **Online presence** — right sidebar shows who is currently online with a green dot
- **Message history** — last 100 messages loaded on join
- **Unread badge** — gold counter on the nav link when new messages arrive while on another page
- **Keyboard shortcut** — press Enter to send

Access: Member sidebar → **Community chat** · Admin sidebar → **Community chat**

### Member count on overview
The member overview now shows three new stat cards:
- Total registered members
- Active members  
- Members currently online

### Deployment note for chat
WebSocket support requires one of:
- `gunicorn --worker-class eventlet -w 1 app:app` (included in Procfile)
- `gunicorn --worker-class gevent -w 1 app:app` (alternative)
- **Render**: set start command to the Procfile web command above
- **Railway**: add `eventlet` to requirements and the Procfile is picked up automatically
