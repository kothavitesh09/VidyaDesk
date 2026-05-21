from datetime import datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash
from flask import current_app, has_request_context, session

from extensions import mongo
from utils.performance import ttl_cache
from utils.tenant import current_school_id, default_school_id, scoped_insert, scoped_query
from services.saas_service import ensure_legacy_school_account, seed_super_admin

GRADES = [
    "Grade-LKG", "Grade-UKG", "Grade-I", "Grade-II", "Grade-III", "Grade-IV",
    "Grade-V", "Grade-VI", "Grade-VII", "Grade-VIII", "Grade-IX", "Grade-X",
    "Grade-XI", "Grade-XII",
]

FEE_HEADS = [
    ("tuition_fee", "Tuition Fee"),
    ("books_fee", "Books Fee"),
    ("uniform_fee", "Uniform Fee"),
    ("lab_fee", "Lab Fee"),
    ("transport_fee", "Transport Fee"),
    ("hostel_fee", "Hostel Fee"),
    ("other_fee", "Other Fee"),
]


def oid(value):
    return ObjectId(value) if ObjectId.is_valid(str(value)) else None


def money(value):
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fmt_money(value):
    return f"{money(value):,.2f}"


def current_academic_year_name(date=None):
    today = date or datetime.now()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def active_year():
    selected = session.get("academic_year") if has_request_context() else None
    if selected:
        return selected
    return current_app.config.get("DEFAULT_ACADEMIC_YEAR") or current_academic_year_name()


def ensure_defaults():
    year_name = current_app.config.get("DEFAULT_ACADEMIC_YEAR") or current_academic_year_name()
    school_id = default_school_id()
    if not mongo.db.academic_years.find_one({"name": year_name, "school_id": school_id}):
        mongo.db.academic_years.insert_one({"name": year_name, "is_active": True, "school_id": school_id})
    if not mongo.db.users.find_one({"username": "admin"}):
        mongo.db.users.insert_one({
            "username": "admin",
            "password_hash": generate_password_hash("admin123"),
            "full_name": "Administrator",
            "role": "school_admin",
            "school_id": school_id,
            "approval_status": "approved",
            "account_status": "active",
            "expiry_date": "2099-12-31",
            "created_at": datetime.utcnow(),
        })
    else:
        mongo.db.users.update_one(
            {"username": "admin"},
            {"$set": {"role": "school_admin", "school_id": school_id, "approval_status": "approved", "account_status": "active"}},
        )
    ensure_legacy_school_account()
    seed_super_admin()


@ttl_cache(seconds=300)
def academic_years():
    names = set()
    for row in mongo.db.academic_years.find(scoped_query({}), {"name": 1}):
        if row.get("name"):
            names.add(str(row["name"]).strip())
    for collection_name in ("students", "fee_structures", "receipts", "payments", "discounts"):
        for name in mongo.db[collection_name].distinct("academic_year", scoped_query({})):
            if name:
                names.add(str(name).strip())
    names.update(generated_academic_years())
    return [{"name": name} for name in sorted(names, key=_academic_year_sort_key, reverse=True) if name]


def generated_academic_years(past=5, future=5):
    current_start = _academic_year_sort_key(current_academic_year_name())
    default_start = _academic_year_sort_key(current_app.config.get("DEFAULT_ACADEMIC_YEAR"))
    center = max(current_start, default_start)
    return [
        f"{year}-{str(year + 1)[-2:]}"
        for year in range(center - past, center + future + 1)
    ]


def _academic_year_sort_key(name):
    try:
        return int(str(name).split("-")[0])
    except (TypeError, ValueError):
        return -1


def next_receipt_no():
    year = datetime.now().year
    prefix = f"REC-{year}-"
    latest_numbers = []
    for collection in (mongo.db.receipts, mongo.db.payments):
        latest = collection.find_one(scoped_query({"receipt_no": {"$regex": f"^{prefix}"}}), sort=[("receipt_no", -1)])
        if latest:
            try:
                latest_numbers.append(int(str(latest.get("receipt_no", "")).split("-")[-1]))
            except ValueError:
                latest_numbers.append(0)
    return f"{prefix}{(max(latest_numbers) if latest_numbers else 0) + 1:04d}"


def student_status(balance, paid):
    if money(balance) <= 0:
        return "Fully Paid", "success"
    if money(paid) > 0:
        return "Partial", "warning"
    return "Due Pending", "danger"
