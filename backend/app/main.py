import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, Field

from . import db
from .security import check_pw, current_user, hash_pw, make_token

app = FastAPI(title="Dokotela API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGIN", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

SAST = timezone(timedelta(hours=2))  # South Africa has no daylight saving
COMMISSION_PCT = int(os.environ.get("COMMISSION_PCT", "10"))
CANCEL_HOURS = 2  # patient cancels later than this -> only 50% refunded, rest goes to the doctor
BOOK_LEAD = timedelta(minutes=15)
STATUSES = ("online", "resting", "dropin")


def now():
    return datetime.now(SAST)


def get_or_404(sql, **p):
    row = db.one(sql, **p)
    if not row:
        raise HTTPException(404, "Not found")
    return row


def notify(uid, body, link=None):
    db.run("INSERT INTO notifications (user_id, body, link) VALUES (:u,:b,:l)", u=uid, b=body, l=link)


def credit(uid, cents, kind, appt_id=None, note=None):
    """Move money into a wallet + write the ledger row. Call inside db.tx()."""
    if cents <= 0:
        return
    db.run("UPDATE users SET balance_cents=balance_cents+:c WHERE id=:u", c=cents, u=uid)
    db.run("INSERT INTO wallet_tx (user_id, amount_cents, kind, appointment_id, note) VALUES (:u,:c,:k,:a,:n)",
           u=uid, c=cents, k=kind, a=appt_id, n=note)


# ---------- slot logic ----------
def day_slots(doc, avail, booked, day):
    """Free slot start times (SAST) for one doctor on one date."""
    if doc["status"] == "resting" and day == now().date():
        return []  # resting doctors are not taking same-day bookings
    step = timedelta(minutes=doc["slot_minutes"])
    out = []
    for a in avail:
        if a["weekday"] != day.weekday():
            continue
        t = datetime.combine(day, a["start_time"], SAST)
        end = datetime.combine(day, a["end_time"], SAST)
        while t + step <= end:
            if t > now() + BOOK_LEAD and t.timestamp() not in booked:
                out.append(t)
            t += step
    return sorted(out)


def load_avail(doctor_id):
    return db.run("SELECT weekday, start_time, end_time FROM availability WHERE doctor_id=:d", d=doctor_id)


def load_booked(doctor_id, start, end):
    rows = db.run("SELECT starts_at FROM appointments WHERE doctor_id=:d AND status='booked' "
                  "AND starts_at>=:a AND starts_at<:b", d=doctor_id, a=start, b=end)
    return {r["starts_at"].timestamp() for r in rows}


def day_bounds(day):
    s = datetime.combine(day, time(0), SAST)
    return s, s + timedelta(days=1)


@app.get("/health")
def health():
    return {"ok": True}


# ---------- auth ----------
class Register(BaseModel):
    email: str = Field(max_length=200)
    full_name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = Field(None, max_length=30)
    password: str = Field(min_length=8, max_length=200)


class Login(BaseModel):
    email: str
    password: str


@app.post("/auth/register")
def register(b: Register):
    email = b.email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Invalid email")
    try:
        u = db.one("INSERT INTO users (email, full_name, phone, password_hash) VALUES (:e,:n,:p,:h) RETURNING id",
                   e=email, n=b.full_name.strip(), p=b.phone, h=hash_pw(b.password))
    except Exception as e:
        if "23505" in str(e):
            raise HTTPException(409, "That email is already registered")
        raise
    return {"token": make_token(u["id"])}


@app.post("/auth/login")
def login(b: Login):
    u = db.one("SELECT id, password_hash FROM users WHERE email=:e", e=b.email.strip().lower())
    if not u or not check_pw(b.password, u["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(u["id"])}


@app.get("/me")
def me(u=Depends(current_user)):
    return u


# ---------- doctor discovery ----------
@app.get("/doctors")
def doctors(q: str = "", city: str = "", specialty: str = "", max_fee_cents: int = 0, status: str = "",
            limit: int = Query(20, le=40), offset: int = 0):
    sql = ("SELECT d.id, d.name, d.profession, d.specialty, d.city, d.address, d.fee_cents, d.slot_minutes, "
           "d.status, COALESCE(r.avg, 0) AS avg_rating, COALESCE(r.n, 0) AS review_count "
           "FROM doctors d LEFT JOIN (SELECT doctor_id, ROUND(AVG(rating)::numeric, 1)::float AS avg, "
           "COUNT(*) AS n FROM reviews GROUP BY doctor_id) r ON r.doctor_id = d.id "
           "WHERE d.verified AND d.active")
    p = {"n": limit, "o": offset}
    if q:
        sql += " AND (d.name ILIKE :q OR d.specialty ILIKE :q OR d.profession ILIKE :q)"
        p["q"] = f"%{q}%"
    if city:
        sql += " AND d.city ILIKE :city"
        p["city"] = f"%{city}%"
    if specialty:
        sql += " AND d.specialty ILIKE :sp"
        p["sp"] = f"%{specialty}%"
    if max_fee_cents:
        sql += " AND d.fee_cents <= :mf"
        p["mf"] = max_fee_cents
    if status in STATUSES:
        sql += " AND d.status = :st"
        p["st"] = status
    docs = db.run(sql + " ORDER BY (d.status <> 'resting') DESC, avg_rating DESC, d.id LIMIT :n OFFSET :o", **p)
    if docs:
        ids = [d["id"] for d in docs]
        today = now().date()
        a0, _ = day_bounds(today)
        av = db.run("SELECT doctor_id, weekday, start_time, end_time FROM availability WHERE doctor_id = ANY(:ids)", ids=ids)
        bk = db.run("SELECT doctor_id, starts_at FROM appointments WHERE doctor_id = ANY(:ids) AND status='booked' "
                    "AND starts_at>=:a AND starts_at<:b", ids=ids, a=a0, b=a0 + timedelta(days=8))
        for d in docs:
            mine = [a for a in av if a["doctor_id"] == d["id"]]
            taken = {b["starts_at"].timestamp() for b in bk if b["doctor_id"] == d["id"]}
            d["next_slot"] = None
            for i in range(8):
                s = day_slots(d, mine, taken, today + timedelta(days=i))
                if s:
                    d["next_slot"] = s[0].isoformat()
                    break
    return docs


@app.get("/specialties")
def specialties():
    return [r["specialty"] for r in db.run(
        "SELECT specialty, COUNT(*) AS n FROM doctors WHERE verified AND active GROUP BY specialty ORDER BY n DESC")]


@app.get("/doctors/{did}")
def doctor(did: int):
    d = get_or_404("SELECT id, name, profession, specialty, city, address, bio, fee_cents, slot_minutes, status "
                   "FROM doctors WHERE id=:i AND verified AND active", i=did)
    agg = db.one("SELECT COUNT(*) AS n, ROUND(AVG(rating)::numeric,1)::float AS avg, "
                 "ROUND(AVG(wait_rating)::numeric,1)::float AS wait, ROUND(AVG(clinic_rating)::numeric,1)::float AS clinic, "
                 "ROUND(AVG(comm_rating)::numeric,1)::float AS comm FROM reviews WHERE doctor_id=:i", i=did)
    d["ratings"] = agg
    d["reviews"] = db.run(
        "SELECT r.rating, r.wait_rating, r.clinic_rating, r.comm_rating, r.comment, r.created_at, "
        "split_part(u.full_name, ' ', 1) AS patient FROM reviews r JOIN users u ON u.id=r.patient_id "
        "WHERE r.doctor_id=:i ORDER BY r.id DESC LIMIT 20", i=did)
    return d


@app.get("/doctors/{did}/slots")
def slots(did: int, day: str):
    d = get_or_404("SELECT id, slot_minutes, status, fee_cents FROM doctors WHERE id=:i AND verified AND active", i=did)
    try:
        dt = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Bad date")
    if not now().date() <= dt <= now().date() + timedelta(days=60):
        raise HTTPException(400, "Pick a date within the next 60 days")
    a, b = day_bounds(dt)
    s = day_slots(d, load_avail(did), load_booked(did, a, b), dt)
    return {"slots": [x.isoformat() for x in s], "fee_cents": d["fee_cents"], "status": d["status"]}


# ---------- doctor side ----------
class DoctorIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    profession: str = Field(min_length=2, max_length=80)
    specialty: str = Field(min_length=2, max_length=80)
    hpcsa_number: str = Field(min_length=3, max_length=40)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=5, max_length=250)
    bio: Optional[str] = Field(None, max_length=1500)
    fee_cents: int = Field(ge=5000, le=2000000)
    slot_minutes: Literal[15, 20, 30, 45, 60]


def my_doctor(u):
    if not u["doctor_id"]:
        raise HTTPException(403, "You do not have a practitioner profile")
    return get_or_404("SELECT * FROM doctors WHERE id=:i", i=u["doctor_id"])


@app.post("/doctor/apply")
def apply(b: DoctorIn, u=Depends(current_user)):
    try:
        r = db.one("INSERT INTO doctors (user_id, name, profession, specialty, hpcsa_number, city, address, bio, "
                   "fee_cents, slot_minutes) VALUES (:u,:n,:p,:s,:h,:c,:a,:b,:f,:m) RETURNING id",
                   u=u["id"], n=b.name, p=b.profession, s=b.specialty, h=b.hpcsa_number, c=b.city,
                   a=b.address, b=b.bio, f=b.fee_cents, m=b.slot_minutes)
    except Exception as e:
        if "23505" in str(e):
            raise HTTPException(409, "You already have a practitioner profile")
        raise
    return r


@app.get("/doctor/me")
def doctor_me(u=Depends(current_user)):
    d = my_doctor(u)
    d["availability"] = db.run(
        "SELECT weekday, to_char(start_time,'HH24:MI') AS start, to_char(end_time,'HH24:MI') AS \"end\" "
        "FROM availability WHERE doctor_id=:d ORDER BY weekday, start_time", d=d["id"])
    return d


@app.put("/doctor/profile")
def doctor_profile(b: DoctorIn, u=Depends(current_user)):
    d = my_doctor(u)
    # HPCSA number changes require re-verification
    reverify = b.hpcsa_number.strip() != d["hpcsa_number"]
    db.run("UPDATE doctors SET name=:n, profession=:p, specialty=:s, hpcsa_number=:h, city=:c, address=:a, bio=:b, "
           "fee_cents=:f, slot_minutes=:m, verified=(verified AND NOT :rv) WHERE id=:i",
           n=b.name, p=b.profession, s=b.specialty, h=b.hpcsa_number.strip(), c=b.city, a=b.address, b=b.bio,
           f=b.fee_cents, m=b.slot_minutes, rv=reverify, i=d["id"])
    return {"ok": True, "reverify": reverify}


class SlotWindow(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


@app.put("/doctor/availability")
def set_availability(rows: list[SlotWindow], u=Depends(current_user)):
    d = my_doctor(u)
    if len(rows) > 21:
        raise HTTPException(400, "Too many windows")
    parsed = []
    for r in rows:
        try:
            s, e = time.fromisoformat(r.start), time.fromisoformat(r.end)
        except ValueError:
            raise HTTPException(400, "Bad time")
        if e <= s:
            raise HTTPException(400, "End time must be after start time")
        parsed.append((r.weekday, s, e))
    with db.tx():
        db.run("DELETE FROM availability WHERE doctor_id=:d", d=d["id"])
        for w, s, e in parsed:
            db.run("INSERT INTO availability (doctor_id, weekday, start_time, end_time) VALUES (:d,:w,:s,:e)",
                   d=d["id"], w=w, s=s, e=e)
    return {"ok": True}


class StatusIn(BaseModel):
    status: Literal["online", "resting", "dropin"]


@app.patch("/doctor/status")
def set_status(b: StatusIn, u=Depends(current_user)):
    d = my_doctor(u)
    db.run("UPDATE doctors SET status=:s WHERE id=:i", s=b.status, i=d["id"])
    return {"status": b.status}


@app.get("/doctor/appointments")
def doctor_appointments(u=Depends(current_user)):
    d = my_doctor(u)
    return db.run(
        "SELECT a.id, a.starts_at, a.ends_at, a.fee_cents, a.reason, a.status, p.full_name AS patient, p.phone "
        "FROM appointments a JOIN users p ON p.id=a.patient_id WHERE a.doctor_id=:d "
        "AND (a.status='booked' OR a.starts_at > now() - interval '30 days') ORDER BY a.starts_at DESC LIMIT 100",
        d=d["id"])


# ---------- booking ----------
class BookIn(BaseModel):
    doctor_id: int
    starts_at: datetime
    reason: Optional[str] = Field(None, max_length=500)


@app.post("/appointments")
def book(b: BookIn, u=Depends(current_user)):
    d = get_or_404("SELECT * FROM doctors WHERE id=:i AND verified AND active", i=b.doctor_id)
    if d["user_id"] == u["id"]:
        raise HTTPException(400, "You cannot book yourself")
    if b.starts_at.tzinfo is None:
        raise HTTPException(400, "starts_at needs a timezone offset")
    t = b.starts_at.astimezone(SAST)
    lo, hi = day_bounds(t.date())
    if t not in day_slots(d, load_avail(d["id"]), load_booked(d["id"], lo, hi), t.date()):
        raise HTTPException(409, "That slot is no longer available")
    fee = d["fee_cents"]
    try:
        with db.tx():
            paid = db.one("UPDATE users SET balance_cents=balance_cents-:f WHERE id=:u AND balance_cents>=:f "
                          "RETURNING balance_cents", f=fee, u=u["id"])
            if not paid:
                raise HTTPException(402, "Not enough wallet balance - top up first")
            appt = db.one("INSERT INTO appointments (patient_id, doctor_id, starts_at, ends_at, fee_cents, reason) "
                          "VALUES (:p,:d,:s,:e,:f,:r) RETURNING id",
                          p=u["id"], d=d["id"], s=t, e=t + timedelta(minutes=d["slot_minutes"]), f=fee, r=b.reason)
            db.run("INSERT INTO wallet_tx (user_id, amount_cents, kind, appointment_id, note) VALUES (:u,:c,'booking',:a,:n)",
                   u=u["id"], c=-fee, a=appt["id"], n=f"Consultation with {d['name']}")
            notify(d["user_id"], f"New booking: {u['full_name']} on {t:%a %d %b at %H:%M}", "#/doctor")
            notify(u["id"], f"Booked with {d['name']} on {t:%a %d %b at %H:%M}", "#/appointments")
    except HTTPException:
        raise
    except Exception as e:
        if "23505" in str(e):
            raise HTTPException(409, "Someone just took that slot - pick another")
        raise
    return {"id": appt["id"], "balance_cents": paid["balance_cents"]}


@app.get("/appointments")
def my_appointments(u=Depends(current_user)):
    return db.run(
        "SELECT a.id, a.starts_at, a.fee_cents, a.reason, a.status, a.cancelled_by, d.id AS doctor_id, d.name AS doctor, "
        "d.specialty, d.address, d.city, EXISTS(SELECT 1 FROM reviews r WHERE r.appointment_id=a.id) AS reviewed "
        "FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.patient_id=:u ORDER BY a.starts_at DESC LIMIT 100",
        u=u["id"])


@app.post("/appointments/{aid}/cancel")
def cancel(aid: int, u=Depends(current_user)):
    a = get_or_404("SELECT a.*, d.user_id AS doctor_user, d.name AS doctor_name FROM appointments a "
                   "JOIN doctors d ON d.id=a.doctor_id WHERE a.id=:i", i=aid)
    by_patient, by_doctor = a["patient_id"] == u["id"], a["doctor_user"] == u["id"]
    if not (by_patient or by_doctor):
        raise HTTPException(403, "Not your appointment")
    if a["status"] != "booked":
        raise HTTPException(409, "Appointment is not active")
    if a["starts_at"] <= now():
        raise HTTPException(400, "This appointment has already started")
    late = by_patient and (a["starts_at"] - now()) < timedelta(hours=CANCEL_HOURS)
    refund = a["fee_cents"] // 2 if late else a["fee_cents"]
    with db.tx():
        ok = db.one("UPDATE appointments SET status='cancelled', cancelled_by=:w WHERE id=:i AND status='booked' RETURNING id",
                    w="patient" if by_patient else "doctor", i=aid)
        if not ok:
            raise HTTPException(409, "Appointment is not active")
        credit(a["patient_id"], refund, "refund", aid, f"Refund - {a['doctor_name']}")
        if a["fee_cents"] - refund:
            credit(a["doctor_user"], a["fee_cents"] - refund, "earning", aid, "Late cancellation fee")
        when = a["starts_at"].astimezone(SAST)
        if by_patient:
            notify(a["doctor_user"], f"Appointment on {when:%a %d %b at %H:%M} was cancelled by the patient", "#/doctor")
        else:
            notify(a["patient_id"], f"{a['doctor_name']} cancelled your {when:%a %d %b %H:%M} appointment. Full refund issued.", "#/appointments")
    return {"refunded_cents": refund}


@app.post("/appointments/{aid}/complete")
def complete(aid: int, u=Depends(current_user)):
    d = my_doctor(u)
    a = get_or_404("SELECT * FROM appointments WHERE id=:i AND doctor_id=:d", i=aid, d=d["id"])
    if a["status"] != "booked":
        raise HTTPException(409, "Appointment is not active")
    if a["starts_at"] > now():
        raise HTTPException(400, "You can complete it once the appointment has started")
    net = a["fee_cents"] - a["fee_cents"] * COMMISSION_PCT // 100
    with db.tx():
        ok = db.one("UPDATE appointments SET status='completed', completed_at=now() WHERE id=:i AND status='booked' RETURNING id", i=aid)
        if not ok:
            raise HTTPException(409, "Appointment is not active")
        credit(u["id"], net, "earning", aid, f"Consultation fee less {COMMISSION_PCT}% platform fee")
        notify(a["patient_id"], f"How was your visit with {d['name']}? Leave a review.", "#/appointments")
    return {"earned_cents": net}


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    wait_rating: Optional[int] = Field(None, ge=1, le=5)
    clinic_rating: Optional[int] = Field(None, ge=1, le=5)
    comm_rating: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1500)


@app.post("/appointments/{aid}/review")
def review(aid: int, b: ReviewIn, u=Depends(current_user)):
    a = get_or_404("SELECT doctor_id, status FROM appointments WHERE id=:i AND patient_id=:u", i=aid, u=u["id"])
    if a["status"] != "completed":
        raise HTTPException(400, "You can review after the consultation is completed")
    try:
        db.run("INSERT INTO reviews (appointment_id, doctor_id, patient_id, rating, wait_rating, clinic_rating, comm_rating, comment) "
               "VALUES (:a,:d,:p,:r,:w,:c,:m,:t)", a=aid, d=a["doctor_id"], p=u["id"], r=b.rating, w=b.wait_rating,
               c=b.clinic_rating, m=b.comm_rating, t=b.comment)
    except Exception as e:
        if "23505" in str(e):
            raise HTTPException(409, "You already reviewed this visit")
        raise
    return {"ok": True}


# ---------- wallet & payments ----------
PAYSTACK_API = "https://api.paystack.co"


def paystack(path, payload=None):
    key = os.environ.get("PAYSTACK_SECRET")
    if not key:
        raise HTTPException(503, "Card payments are not configured yet")
    req = urllib.request.Request(
        PAYSTACK_API + path, json.dumps(payload).encode() if payload else None,
        {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "dokotela/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:
        raise HTTPException(502, "Payment provider unavailable, try again")


def settle(ref, amount_cents):
    """Credit a paid top-up exactly once (webhook and verify can both call this safely)."""
    with db.tx():
        t = db.one("UPDATE topups SET status='paid' WHERE reference=:r AND status='pending' AND amount_cents=:a "
                   "RETURNING user_id", r=ref, a=amount_cents)
        if t:
            credit(t["user_id"], amount_cents, "topup", None, "Wallet top-up")
    return bool(t)


@app.get("/wallet")
def wallet(u=Depends(current_user)):
    return {
        "balance_cents": u["balance_cents"],
        "dev_topup": os.environ.get("DEV_TOPUP") == "1",
        "transactions": db.run("SELECT id, amount_cents, kind, note, created_at FROM wallet_tx WHERE user_id=:u "
                               "ORDER BY id DESC LIMIT 50", u=u["id"]),
    }


class TopupIn(BaseModel):
    amount_cents: int = Field(ge=2000, le=500000)  # R20 - R5000


@app.post("/wallet/topup")
def topup(b: TopupIn, u=Depends(current_user)):
    ref = "dk_" + secrets.token_hex(10)
    res = paystack("/transaction/initialize", {
        "email": u["email"], "amount": b.amount_cents, "currency": "ZAR", "reference": ref,
        "callback_url": os.environ.get("FRONTEND_URL", "")})
    db.run("INSERT INTO topups (reference, user_id, amount_cents) VALUES (:r,:u,:a)", r=ref, u=u["id"], a=b.amount_cents)
    return {"url": res["data"]["authorization_url"], "reference": ref}


@app.get("/wallet/verify/{ref}")
def verify_topup(ref: str, u=Depends(current_user)):
    t = get_or_404("SELECT amount_cents, status FROM topups WHERE reference=:r AND user_id=:u", r=ref, u=u["id"])
    if t["status"] == "pending":
        d = paystack("/transaction/verify/" + ref)["data"]
        if d.get("status") == "success" and d.get("currency") == "ZAR":
            settle(ref, d["amount"])
    return {"status": db.one("SELECT status FROM topups WHERE reference=:r", r=ref)["status"]}


@app.post("/webhooks/paystack")
async def paystack_webhook(request: Request):
    key = os.environ.get("PAYSTACK_SECRET", "")
    raw = await request.body()
    sig = request.headers.get("x-paystack-signature", "")
    if not key or not hmac.compare_digest(sig, hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()):
        raise HTTPException(401, "Bad signature")
    ev = json.loads(raw)
    if ev.get("event") == "charge.success" and ev["data"].get("currency") == "ZAR":
        settle(ev["data"]["reference"], ev["data"]["amount"])
    return {"ok": True}


@app.post("/wallet/dev-topup")
def dev_topup(b: TopupIn, u=Depends(current_user)):
    if os.environ.get("DEV_TOPUP") != "1":
        raise HTTPException(404, "Not found")
    with db.tx():
        credit(u["id"], b.amount_cents, "topup", None, "Test credit")
    return {"ok": True}


# ---------- notifications ----------
@app.get("/notifications")
def notifications(u=Depends(current_user)):
    return {
        "unread": db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=:u AND NOT read", u=u["id"])["n"],
        "items": db.run("SELECT id, body, link, read, created_at FROM notifications WHERE user_id=:u ORDER BY id DESC LIMIT 30", u=u["id"]),
    }


@app.post("/notifications/read")
def mark_read(u=Depends(current_user)):
    db.run("UPDATE notifications SET read=TRUE WHERE user_id=:u AND NOT read", u=u["id"])
    return {"ok": True}


# ---------- admin: accreditation ----------
def require_admin(u=Depends(current_user)):
    if not u["is_admin"]:
        raise HTTPException(403, "Admins only")
    return u


@app.get("/admin/doctors")
def admin_doctors(pending: bool = True, u=Depends(require_admin)):
    return db.run("SELECT d.id, d.name, d.profession, d.specialty, d.hpcsa_number, d.city, d.verified, d.created_at, "
                  "us.email FROM doctors d JOIN users us ON us.id=d.user_id WHERE (:p AND NOT d.verified) OR NOT :p "
                  "ORDER BY d.id DESC LIMIT 100", p=pending)


class VerifyIn(BaseModel):
    verified: bool


@app.post("/admin/doctors/{did}/verify")
def admin_verify(did: int, b: VerifyIn, u=Depends(require_admin)):
    d = get_or_404("UPDATE doctors SET verified=:v WHERE id=:i RETURNING user_id, name", v=b.verified, i=did)
    notify(d["user_id"], "Your practitioner profile was verified and is now listed." if b.verified
           else "Your practitioner profile was not verified. Please check your HPCSA details.", "#/doctor")
    return {"ok": True}


handler = Mangum(app, lifespan="off")
