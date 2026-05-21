import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

from extensions import mongo
from services.fee_service import effective_fee_for_student
from services.payment_service import create_receipt, delete_receipt
from utils.auth import login_required
from utils.helpers import FEE_HEADS, GRADES, active_year, fmt_money, money, oid
from utils.tenant import scoped_query
from utils.pdf_generator import receipt_pdf

receipt_bp = Blueprint("receipts", __name__, url_prefix="/receipts")

RECEIPT_PRINT_PROJECTION = {
    "receipt_no": 1, "receipt_date": 1, "current_payment": 1, "amount_paid": 1,
    "payment_mode": 1, "transaction_ref": 1, "cheque_no": 1,
    "amount_paid_before": 1, "balance_due": 1, "student_id": 1,
}

RECEIPT_STUDENT_PROJECTION = {
    "roll_no": 1, "grade": 1, "student_name": 1,
    "admission_no": 1, "balance_due": 1,
}


@receipt_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    selected = None
    payments = []
    search = request.values.get("search", "").strip()
    receipt_search = request.args.get("receipt_search", "").strip()
    filters = {
        "grade": request.args.get("grade", "").strip(),
        "student_id": request.args.get("student_id", "").strip(),
        "payment_mode": request.args.get("payment_mode", "").strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
    }
    if search:
        search_pattern = re.escape(search)
        selected = mongo.db.students.find_one(scoped_query({
            "academic_year": active_year(),
            "$or": [
                {"admission_no": {"$regex": search_pattern, "$options": "i"}},
                {"student_name": {"$regex": search_pattern, "$options": "i"}},
                {"mobile": {"$regex": search_pattern, "$options": "i"}},
            ],
        }), {"student_name": 1, "admission_no": 1, "mobile": 1, "grade": 1, "student_type": 1, "academic_year": 1, "total_fee": 1, "discount_amount": 1, "net_receivable": 1, "total_paid": 1, "balance_due": 1, "school_id": 1})
        if selected:
            payments = list(mongo.db.payments.find(scoped_query({"student_id": str(selected["_id"])}, selected.get("school_id")), {"receipt_no": 1, "receipt_date": 1, "amount_paid": 1, "payment_mode": 1}).sort("receipt_date", -1).limit(100))
        else:
            flash("No matching student found", "warning")
    receipt_query = scoped_query({"academic_year": active_year()})
    if receipt_search:
        receipt_pattern = re.escape(receipt_search)
        receipt_query["$or"] = [
            {"receipt_no": {"$regex": receipt_pattern, "$options": "i"}},
            {"student_name": {"$regex": receipt_pattern, "$options": "i"}},
            {"mobile": {"$regex": receipt_pattern, "$options": "i"}},
        ]
    if filters["grade"]:
        grade_student_ids = [
            str(row["_id"])
            for row in mongo.db.students.find(scoped_query({"academic_year": active_year(), "grade": filters["grade"]}), {"_id": 1})
        ]
        receipt_query["$and"] = receipt_query.get("$and", [])
        receipt_query["$and"].append({"$or": [{"grade": filters["grade"]}, {"class_name": filters["grade"]}, {"student_id": {"$in": grade_student_ids}}]})
    if filters["student_id"]:
        receipt_query["student_id"] = filters["student_id"]
    if filters["payment_mode"]:
        receipt_query["payment_mode"] = filters["payment_mode"]
    if filters["date_from"] or filters["date_to"]:
        receipt_query["receipt_date"] = {}
        if filters["date_from"]:
            receipt_query["receipt_date"]["$gte"] = filters["date_from"]
        if filters["date_to"]:
            receipt_query["receipt_date"]["$lte"] = filters["date_to"]
    if not receipt_query.get("$and"):
        receipt_query.pop("$and", None)
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 10
    total_receipts = mongo.db.receipts.count_documents(receipt_query)
    recent = list(
        mongo.db.receipts.find(receipt_query, {"receipt_no": 1, "receipt_date": 1, "student_name": 1, "class_name": 1, "grade": 1, "current_payment": 1, "amount_paid": 1, "payment_mode": 1, "balance_due": 1, "created_at": 1})
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    has_history_filters = any(filters.values()) or bool(receipt_search)
    if not recent and not has_history_filters:
        recent = list(mongo.db.payments.find(scoped_query({"academic_year": active_year()}), {"receipt_no": 1, "receipt_date": 1, "student_name": 1, "grade": 1, "amount_paid": 1, "payment_mode": 1, "balance_due": 1, "created_at": 1}).sort("created_at", -1).limit(10))
        total_receipts = len(recent)
        receipt_summary = _receipt_summary_from_rows(recent)
    else:
        receipt_summary = _receipt_summary(receipt_query)
    filter_students_query = scoped_query({"academic_year": active_year()})
    if filters["grade"]:
        filter_students_query["grade"] = filters["grade"]
    filter_students = list(mongo.db.students.find(filter_students_query, {"student_name": 1, "admission_no": 1, "grade": 1}).sort("student_name", 1).limit(1000))
    return render_template(
        "receipts/index.html",
        student=selected,
        payments=payments,
        recent=recent,
        search=search,
        receipt_search=receipt_search,
        filters=filters,
        filter_students=filter_students,
        receipt_summary=receipt_summary,
        grades=GRADES,
        fee_heads=FEE_HEADS,
        page=page,
        per_page=per_page,
        total_receipts=total_receipts,
        active_year=active_year(),
    )


@receipt_bp.route("/save/<student_id>", methods=["POST"])
@login_required
def save(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("receipts.index"))
    try:
        receipt = create_receipt(student, request.form)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("receipts.index", search=student.get("admission_no", "")))
    flash(f"Receipt {receipt['receipt_no']} saved successfully", "success")
    return redirect(url_for("receipts.print_receipt", receipt_no=receipt["receipt_no"]))


