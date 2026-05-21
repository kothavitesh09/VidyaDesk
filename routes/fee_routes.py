from datetime import datetime
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from extensions import mongo
from services.fee_service import build_fee_structure, sync_students_with_fee_structure
from utils.auth import login_required
from utils.helpers import GRADES, FEE_HEADS, active_year, oid
from utils.tenant import scoped_insert, scoped_query, scoped_set, scoped_update_query

fee_bp = Blueprint("fees", __name__, url_prefix="/fee-structure")


@fee_bp.route("/")
@login_required
def index():
    selected_year = request.args.get("academic_year") or active_year()
    projection = {"academic_year": 1, "grade": 1, "class_name": 1, "student_type": 1, "fee_structure_name": 1, "fee_structure_type": 1, "fee_heads": 1, "total_amount": 1, "total_fee": 1}
    rows = list(mongo.db.fee_structures.find(scoped_query({"academic_year": selected_year}), projection).sort([("grade", 1), ("fee_structure_name", 1)]))
    return render_template("fee_structure/index.html", rows=rows, edit_row=None, grades=GRADES, fee_heads=FEE_HEADS, active_year=selected_year)


@fee_bp.route("/<item_id>/edit")
@login_required
def edit(item_id):
    selected_year = request.args.get("academic_year") or active_year()
    projection = {"academic_year": 1, "grade": 1, "class_name": 1, "student_type": 1, "fee_structure_name": 1, "fee_structure_type": 1, "fee_heads": 1, "total_amount": 1, "total_fee": 1}
    rows = list(mongo.db.fee_structures.find(scoped_query({"academic_year": selected_year}), projection).sort([("grade", 1), ("fee_structure_name", 1)]))
    edit_row = mongo.db.fee_structures.find_one(scoped_query({"_id": oid(item_id)}))
    return render_template("fee_structure/index.html", rows=rows, edit_row=edit_row, grades=GRADES, fee_heads=FEE_HEADS, active_year=selected_year)


@fee_bp.route("/save", methods=["POST"])
@login_required
def save():
    payload = build_fee_structure(request.form)
    payload["updated_at"] = datetime.utcnow()
    item_id = request.form.get("id")
    if item_id:
        mongo.db.fee_structures.update_one(scoped_update_query({"_id": oid(item_id)}), {"$set": scoped_set(payload)})
        payload["_id"] = oid(item_id)
        updated_students = sync_students_with_fee_structure(payload)
        flash(f"Fee structure updated. {updated_students} matching student receivable records recalculated.", "success")
    else:
        payload["created_at"] = datetime.utcnow()
        result = mongo.db.fee_structures.insert_one(scoped_insert(payload))
        payload["_id"] = result.inserted_id
        updated_students = sync_students_with_fee_structure(payload)
        flash(f"Fee structure saved. {updated_students} matching student receivable records recalculated.", "success")
    return redirect(url_for("fees.index"))


@fee_bp.route("/<item_id>/delete", methods=["POST"])
@login_required
def delete(item_id):
    mongo.db.fee_structures.delete_one(scoped_update_query({"_id": oid(item_id)}))
    flash("Fee structure deleted", "success")
    return redirect(url_for("fees.index"))


@fee_bp.route("/api/list")
@login_required
def api_list():
    query = {}
    academic_year = request.args.get("academic_year", "").strip()
    grade = request.args.get("grade", "").strip()
    if academic_year:
        query["academic_year"] = academic_year
    if grade:
        query["$or"] = [{"grade": grade}, {"class_name": grade}]
    projection = {"academic_year": 1, "grade": 1, "class_name": 1, "fee_structure_name": 1, "fee_structure_type": 1, "total_amount": 1, "total_fee": 1, "fee_heads": 1}
    rows = mongo.db.fee_structures.find(scoped_query(query), projection).sort([("academic_year", -1), ("grade", 1), ("fee_structure_name", 1)]).limit(500)
    return jsonify([
        {
            "id": str(row["_id"]),
            "academic_year": row.get("academic_year", ""),
            "grade": row.get("grade") or row.get("class_name", ""),
            "name": row.get("fee_structure_name") or f"{row.get('grade', '')} Fee Structure",
            "type": row.get("fee_structure_type", "standard"),
            "total_amount": row.get("total_amount", row.get("total_fee", 0)),
            "fee_heads": row.get("fee_heads", []),
        }
        for row in rows
    ])
