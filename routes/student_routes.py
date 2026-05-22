import os
import re
from datetime import datetime
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from extensions import mongo
from services.fee_service import build_student_fee, effective_fee_for_student, find_fee_structure, fee_payload_from_structure
from services.payment_service import update_student_payment_summary
from utils.auth import login_required
from utils.helpers import GRADES, FEE_HEADS, active_year, money, oid
from utils.tenant import scoped_insert, scoped_query, scoped_set, scoped_update_query, write_school_id

student_bp = Blueprint("students", __name__, url_prefix="/students")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@student_bp.route("/")
@login_required
def index():
    selected_year = request.args.get("academic_year") or active_year()
    query = {"academic_year": selected_year}
    search = request.args.get("q", "").strip()
    grade = request.args.get("grade", "")
    status = request.args.get("status", "")
    student_type = request.args.get("student_type", "")
    due_status = request.args.get("due_status", "")
    if grade:
        query["grade"] = grade
    if status == "Active":
        query["status"] = {"$in": ["Active", None]}
    elif status:
        query["status"] = status
    if student_type:
        query["student_type"] = student_type
    if search:
        search_pattern = re.escape(search)
        query["$or"] = [
            {"admission_no": {"$regex": search_pattern, "$options": "i"}},
            {"student_name": {"$regex": search_pattern, "$options": "i"}},
            {"mobile": {"$regex": search_pattern, "$options": "i"}},
        ]
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 12
    projection = {
        "admission_no": 1, "student_name": 1, "father_name": 1, "grade": 1,
        "student_type": 1, "mobile": 1, "status": 1, "academic_year": 1,
        "balance_due": 1, "net_receivable": 1, "total_paid": 1,
    }
    all_rows = list(mongo.db.students.find(scoped_query(query), projection).sort("student_name", 1))
    for row in all_rows:
        row["display_balance_due"] = money(row.get("balance_due"))
        if not row["display_balance_due"]:
            row["display_balance_due"] = max(money(row.get("net_receivable")) - money(row.get("total_paid")), 0)
    if due_status == "no_due":
        all_rows = [row for row in all_rows if money(row.get("display_balance_due")) <= 0]
    elif due_status == "medium_due":
        all_rows = [row for row in all_rows if 0 < money(row.get("display_balance_due")) <= 10000]
    elif due_status == "high_due":
        all_rows = [row for row in all_rows if money(row.get("display_balance_due")) > 10000]
    total = len(all_rows)
    rows = all_rows[(page - 1) * per_page:page * per_page]
    stats = _student_stats(selected_year)
    return render_template(
        "students/index.html",
        students=rows,
        grades=GRADES,
        page=page,
        total=total,
        per_page=per_page,
        q=search,
        grade=grade,
        selected_year=selected_year,
        status=status,
        student_type=student_type,
        due_status=due_status,
        stats=stats,
        fee_structures=_fee_structure_options(),
    )


@student_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        if not _valid_parent_email(request.form):
            flash("Enter a valid parent email address or leave it blank.", "danger")
            return render_template("students/form.html", student=request.form, grades=GRADES, fee_heads=FEE_HEADS, fee_structures=_fee_structure_options(), active_year=active_year())
        payload = _student_payload(request.form)
        if not request.form.get("status"):
            payload["status"] = "Active"
        payload.update(build_student_fee(request.form))
        payload["created_at"] = datetime.utcnow()
        photo = _save_photo()
        if photo:
            payload["photo"] = photo
        result = mongo.db.students.insert_one(scoped_insert(payload))
        update_student_payment_summary(result.inserted_id, payload.get("school_id"))
        flash("Student added successfully", "success")
        return redirect(url_for("students.index"))
    return render_template("students/form.html", student={}, grades=GRADES, fee_heads=FEE_HEADS, fee_structures=_fee_structure_options(), active_year=active_year())


