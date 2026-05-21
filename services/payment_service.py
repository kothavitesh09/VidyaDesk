from datetime import datetime

from extensions import mongo
from services.fee_service import effective_fee_for_student
from utils.helpers import FEE_HEADS, money, next_receipt_no, oid
from utils.tenant import current_school_id, scoped_insert, scoped_query, scoped_update_query


def totals_for_student(student_id):
    paid_row = next(mongo.db.payments.aggregate([
        {"$match": scoped_query({"student_id": str(student_id)})},
        {"$group": {"_id": None, "total_paid": {"$sum": "$amount_paid"}}},
    ]), {})
    total_paid = money(paid_row.get("total_paid"))
    projection = {"school_id": 1, "net_receivable": 1, "total_fee": 1, "total_amount": 1, "discount_amount": 1, "fee_structure_mode": 1, "assigned_fee_structure_id": 1, "assigned_fee_structure_year": 1, "manual_fee_structure": 1, "fee_heads": 1, **{key: 1 for key, _ in FEE_HEADS}}
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}), projection)
    net = effective_fee_for_student(student)["net_receivable"] if student else 0
    return {
        "total_paid": total_paid,
        "balance_due": max(net - total_paid, 0),
    }


def create_payment(student, form):
    return create_receipt(student, form)


def create_receipt(student, form):
    amount = money(form.get("current_payment") or form.get("amount_paid"))
    extra_discount = money(form.get("discount"))
    effective_fee = effective_fee_for_student(student)
    paid_before = totals_for_student(student["_id"])["total_paid"]
    pending_before = max(effective_fee["net_receivable"] - paid_before, 0)
    payable_balance = max(pending_before - extra_discount, 0)
    if amount <= 0:
        raise ValueError("Current payment amount must be greater than zero.")
    if amount > payable_balance:
        raise ValueError("Current payment cannot be greater than pending due.")

    receipt_no = next_receipt_no()
    receipt_date = form.get("receipt_date") or datetime.now().strftime("%Y-%m-%d")
    structure = effective_fee["structure"]
    balance_due = max(payable_balance - amount, 0)
    receipt = {
        "receipt_no": receipt_no,
        "student_id": str(student["_id"]),
        "school_id": student.get("school_id") or current_school_id(),
        "student_name": student.get("student_name"),
        "class_name": student.get("grade"),
        "grade": student.get("grade"),
        "admission_no": student.get("admission_no"),
        "mobile": student.get("mobile"),
        "academic_year": student.get("academic_year"),
        "fee_structure_id": str(structure["_id"]) if structure else "",
        "fee_structure_year": student.get("assigned_fee_structure_year") or student.get("academic_year"),
        "fee_structure_mode": student.get("fee_structure_mode") or "existing",
        "fee_heads": effective_fee.get("fee_heads", []),
        "total_fee": effective_fee["total_fee"],
        "amount_paid_before": paid_before,
        "current_payment": amount,
        "discount": extra_discount,
        "balance_due": balance_due,
        "payment_mode": form.get("payment_mode"),
        "transaction_ref": form.get("transaction_ref") or form.get("cheque_no", ""),
        "cheque_no": form.get("cheque_no") or form.get("transaction_ref", ""),
        "receipt_date": receipt_date,
        "remarks": form.get("remarks", ""),
        "created_at": datetime.utcnow(),
    }
    mongo.db.receipts.insert_one(scoped_insert(receipt.copy(), receipt["school_id"]))
    payment = {
        "receipt_no": receipt_no,
        "student_id": str(student["_id"]),
        "school_id": receipt["school_id"],
        "student_name": student.get("student_name"),
        "admission_no": student.get("admission_no"),
        "grade": student.get("grade"),
        "academic_year": student.get("academic_year"),
        "receipt_date": receipt_date,
        "amount_paid": amount,
        "payment_mode": form.get("payment_mode"),
        "cheque_no": receipt["transaction_ref"],
        "remarks": form.get("remarks", ""),
        "created_at": datetime.utcnow(),
    }
    mongo.db.payments.insert_one(scoped_insert(payment, receipt["school_id"]))
    if extra_discount:
        mongo.db.discounts.insert_one(scoped_insert({
            "student_id": str(student["_id"]),
            "school_id": receipt["school_id"],
            "student_name": student.get("student_name"),
            "grade": student.get("grade"),
            "academic_year": student.get("academic_year"),
            "amount": extra_discount,
            "reason": f"Receipt {receipt_no}",
            "created_at": datetime.utcnow(),
        }, receipt["school_id"]))
        mongo.db.students.update_one(scoped_update_query({"_id": student["_id"]}, receipt["school_id"]), {"$inc": {"discount_amount": extra_discount}})
    update_student_payment_summary(student["_id"], receipt["school_id"])
    receipt["amount_paid"] = amount
    receipt["receipt_id"] = str(receipt.get("_id", ""))
    return receipt


def update_student_payment_summary(student_id, school_id=None):
    totals = totals_for_student(student_id)
    projection = {"school_id": 1, "net_receivable": 1, "total_fee": 1, "total_amount": 1, "discount_amount": 1, "fee_structure_mode": 1, "assigned_fee_structure_id": 1, "assigned_fee_structure_year": 1, "manual_fee_structure": 1, "fee_heads": 1, **{key: 1 for key, _ in FEE_HEADS}}
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}, school_id), projection)
    effective_fee = effective_fee_for_student(student) if student else {"fee_fields": {}, "total_fee": 0, "net_receivable": 0}
    mongo.db.students.update_one(
        scoped_update_query({"_id": oid(student_id)}, school_id or (student or {}).get("school_id")),
        {"$set": {
            **effective_fee["fee_fields"],
            "fee_heads": effective_fee.get("fee_heads", []),
            "total_amount": effective_fee["total_fee"],
            "total_fee": effective_fee["total_fee"],
            "net_receivable": effective_fee["net_receivable"],
            "total_paid": totals["total_paid"],
            "balance_due": totals["balance_due"],
            "updated_at": datetime.utcnow(),
        }},
    )
    return totals


def delete_receipt(receipt_no):
    receipt = mongo.db.receipts.find_one(scoped_query({"receipt_no": receipt_no})) or mongo.db.payments.find_one(scoped_query({"receipt_no": receipt_no}))
    if not receipt:
        raise ValueError("Receipt not found.")
    student_id = receipt.get("student_id")
    discount = money(receipt.get("discount"))
    school_id = receipt.get("school_id") or current_school_id()
    mongo.db.receipts.delete_many(scoped_query({"receipt_no": receipt_no}, school_id))
    mongo.db.payments.delete_many(scoped_query({"receipt_no": receipt_no}, school_id))
    mongo.db.discounts.delete_many(scoped_query({"reason": f"Receipt {receipt_no}"}, school_id))
    if discount and oid(student_id):
        mongo.db.students.update_one(scoped_update_query({"_id": oid(student_id)}, school_id), {"$inc": {"discount_amount": -discount}})
    if oid(student_id):
        update_student_payment_summary(student_id, school_id)
    return receipt
