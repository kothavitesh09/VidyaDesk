import re
import time
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from extensions import mongo
from services.fee_service import effective_fee_for_student
from services.mail_service import send_email
from utils.auth import login_required
from utils.helpers import GRADES, active_year, fmt_money, money, oid
from utils.tenant import current_school_id, scoped_insert, scoped_query, scoped_update_query


communication_bp = Blueprint("communication", __name__, url_prefix="/communication")

PLACEHOLDERS = [
    "student_name",
    "class_name",
    "section",
    "roll_no",
    "academic_year",
    "total_fee",
    "paid_amount",
    "due_amount",
    "school_name",
    "today_date",
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


@communication_bp.route("/")
@login_required
def index():
    return redirect(url_for("communication.templates"))


@communication_bp.route("/templates", methods=["GET", "POST"])
@login_required
def templates():
    if request.method == "POST":
        template_id = request.form.get("template_id", "").strip()
        payload = {
            "template_name": request.form.get("template_name", "").strip(),
            "subject": request.form.get("subject", "").strip(),
            "body": request.form.get("body", "").strip(),
            "updated_at": datetime.utcnow(),
        }
        if not all([payload["template_name"], payload["subject"], payload["body"]]):
            flash("Template name, subject and body are required.", "danger")
            return redirect(url_for("communication.templates"))
        if template_id and oid(template_id):
            mongo.db.email_templates.update_one(
                scoped_update_query({"_id": oid(template_id)}),
                {"$set": payload},
            )
            flash("Email template updated successfully.", "success")
        else:
            payload["created_at"] = datetime.utcnow()
            mongo.db.email_templates.insert_one(scoped_insert(payload))
            flash("Email template created successfully.", "success")
        return redirect(url_for("communication.templates"))

    rows = list(mongo.db.email_templates.find(scoped_query({})).sort("updated_at", -1))
    return render_template("communication/templates.html", templates=rows, placeholders=PLACEHOLDERS, sample_context=_sample_context())


@communication_bp.route("/templates/<template_id>/delete", methods=["POST"])
@login_required
def delete_template(template_id):
    mongo.db.email_templates.delete_one(scoped_update_query({"_id": oid(template_id)}))
    flash("Email template deleted.", "success")
    return redirect(url_for("communication.templates"))


@communication_bp.route("/send", methods=["GET", "POST"])
@login_required
def send():
    templates = list(mongo.db.email_templates.find(scoped_query({}), {"template_name": 1, "subject": 1}).sort("template_name", 1))
    if request.method == "POST":
        template = mongo.db.email_templates.find_one(scoped_query({"_id": oid(request.form.get("template_id"))}))
        if not template:
            flash("Select a valid email template before sending.", "danger")
            return redirect(url_for("communication.send"))
        students = _target_students(request.form)
        if not students:
            flash("No students with valid parent email matched your selection.", "warning")
            return redirect(url_for("communication.send"))

        summary = _send_bulk(template, students)
        flash(f"Email sending completed. Success: {summary['success_count']}, Failed: {summary['failed_count']}.", "success" if summary["failed_count"] == 0 else "warning")
        return redirect(url_for("communication.history"))

    selected_year = request.args.get("academic_year") or active_year()
    students = _student_options(selected_year)
    return render_template(
        "communication/send.html",
        templates=templates,
        grades=GRADES,
        students=students,
        active_year=selected_year,
        placeholders=PLACEHOLDERS,
    )


@communication_bp.route("/preview", methods=["POST"])
@login_required
def preview():
    template = mongo.db.email_templates.find_one(scoped_query({"_id": oid(request.form.get("template_id"))}))
    student = mongo.db.students.find_one(scoped_query({"_id": oid(request.form.get("student_id"))}))
    if not template:
        return jsonify({"error": "Select a template first."}), 400
    if not student:
        student = mongo.db.students.find_one(scoped_query({"parent_email": {"$regex": ".+"}}), sort=[("student_name", 1)])
    context = _student_context(student) if student else _sample_context()
    return jsonify({
        "subject": _render_template_text(template.get("subject", ""), context),
        "body": _render_template_text(template.get("body", ""), context),
    })


@communication_bp.route("/history")
@login_required
def history():
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 25
    query = {}
    status = request.args.get("status", "").strip()
    if status:
        query["status"] = status
    total = mongo.db.email_logs.count_documents(scoped_query(query))
    logs = list(
        mongo.db.email_logs.find(scoped_query(query))
        .sort("sent_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    return render_template("communication/history.html", logs=logs, page=page, per_page=per_page, total=total, status=status)


@communication_bp.route("/api/students")
@login_required
def api_students():
    rows = _student_options(request.args.get("academic_year") or active_year(), request.args.get("grade", ""))
    return jsonify(rows)


def _target_students(form):
    query = {
        "academic_year": form.get("academic_year") or active_year(),
        "status": {"$in": ["Active", None]},
        "parent_email": {"$regex": ".+"},
    }
    scope = form.get("recipient_scope", "single")
    if scope == "single":
        query["_id"] = oid(form.get("student_id"))
    elif scope == "class":
        query["grade"] = form.get("grade", "")
    elif scope == "section":
        query["grade"] = form.get("grade", "")
        query["section"] = form.get("section", "").strip()
    elif scope == "due":
        pass
    elif scope != "all":
        return []

    rows = list(mongo.db.students.find(scoped_query(query)).sort("student_name", 1))
    valid_rows = [row for row in rows if _valid_email(row.get("parent_email"))]
    if scope == "due":
        valid_rows = [row for row in valid_rows if _student_context(row)["due_amount_value"] > 0]
    return valid_rows


def _send_bulk(template, students):
    summary = {"success_count": 0, "failed_count": 0}
    delay = max(float(current_app.config.get("MAIL_SEND_DELAY_SECONDS", 0.2)), 0)
    for index, student in enumerate(students):
        context = _student_context(student)
        subject = _render_template_text(template.get("subject", ""), context)
        body = _render_template_text(template.get("body", ""), context)
        result = send_email(student.get("parent_email", ""), subject, body)
        status = "success" if result.get("success") else "failed"
        if status == "success":
            summary["success_count"] += 1
        else:
            summary["failed_count"] += 1
        mongo.db.email_logs.insert_one(scoped_insert({
            "recipient": student.get("parent_email", ""),
            "subject": subject,
            "status": status,
            "error": result.get("error", ""),
            "sent_at": datetime.utcnow(),
        }, student.get("school_id") or current_school_id()))
        if delay and index < len(students) - 1:
            time.sleep(delay)
    return summary


def _student_options(year, grade=""):
    query = {"academic_year": year, "status": {"$in": ["Active", None]}, "parent_email": {"$regex": ".+"}}
    if grade:
        query["grade"] = grade
    projection = {"student_name": 1, "admission_no": 1, "grade": 1, "section": 1, "parent_email": 1}
    rows = mongo.db.students.find(scoped_query(query), projection).sort("student_name", 1)
    return [
        {
            "id": str(row["_id"]),
            "text": f"{row.get('student_name', '')} ({row.get('admission_no', 'No Admission No')})",
            "grade": row.get("grade", ""),
            "section": row.get("section", ""),
            "email": row.get("parent_email", ""),
        }
        for row in rows
        if _valid_email(row.get("parent_email"))
    ]


def _student_context(student):
    effective_fee = effective_fee_for_student(student)
    total_paid = money(student.get("total_paid"))
    due_amount = max(effective_fee["net_receivable"] - total_paid, 0)
    school = mongo.db.schools.find_one({"school_id": student.get("school_id") or current_school_id()}, {"school_name": 1}) or {}
    return {
        "student_name": student.get("student_name", ""),
        "class_name": student.get("class_name") or student.get("grade", ""),
        "section": student.get("section", ""),
        "roll_no": student.get("roll_no", ""),
        "academic_year": student.get("academic_year", ""),
        "total_fee": fmt_money(effective_fee["total_fee"]),
        "paid_amount": fmt_money(total_paid),
        "due_amount": fmt_money(due_amount),
        "due_amount_value": due_amount,
        "school_name": school.get("school_name") or "VidyaDesk School",
        "today_date": datetime.now().strftime("%Y-%m-%d"),
    }


def _sample_context():
    return {
        "student_name": "Aarav Kumar",
        "class_name": "Grade-V",
        "section": "A",
        "roll_no": "12",
        "academic_year": active_year(),
        "total_fee": "45,000.00",
        "paid_amount": "30,000.00",
        "due_amount": "15,000.00",
        "school_name": "VidyaDesk School",
        "today_date": datetime.now().strftime("%Y-%m-%d"),
    }


def _render_template_text(text, context):
    return TOKEN_RE.sub(lambda match: str(context.get(match.group(1), match.group(0))), text or "")


def _valid_email(value):
    return bool(EMAIL_RE.match((value or "").strip()))