@student_bp.route("/<student_id>")
@login_required
def view(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("students.index"))
    payments = list(mongo.db.payments.find(scoped_query({"student_id": student_id}, student.get("school_id")), {"receipt_no": 1, "receipt_date": 1, "amount_paid": 1, "payment_mode": 1}).sort("receipt_date", -1).limit(250))
    effective_fee = effective_fee_for_student(student)
    total_paid = sum(money(payment.get("amount_paid")) for payment in payments)
    balance_due = max(effective_fee["net_receivable"] - total_paid, 0)
    return render_template("students/view.html", student=student, payments=payments, fee_heads=FEE_HEADS, effective_fee=effective_fee, total_paid=total_paid, balance_due=balance_due)


@student_bp.route("/<student_id>/edit", methods=["GET", "POST"])
@login_required
def edit(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("students.index"))
    if request.method == "POST":
        if not _valid_parent_email(request.form):
            flash("Enter a valid parent email address or leave it blank.", "danger")
            return render_template("students/form.html", student={**student, **request.form}, grades=GRADES, fee_heads=FEE_HEADS, fee_structures=_fee_structure_options(), active_year=active_year())
        payload = _student_payload(request.form)
        if "total_paid" not in request.form:
            payload["total_paid"] = money(student.get("total_paid"))
        payload.update(build_student_fee(request.form))
        photo = _save_photo()
        if photo:
            payload["photo"] = photo
        payload["updated_at"] = datetime.utcnow()
        mongo.db.students.update_one(scoped_update_query({"_id": oid(student_id)}, student.get("school_id")), {"$set": scoped_set(payload)})
        update_student_payment_summary(student_id, student.get("school_id"))
        flash("Student updated successfully", "success")
        return redirect(url_for("students.view", student_id=student_id))
    return render_template("students/form.html", student=student, grades=GRADES, fee_heads=FEE_HEADS, fee_structures=_fee_structure_options(), active_year=active_year())


@student_bp.route("/<student_id>/delete", methods=["POST"])
@login_required
def delete(student_id):
    mongo.db.students.delete_one(scoped_update_query({"_id": oid(student_id)}))
    mongo.db.payments.delete_many(scoped_update_query({"student_id": student_id}))
    mongo.db.receipts.delete_many(scoped_update_query({"student_id": student_id}))
    mongo.db.discounts.delete_many(scoped_update_query({"student_id": student_id}))
    flash("Student deleted", "success")
    return redirect(url_for("students.index"))


