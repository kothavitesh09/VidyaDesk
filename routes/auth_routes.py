from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import mongo
from services.saas_service import audit_log, next_school_id, school_login_error

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = mongo.db.users.find_one(
            {"username": request.form.get("username", "").strip()},
            {"username": 1, "password_hash": 1, "full_name": 1, "school_id": 1, "role": 1, "approval_status": 1, "account_status": 1, "expiry_date": 1},
        )
        if user and check_password_hash(user.get("password_hash", ""), request.form.get("password", "")):
            if user.get("role") in {"global_admin", "super_admin"} or user.get("school_id") == "GLOBAL":
                flash("Please use the Super Admin Login for this account.", "warning")
                audit_log("school_login_blocked", "admin", user.get("username"), user.get("school_id"), {"reason": "admin_account_on_school_login"})
                return render_template("auth/login.html")
            school = mongo.db.schools.find_one({"school_id": user.get("school_id")}) or {}
            login_error = school_login_error({**user, **{key: school.get(key, user.get(key)) for key in ("approval_status", "account_status", "expiry_date")}})
            if login_error:
                flash(login_error, "danger")
                audit_log("school_login_blocked", "school", user.get("username"), user.get("school_id"), {"reason": login_error})
                return render_template("auth/login.html")
            session["user_id"] = str(user["_id"])
            session["username"] = user.get("username")
            session["full_name"] = user.get("full_name", user.get("username"))
            session["school_id"] = user.get("school_id")
            session["role"] = user.get("role", "school_user")
            mongo.db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": datetime.utcnow()}})
            audit_log("school_login", "school", user.get("username"), user.get("school_id"))
            return redirect(request.args.get("next") or url_for("dashboard.index"))
        flash("Invalid username or password", "danger")
    return render_template("auth/login.html")


@auth_bp.route("/register-school", methods=["GET", "POST"])
def register_school():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        if mongo.db.users.find_one({"username": username}) or mongo.db.schools.find_one({"email": email}):
            flash("A school account with this username or email already exists.", "danger")
            return render_template("auth/register_school.html")
        school_id = next_school_id()
        school = {
            "school_id": school_id,
            "school_name": request.form.get("school_name", "").strip(),
            "contact_person": request.form.get("contact_person", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "email": email,
            "address": request.form.get("address", "").strip(),
            "approval_status": "pending",
            "account_status": "inactive",
            "expiry_date": (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d"),
            "subscription_plan": "trial",
            "payment_status": "pending",
            "amount_paid": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        mongo.db.schools.insert_one(school)
        mongo.db.users.insert_one({
            "username": username,
            "password_hash": generate_password_hash(request.form.get("password", "")),
            "full_name": school["contact_person"] or school["school_name"],
            "school_id": school_id,
            "role": "school_admin",
            "approval_status": "pending",
            "account_status": "inactive",
            "expiry_date": school["expiry_date"],
            "created_at": datetime.utcnow(),
        })
        audit_log("school_registration_submitted", "school", username, school_id, {"school_name": school["school_name"]})
        flash("School account request submitted. Please wait for admin approval.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register_school.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