@receipt_bp.route("/create", methods=["POST"])
@login_required
def create():
    student = mongo.db.students.find_one(scoped_query({"_id": oid(request.form.get("student_id"))}))
    if not student:
        flash("Select a valid student before saving receipt.", "danger")
        return redirect(url_for("receipts.index"))
    try:
        receipt = create_receipt(student, request.form)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("receipts.index"))
    flash(f"Receipt {receipt['receipt_no']} created successfully", "success")
    return redirect(url_for("receipts.print_receipt", receipt_no=receipt["receipt_no"]))


@receipt_bp.route("/delete/<receipt_no>", methods=["POST"])
@login_required
def delete(receipt_no):
    try:
        delete_receipt(receipt_no)
        flash(f"Receipt {receipt_no} deleted and balances recalculated.", "success")
    except ValueError as error:
        flash(str(error), "danger")
    return redirect(url_for("receipts.index"))


@receipt_bp.route("/api/students")
@login_required
def api_students():
    query = scoped_query({
        "academic_year": request.args.get("academic_year") or active_year(),
    })
    grade = request.args.get("grade", "").strip()
    if grade:
        query["grade"] = grade
    rows = mongo.db.students.find(query, {"student_name": 1, "admission_no": 1, "mobile": 1, "grade": 1}).sort("student_name", 1).limit(1000)
    return jsonify([
        {
            "id": str(row["_id"]),
            "student_name": row.get("student_name"),
            "admission_no": row.get("admission_no"),
            "mobile": row.get("mobile"),
            "grade": row.get("grade"),
        }
        for row in rows
    ])


def _receipt_summary(query):
    totals = next(mongo.db.receipts.aggregate([
        {"$match": scoped_query(query)},
        {"$group": {
            "_id": None,
            "total_receipts": {"$sum": 1},
            "total_collection": {"$sum": {"$ifNull": ["$current_payment", {"$ifNull": ["$amount_paid", 0]}]}},
            "last_payment_date": {"$max": "$receipt_date"},
        }},
    ]), {})
    pending = next(mongo.db.receipts.aggregate([
        {"$match": scoped_query(query)},
        {"$sort": {"student_id": 1, "receipt_date": -1, "created_at": -1}},
        {"$group": {"_id": "$student_id", "balance_due": {"$first": "$balance_due"}}},
        {"$group": {"_id": None, "pending_due": {"$sum": "$balance_due"}}},
    ]), {})
    return {
        "total_receipts": totals.get("total_receipts", 0),
        "total_collection": money(totals.get("total_collection")),
        "pending_due": money(pending.get("pending_due")),
        "last_payment_date": totals.get("last_payment_date") or "--",
    }