@student_bp.route("/<student_id>/api")
@login_required
def profile_api(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        return jsonify({"error": "Student not found"}), 404
    payments = list(mongo.db.payments.find(scoped_query({"student_id": student_id}, student.get("school_id")), {"receipt_no": 1, "receipt_date": 1, "amount_paid": 1, "payment_mode": 1, "remarks": 1}).sort("receipt_date", -1).limit(100))
    effective_fee = effective_fee_for_student(student)
    total_paid = sum(money(payment.get("amount_paid")) for payment in payments)
    balance_due = max(effective_fee["net_receivable"] - total_paid, 0)
    history = list(mongo.db.students.find(scoped_query({"admission_no": student.get("admission_no")}, student.get("school_id")), {"admission_no": 1, "roll_no": 1, "student_name": 1, "gender": 1, "dob": 1, "academic_year": 1, "grade": 1, "previous_grade": 1, "current_grade": 1, "student_status": 1, "student_type": 1, "status": 1, "father_name": 1, "mother_name": 1, "mobile": 1, "alternate_number": 1, "address": 1, "promotion_date": 1, "left_date": 1, "left_reason": 1, "tc_issued": 1}).sort("academic_year", -1).limit(20))
    return jsonify({
        "student": _student_json(student),
        "fee": {
            "heads": [{"key": head["name"], "label": head["name"], "amount": head["amount"]} for head in effective_fee["fee_heads"]],
            "total_fee": effective_fee["total_fee"],
            "discount": effective_fee["discount"],
            "net_receivable": effective_fee["net_receivable"],
            "total_paid": total_paid,
            "balance_due": balance_due,
        },
        "payments": [_payment_json(payment) for payment in payments],
        "history": [_student_json(row) for row in history],
    })


@student_bp.route("/<student_id>/promote", methods=["POST"])
@login_required
def promote(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("students.index"))
    next_year = request.form.get("next_academic_year", "").strip()
    next_grade = request.form.get("promote_to_grade", "").strip()
    if not next_year or not next_grade:
        flash("Select next academic year and grade before promotion.", "danger")
        return redirect(url_for("students.index"))
    if mongo.db.students.find_one(scoped_query({"admission_no": student.get("admission_no"), "academic_year": next_year, "grade": next_grade}, student.get("school_id"))):
        flash("A promoted record already exists for this student, year, and grade.", "warning")
        return redirect(url_for("students.index"))

    new_doc = _promoted_student_payload(student, next_year, next_grade, request.form.get("remarks", ""), request.form)
    result = mongo.db.students.insert_one(scoped_insert(new_doc, student.get("school_id")))
    update_student_payment_summary(result.inserted_id, student.get("school_id"))
    mongo.db.students.update_one(
        scoped_update_query({"_id": student["_id"]}, student.get("school_id")),
        {"$set": {
            "status": "Promoted",
            "promoted_to": str(result.inserted_id),
            "promotion_date": datetime.utcnow(),
            "promotion_remarks": request.form.get("remarks", ""),
            "updated_at": datetime.utcnow(),
        }},
    )
    flash("Student promoted successfully. Previous year history is preserved.", "success")
    return redirect(url_for("students.index", academic_year=next_year, status="Active"))


@student_bp.route("/<student_id>/mark-left", methods=["POST"])
@login_required
def mark_left(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("students.index"))
    mongo.db.students.update_one(
        scoped_update_query({"_id": student["_id"]}, student.get("school_id")),
        {"$set": {
            "status": "Left",
            "left_date": request.form.get("left_date"),
            "left_reason": request.form.get("left_reason", "").strip(),
            "tc_issued": request.form.get("tc_issued") == "Yes",
            "left_remarks": request.form.get("remarks", "").strip(),
            "updated_at": datetime.utcnow(),
        }},
    )
    flash("Student marked as left. Record remains available in reports and history.", "success")
    return redirect(url_for("students.index"))


@student_bp.route("/bulk-promote", methods=["POST"])
@login_required
def bulk_promote():
    current_year = request.form.get("current_academic_year") or active_year()
    next_year = request.form.get("next_academic_year", "").strip()
    current_grade = request.form.get("current_grade", "").strip()
    next_grade = request.form.get("next_grade", "").strip()
    student_ids = request.form.getlist("student_ids")
    if not all([current_year, next_year, current_grade, next_grade]) or not student_ids:
        flash("Complete the bulk promotion selections before confirming.", "danger")
        return redirect(url_for("students.index"))
    promoted = 0
    for student_id in student_ids:
        student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id), "academic_year": current_year, "grade": current_grade}))
        if not student:
            continue
        exists = mongo.db.students.find_one(scoped_query({"admission_no": student.get("admission_no"), "academic_year": next_year, "grade": next_grade}, student.get("school_id")))
        if exists:
            continue
        new_doc = _promoted_student_payload(student, next_year, next_grade, "Bulk promotion", request.form)
        result = mongo.db.students.insert_one(scoped_insert(new_doc, student.get("school_id")))
        update_student_payment_summary(result.inserted_id, student.get("school_id"))
        mongo.db.students.update_one(
            scoped_update_query({"_id": student["_id"]}, student.get("school_id")),
            {"$set": {
                "status": "Promoted",
                "promoted_to": str(result.inserted_id),
                "promotion_date": datetime.utcnow(),
                "promotion_remarks": "Bulk promotion",
                "updated_at": datetime.utcnow(),
            }},
        )
        promoted += 1
    flash(f"{promoted} student(s) promoted successfully.", "success")
    return redirect(url_for("students.index", academic_year=next_year, grade=next_grade, status="Active"))


@student_bp.route("/api/eligible")
@login_required
def eligible_students():
    year = request.args.get("academic_year") or active_year()
    grade = request.args.get("grade", "")
    query = {"academic_year": year, "status": {"$in": ["Active", None]}}
    if grade:
        query["grade"] = grade
    rows = mongo.db.students.find(scoped_query(query), {"admission_no": 1, "student_name": 1}).sort("student_name", 1)
    return jsonify([{"id": str(row["_id"]), "text": f"{row.get('admission_no')} - {row.get('student_name')}"} for row in rows])


