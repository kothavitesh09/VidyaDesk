from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from extensions import mongo
from utils.tenant import default_school_id


def seed_super_admin():
    if mongo.db.admin_users.find_one({"username": "kothavitesh"}):
        return
    mongo.db.admin_users.insert_one({
        "username": "kothavitesh",
        "password_hash": generate_password_hash("Rkvc@2005"),
        "role": "super_admin",
        "created_at": datetime.utcnow(),
        "last_login": None,
    })


def ensure_legacy_school_account():
    school_id = default_school_id()
    mongo.db.users.update_many(
        {"approval_status": {"$exists": False}},
        {"$set": {"approval_status": "approved"}},
    )
    mongo.db.users.update_many(
        {"account_status": {"$exists": False}},
        {"$set": {"account_status": "active"}},
    )
    mongo.db.users.update_many(
        {"expiry_date": {"$exists": False}},
        {"$set": {"expiry_date": (datetime.utcnow() + timedelta(days=3650)).strftime("%Y-%m-%d")}},
    )
    if not mongo.db.schools.find_one({"school_id": school_id}):
        mongo.db.schools.insert_one({
            "school_id": school_id,
            "school_name": "Default School",
            "contact_person": "Administrator",
            "phone": "",
            "email": "",
            "address": "",
            "approval_status": "approved",
            "account_status": "active",
            "expiry_date": (datetime.utcnow() + timedelta(days=3650)).strftime("%Y-%m-%d"),
            "subscription_plan": "legacy",
            "payment_status": "active",
            "amount_paid": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })


def next_school_id():
    latest = mongo.db.schools.find_one({"school_id": {"$regex": "^SCH"}}, sort=[("school_id", -1)])
    if not latest:
        return "SCH001"
    try:
        number = int(str(latest.get("school_id", "SCH000"))[3:])
    except ValueError:
        number = mongo.db.schools.count_documents({}) + 1
    return f"SCH{number + 1:03d}"


def audit_log(action, actor_type, actor_username="", school_id="", details=None):
    mongo.db.audit_logs.insert_one({
        "action": action,
        "actor_type": actor_type,
        "actor_username": actor_username,
        "school_id": school_id,
        "details": details or {},
        "created_at": datetime.utcnow(),
    })


def school_is_expired(expiry_date):
    if not expiry_date:
        return False
    try:
        return datetime.strptime(str(expiry_date), "%Y-%m-%d").date() < datetime.utcnow().date()
    except ValueError:
        return False


def school_login_error(user):
    approval = user.get("approval_status", "approved")
    status = user.get("account_status", "active")
    if approval == "pending":
        return "Your account is awaiting approval. Please contact administrator."
    if approval == "rejected":
        return "Your school account request was rejected. Please contact administrator."
    if status == "suspended":
        return "Your account has been suspended. Please contact administrator."
    if status != "active":
        return "Your account is inactive. Please contact administrator."
    if school_is_expired(user.get("expiry_date")):
        return "Your subscription has expired. Please contact administrator."
    return ""