def _receipt_summary_from_rows(rows):
    latest_balance_by_student = {}
    for row in sorted(rows, key=lambda item: (item.get("receipt_date") or "", item.get("created_at") or ""), reverse=True):
        student_id = row.get("student_id") or str(row.get("_id", ""))
        latest_balance_by_student.setdefault(student_id, money(row.get("balance_due")))
    return {
        "total_receipts": len(rows),
        "total_collection": sum(money(row.get("current_payment") or row.get("amount_paid")) for row in rows),
        "pending_due": sum(latest_balance_by_student.values()),
        "last_payment_date": max((row.get("receipt_date") or "" for row in rows), default="--"),
    }


@receipt_bp.route("/api/student/<student_id>")
@login_required
def api_student(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}))
    if not student:
        return jsonify({"error": "Student not found"}), 404
    structure = mongo.db.fee_structures.find_one(scoped_query({
        "academic_year": student.get("academic_year"),
        "grade": student.get("grade"),
        "student_type": student.get("student_type"),
    }, student.get("school_id")))
    effective_fee = effective_fee_for_student(student)
    paid = money(student.get("total_paid"))
    balance = max(effective_fee["net_receivable"] - paid, 0)
    fee_heads = [{"key": head["name"], "label": head["name"], "amount": head["amount"]} for head in effective_fee["fee_heads"]]
    return jsonify({
        "id": str(student["_id"]),
        "student_name": student.get("student_name"),
        "admission_no": student.get("admission_no"),
        "mobile": student.get("mobile"),
        "grade": student.get("grade"),
        "student_type": student.get("student_type"),
        "academic_year": student.get("academic_year"),
        "fee_structure_id": str((effective_fee["structure"] or structure)["_id"]) if (effective_fee["structure"] or structure) else "",
        "structure_found": bool(effective_fee["structure"]),
        "total_fee": effective_fee["total_fee"],
        "total_fee_display": fmt_money(effective_fee["total_fee"]),
        "net_receivable": effective_fee["net_receivable"],
        "previously_paid": paid,
        "previously_paid_display": fmt_money(paid),
        "pending_due": balance,
        "pending_due_display": fmt_money(balance),
        "fee_heads": fee_heads,
    })


@receipt_bp.route("/print/<receipt_no>")
@login_required
def print_receipt(receipt_no):
    payment = mongo.db.receipts.find_one(scoped_query({"receipt_no": receipt_no}), RECEIPT_PRINT_PROJECTION) or mongo.db.payments.find_one(scoped_query({"receipt_no": receipt_no}), RECEIPT_PRINT_PROJECTION)
    if not payment:
        flash("Receipt not found", "danger")
        return redirect(url_for("receipts.index"))
    student = mongo.db.students.find_one(scoped_query({"_id": oid(payment.get("student_id"))}, payment.get("school_id")), RECEIPT_STUDENT_PROJECTION)
    if not student:
        flash("Student linked to this receipt was not found", "danger")
        return redirect(url_for("receipts.index"))
    return render_template("receipts/print.html", payment=payment, student=student)


@receipt_bp.route("/pdf/<receipt_no>")
@login_required
def pdf(receipt_no):
    payment = mongo.db.receipts.find_one(scoped_query({"receipt_no": receipt_no}), RECEIPT_PRINT_PROJECTION) or mongo.db.payments.find_one(scoped_query({"receipt_no": receipt_no}), RECEIPT_PRINT_PROJECTION)
    if not payment:
        flash("Receipt not found", "danger")
        return redirect(url_for("receipts.index"))
    student = mongo.db.students.find_one(scoped_query({"_id": oid(payment.get("student_id"))}, payment.get("school_id")), RECEIPT_STUDENT_PROJECTION)
    if not student:
        flash("Student linked to this receipt was not found", "danger")
        return redirect(url_for("receipts.index"))
    return send_file(receipt_pdf(payment, student), mimetype="application/pdf", as_attachment=True, download_name=f"{receipt_no}.pdf")