@student_bp.route("/fee-lookup")
@login_required
def fee_lookup():
    structure = find_fee_structure(
        request.args.get("academic_year"),
        request.args.get("grade"),
        request.args.get("student_type"),
        request.args.get("fee_structure_id"),
    )
    payload = fee_payload_from_structure(structure)
    payload["structure_found"] = bool(structure)
    payload["fee_structure_id"] = str(structure["_id"]) if structure else ""
    payload["fee_structure_name"] = structure.get("fee_structure_name", "") if structure else ""
    return jsonify(payload)


@student_bp.route("/fee-structures")
@login_required
def fee_structures():
    rows = _fee_structure_options(
        academic_year=request.args.get("academic_year", "").strip(),
        grade=request.args.get("grade", "").strip(),
    )
    return jsonify(rows)


@student_bp.route("/api/by-grade/<grade>")
@login_required
def by_grade(grade):
    rows = mongo.db.students.find(scoped_query({"academic_year": active_year(), "grade": grade, "status": {"$ne": "Left"}}), {"admission_no": 1, "student_name": 1}).sort("student_name", 1)
    return jsonify([{"id": str(row["_id"]), "text": f"{row.get('admission_no')} - {row.get('student_name')}"} for row in rows])


def _student_payload(form):
    return scoped_insert({
        "admission_no": form.get("admission_no", "").strip(),
        "roll_no": form.get("roll_no", "").strip(),
        "student_name": form.get("student_name", "").strip(),
        "gender": form.get("gender"),
        "dob": form.get("dob"),
        "academic_year": form.get("academic_year"),
        "grade": form.get("grade"),
        "section": form.get("section", "").strip(),
        "current_grade": form.get("grade"),
        "previous_grade": form.get("previous_grade", ""),
        "status": form.get("status") or "Active",
        "student_status": form.get("student_status"),
        "student_type": form.get("student_type"),
        "father_name": form.get("father_name", "").strip(),
        "mother_name": form.get("mother_name", "").strip(),
        "mobile": form.get("mobile", "").strip(),
        "parent_email": form.get("parent_email", "").strip().lower(),
        "alternate_number": form.get("alternate_number", "").strip(),
        "address": form.get("address", "").strip(),
        "total_paid": money(form.get("total_paid")),
    })


def _save_photo():
    file = request.files.get("photo")
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    unique = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    school_id = write_school_id()
    school_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], school_id)
    os.makedirs(school_folder, exist_ok=True)
    file.save(os.path.join(school_folder, unique))
    return f"{school_id}/{unique}"


