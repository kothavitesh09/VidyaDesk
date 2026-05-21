from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import mongo
from services.saas_service import audit_log, school_is_expired
from utils.auth import super_admin_required
from utils.helpers import money

super_admin_bp = Blueprint("super_admin", __name__, url_prefix="/super-admin")


@super_admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        admin = mongo.db.admin_users.find_one({"username": request.form.get("username", "").strip()})
        if admin and check_password_hash(admin.get("password_hash", ""), request.form.get("password", "")):
            session.clear()
            session["admin_user_id"] = str(admin["_id"])
            session["admin_username"] = admin.get("username")
            session["admin_role"] = admin.get("role", "super_admin")
            mongo.db.admin_users.update_one({"_id": admin["_id"]}, {"$set": {"last_login": datetime.utcnow()}})
            audit_log("super_admin_login", "super_admin", admin.get("username"))
            return redirect(request.args.get("next") or url_for("super_admin.dashboard"))
        flash("Invalid super admin username or password.", "danger")
    return render_template("super_admin/login.html")


@super_admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("super_admin.login"))


@super_admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    stats = _dashboard_stats()
    recent_logs = list(mongo.db.audit_logs.find({}, {"action": 1, "actor_type": 1, "actor_username": 1, "school_id": 1, "created_at": 1}).sort("created_at", -1).limit(8))
    return render_template("super_admin/dashboard.html", stats=stats, recent_logs=recent_logs)


@super_admin_bp.route("/school-requests")
@super_admin_required
def school_requests():
    schools = list(mongo.db.schools.find({"approval_status": "pending"}).sort("created_at", -1))
    return render_template("super_admin/school_requests.html", schools=schools)


@super_admin_bp.route("/schools")
@super_admin_required
def schools():
    schools = list(mongo.db.schools.find({}).sort("created_at", -1))
    return render_template("super_admin/schools.html", schools=schools)


