import hashlib
import hmac
import os
import secrets
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

bearer = HTTPBearer(auto_error=False)


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    salt, h = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return hmac.compare_digest(test, h)


def make_token(uid: int) -> str:
    return jwt.encode({"sub": str(uid), "exp": int(time.time()) + 7 * 86400}, os.environ["JWT_SECRET"], "HS256")


def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(401, "Login required")
    try:
        uid = int(jwt.decode(creds.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"])["sub"])
    except Exception:
        raise HTTPException(401, "Session expired, please log in again")
    u = db.one(
        "SELECT id, email, full_name, phone, balance_cents, "
        "(SELECT id FROM doctors WHERE user_id=users.id) AS doctor_id FROM users WHERE id=:i", i=uid)
    if not u:
        raise HTTPException(401, "Login required")
    admins = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    u["is_admin"] = u["email"] in admins
    return u