def _promoted_student_payload(student, next_year, next_grade, remarks, form=None):
    excluded = {"_id", "created_at", "updated_at", "total_paid", "balance_due", "promoted_to", "left_date", "left_reason", "tc_issued", "left_remarks"}
    payload = {key: value for key, value in student.items() if key not in excluded}
    payload.update({
        "academic_year": next_year,
        "grade": next_grade,
        "previous_grade": student.get("grade"),
        "current_grade": next_grade,
        "status": "Active",
        "student_status": "Old",
        "promoted_from": str(student["_id"]),
        "promoted_to": "",
        "promotion_date": datetime.utcnow(),
        "promotion_remarks": remarks,
        "total_paid": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    keep_existing = (form or {}).get("keep_existing_fee_structure", "on") == "on"
    if keep_existing:
        effective_fee = effective_fee_for_student(student)
        discount = money(student.get("discount_amount"))
        fee = {
            **effective_fee["fee_fields"],
            "fee_structure_mode": student.get("fee_structure_mode") or "existing",
            "assigned_fee_structure_id": student.get("assigned_fee_structure_id", ""),
            "assigned_fee_structure_year": student.get("assigned_fee_structure_year") or student.get("academic_year"),
            "manual_fee_structure": student.get("manual_fee_structure", {}),
            "fee_heads": effective_fee["fee_heads"],
            "total_fee": effective_fee["total_fee"],
            "total_amount": effective_fee["total_fee"],
            "discount_amount": discount,
            "discount_reason": student.get("discount_reason", ""),
            "net_receivable": max(effective_fee["total_fee"] - discount, 0),
        }
    else:
        fee_form = {
            "academic_year": next_year,
            "grade": next_grade,
            "student_type": student.get("student_type"),
            "discount_amount": student.get("discount_amount", 0),
            "discount_reason": student.get("discount_reason", ""),
            "fee_structure_mode": "existing",
            "assigned_fee_structure_id": (form or {}).get("assigned_fee_structure_id", ""),
            "assigned_fee_structure_year": (form or {}).get("assigned_fee_structure_year", next_year),
        }
        fee = build_student_fee(_StaticForm(fee_form))
    payload.update(fee)
    payload["balance_due"] = payload.get("net_receivable", 0)
    return payload


class _StaticForm(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


def _student_stats(year):
    query = scoped_query({"academic_year": year})
    stats = {
        "total_students": mongo.db.students.count_documents(query),
        "active_students": 0,
        "promoted_students": 0,
        "left_students": 0,
        "total_due": 0,
    }
    status_rows = mongo.db.students.aggregate([
        {"$match": query},
        {"$group": {"_id": {"$ifNull": ["$status", "Active"]}, "count": {"$sum": 1}}},
    ])
    for row in status_rows:
        status = row.get("_id") or "Active"
        if status == "Active":
            stats["active_students"] += row.get("count", 0)
        elif status == "Promoted":
            stats["promoted_students"] += row.get("count", 0)
        elif status == "Left":
            stats["left_students"] += row.get("count", 0)
    due_row = next(mongo.db.students.aggregate([
        {"$match": query},
        {"$group": {"_id": None, "total_due": {"$sum": {"$ifNull": ["$balance_due", 0]}}}},
    ]), {})
    stats["total_due"] = money(due_row.get("total_due"))
    return stats


def _student_json(student):
    return {
        "id": str(student["_id"]),
        "admission_no": student.get("admission_no", ""),
        "roll_no": student.get("roll_no", ""),
        "student_name": student.get("student_name", ""),
        "gender": student.get("gender", ""),
        "dob": student.get("dob", ""),
        "academic_year": student.get("academic_year", ""),
        "grade": student.get("grade", ""),
        "section": student.get("section", ""),
        "previous_grade": student.get("previous_grade", ""),
        "current_grade": student.get("current_grade") or student.get("grade", ""),
        "student_status": student.get("student_status", ""),
        "student_type": student.get("student_type", ""),
        "status": student.get("status") or "Active",
        "father_name": student.get("father_name", ""),
        "mother_name": student.get("mother_name", ""),
        "mobile": student.get("mobile", ""),
        "parent_email": student.get("parent_email", ""),
        "alternate_number": student.get("alternate_number", ""),
        "address": student.get("address", ""),
        "promotion_date": str(student.get("promotion_date", "")),
        "left_date": student.get("left_date", ""),
        "left_reason": student.get("left_reason", ""),
        "tc_issued": bool(student.get("tc_issued")),
    }


def _valid_parent_email(form):
    email = form.get("parent_email", "").strip()
    return not email or bool(EMAIL_RE.match(email))


def _fee_structure_options(academic_year="", grade=""):
    query = {}
    if academic_year:
        query["academic_year"] = academic_year
    if grade:
        query["$or"] = [{"grade": grade}, {"class_name": grade}]
    projection = {"academic_year": 1, "grade": 1, "class_name": 1, "fee_structure_name": 1, "fee_structure_type": 1, "total_amount": 1, "total_fee": 1}
    rows = mongo.db.fee_structures.find(scoped_query(query), projection).sort([("academic_year", -1), ("grade", 1), ("fee_structure_name", 1)])
    return [
        {
            "id": str(row["_id"]),
            "academic_year": row.get("academic_year", ""),
            "grade": row.get("grade") or row.get("class_name", ""),
            "name": row.get("fee_structure_name") or f"{row.get('grade', '')} Fee Structure",
            "type": row.get("fee_structure_type", "standard"),
            "total_amount": money(row.get("total_amount", row.get("total_fee", 0))),
        }
        for row in rows
    ]


def _payment_json(payment):
    return {
        "receipt_no": payment.get("receipt_no", ""),
        "receipt_date": payment.get("receipt_date", ""),
        "amount_paid": money(payment.get("amount_paid")),
        "payment_mode": payment.get("payment_mode", ""),
        "remarks": payment.get("remarks", ""),
    }