@super_admin_bp.route("/schools/<school_id>/approve", methods=["POST"])
@super_admin_required
def approve_school(school_id):
    expiry_date = request.form.get("expiry_date") or (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d")
    _set_school_and_users(school_id, {
        "approval_status": "approved",
        "account_status": "active",
        "expiry_date": expiry_date,
        "updated_at": datetime.utcnow(),
    })
    audit_log("school_approved", "super_admin", session.get("admin_username"), school_id)
    flash("School approved and activated.", "success")
    return redirect(request.referrer or url_for("super_admin.school_requests"))


@super_admin_bp.route("/schools/<school_id>/reject", methods=["POST"])
@super_admin_required
def reject_school(school_id):
    _set_school_and_users(school_id, {
        "approval_status": "rejected",
        "account_status": "inactive",
        "updated_at": datetime.utcnow(),
    })
    audit_log("school_rejected", "super_admin", session.get("admin_username"), school_id)
    flash("School request rejected.", "success")
    return redirect(request.referrer or url_for("super_admin.school_requests"))


@super_admin_bp.route("/schools/<school_id>/status", methods=["POST"])
@super_admin_required
def update_school_status(school_id):
    status = request.form.get("account_status", "inactive")
    mongo.db.schools.update_one({"school_id": school_id}, {"$set": {"account_status": status, "updated_at": datetime.utcnow()}})
    mongo.db.users.update_many({"school_id": school_id}, {"$set": {"account_status": status}})
    audit_log(f"school_{status}", "super_admin", session.get("admin_username"), school_id)
    flash("School status updated.", "success")
    return redirect(request.referrer or url_for("super_admin.schools"))


@super_admin_bp.route("/schools/<school_id>/reset-password", methods=["POST"])
@super_admin_required
def reset_school_password(school_id):
    password = request.form.get("password", "").strip()
    if len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(request.referrer or url_for("super_admin.schools"))
    mongo.db.users.update_many({"school_id": school_id}, {"$set": {"password_hash": generate_password_hash(password)}})
    audit_log("school_password_reset", "super_admin", session.get("admin_username"), school_id)
    flash("School password reset.", "success")
    return redirect(request.referrer or url_for("super_admin.schools"))


@super_admin_bp.route("/schools/<school_id>/delete", methods=["POST"])
@super_admin_required
def delete_school(school_id):
    mongo.db.schools.delete_one({"school_id": school_id})
    mongo.db.users.delete_many({"school_id": school_id})
    audit_log("school_deleted", "super_admin", session.get("admin_username"), school_id)
    flash("School account deleted. ERP data remains untouched for safety.", "success")
    return redirect(url_for("super_admin.schools"))


@super_admin_bp.route("/billing")
@super_admin_required
def billing():
    schools = list(mongo.db.schools.find({}).sort("expiry_date", 1))
    return render_template("super_admin/billing.html", schools=schools)


@super_admin_bp.route("/schools/<school_id>/renew", methods=["POST"])
@super_admin_required
def renew_school(school_id):
    amount = money(request.form.get("amount_paid"))
    expiry_date = request.form.get("expiry_date")
    payload = {
        "expiry_date": expiry_date,
        "payment_status": request.form.get("payment_status", "paid"),
        "amount_paid": amount,
        "updated_at": datetime.utcnow(),
    }
    mongo.db.schools.update_one({"school_id": school_id}, {"$set": payload})
    mongo.db.users.update_many({"school_id": school_id}, {"$set": {"expiry_date": expiry_date}})
    mongo.db.subscriptions.insert_one({
        "school_id": school_id,
        "expiry_date": expiry_date,
        "payment_status": payload["payment_status"],
        "amount_paid": amount,
        "created_at": datetime.utcnow(),
    })
    audit_log("school_renewed", "super_admin", session.get("admin_username"), school_id, payload)
    flash("Renewal updated.", "success")
    return redirect(url_for("super_admin.billing"))


@super_admin_bp.route("/audit-logs")
@super_admin_required
def audit_logs():
    logs = list(mongo.db.audit_logs.find({}).sort("created_at", -1).limit(200))
    return render_template("super_admin/audit_logs.html", logs=logs)


@super_admin_bp.route("/settings", methods=["GET", "POST"])
@super_admin_required
def settings():
    if request.method == "POST":
        payload = {
            "software_name": request.form.get("software_name", "VidyaDesk"),
            "maintenance_mode": request.form.get("maintenance_mode") == "on",
            "email_settings": request.form.get("email_settings", "").strip(),
            "payment_settings": request.form.get("payment_settings", "").strip(),
            "updated_at": datetime.utcnow(),
        }
        mongo.db.global_settings.update_one({"key": "saas"}, {"$set": payload}, upsert=True)
        audit_log("global_settings_updated", "super_admin", session.get("admin_username"))
        flash("Global settings saved.", "success")
        return redirect(url_for("super_admin.settings"))
    settings = mongo.db.global_settings.find_one({"key": "saas"}) or {}
    return render_template("super_admin/settings.html", settings=settings)


def _dashboard_stats():
    schools = list(mongo.db.schools.find({}, {"school_id": 1, "approval_status": 1, "account_status": 1, "expiry_date": 1, "amount_paid": 1}))
    return {
        "total_schools": len(schools),
        "active_schools": sum(1 for school in schools if school.get("account_status") == "active"),
        "pending_approvals": sum(1 for school in schools if school.get("approval_status") == "pending"),
        "expired_schools": sum(1 for school in schools if school_is_expired(school.get("expiry_date"))),
        "total_students": mongo.db.students.count_documents({}),
        "revenue": sum(money(school.get("amount_paid")) for school in schools),
    }


def _set_school_and_users(school_id, payload):
    mongo.db.schools.update_one({"school_id": school_id}, {"$set": payload})
    user_payload = {key: value for key, value in payload.items() if key in {"approval_status", "account_status", "expiry_date"}}
    if user_payload:
        mongo.db.users.update_many({"school_id": school_id}, {"$set": user_payload})
