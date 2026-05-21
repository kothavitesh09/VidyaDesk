from pymongo import UpdateOne

from extensions import mongo
from utils.helpers import FEE_HEADS, money, oid
from utils.tenant import scoped_insert, scoped_query, scoped_set, scoped_update_query, write_school_id


LEGACY_HEAD_BY_LABEL = {label.strip().lower(): key for key, label in FEE_HEADS}


def fee_total(data):
    if isinstance(data, list):
        return sum(money(head.get("amount")) for head in data)
    return sum(money(data.get(key)) for key, _ in FEE_HEADS)


def normalize_fee_heads(raw_heads):
    heads = []
    seen = set()
    for raw in raw_heads or []:
        name = str((raw or {}).get("name", "")).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        heads.append({"name": name, "amount": money((raw or {}).get("amount"))})
    return heads


def fee_heads_from_form(form):
    names = form.getlist("fee_head_name")
    amounts = form.getlist("fee_head_amount")
    dynamic = normalize_fee_heads({"name": name, "amount": amount} for name, amount in zip(names, amounts))
    if dynamic:
        return dynamic
    return normalize_fee_heads({"name": label, "amount": form.get(key)} for key, label in FEE_HEADS if money(form.get(key)))


def fee_heads_from_legacy(source):
    return normalize_fee_heads({"name": label, "amount": (source or {}).get(key)} for key, label in FEE_HEADS if money((source or {}).get(key)))


def legacy_fields_from_heads(heads):
    fields = {key: 0 for key, _ in FEE_HEADS}
    for head in normalize_fee_heads(heads):
        legacy_key = LEGACY_HEAD_BY_LABEL.get(head["name"].lower())
        if legacy_key:
            fields[legacy_key] = money(head.get("amount"))
    return fields


def structure_fee_heads(structure):
    if not structure:
        return []
    return normalize_fee_heads(structure.get("fee_heads")) or fee_heads_from_legacy(structure)


def student_manual_fee_heads(student):
    manual = (student or {}).get("manual_fee_structure") or {}
    return normalize_fee_heads(manual.get("fee_heads"))


def find_fee_structure(academic_year, grade, student_type=None, structure_id=None, school_id=None):
    if structure_id and oid(structure_id):
        return mongo.db.fee_structures.find_one(scoped_query({"_id": oid(structure_id)}, school_id))
    query = {
        "academic_year": academic_year,
        "$or": [{"grade": grade}, {"class_name": grade}],
    }
    if student_type:
        query["student_type"] = student_type
    return mongo.db.fee_structures.find_one(scoped_query(query, school_id), sort=[("updated_at", -1), ("created_at", -1)])


def fee_payload_from_structure(structure):
    heads = structure_fee_heads(structure)
    fee_fields = legacy_fields_from_heads(heads)
    total = fee_total(heads)
    payload = {
        **fee_fields,
        "fee_heads": heads,
        "total_amount": total,
        "total_fee": total,
    }
    return payload


def build_fee_structure(form):
    heads = fee_heads_from_form(form)
    fee_fields = legacy_fields_from_heads(heads)
    total = fee_total(heads)
    grade = form.get("grade") or form.get("class_name")
    return scoped_insert({
        "academic_year": form.get("academic_year"),
        "grade": grade,
        "class_name": grade,
        "student_type": form.get("student_type", ""),
        "fee_structure_name": form.get("fee_structure_name", "").strip() or f"{grade} Fee Structure",
        "fee_structure_type": form.get("fee_structure_type") or "standard",
        "description": form.get("description", "").strip(),
        "fee_heads": heads,
        "total_amount": total,
        "total_fee": total,
        **fee_fields,
    })


def build_student_fee(form):
    mode = form.get("fee_structure_mode") or "existing"
    discount = money(form.get("discount_amount"))
    fee = {
        "fee_structure_mode": mode,
        "discount_amount": discount,
        "discount_reason": form.get("discount_reason", ""),
    }
    if mode == "manual":
        heads = fee_heads_from_form(form)
        total = fee_total(heads)
        fee.update({
            **legacy_fields_from_heads(heads),
            "assigned_fee_structure_id": "",
            "assigned_fee_structure_year": "",
            "manual_fee_structure": {"fee_heads": heads, "total_amount": total},
            "fee_heads": heads,
            "total_fee": total,
            "total_amount": total,
            "net_receivable": max(total - discount, 0),
        })
        return fee

    structure = find_fee_structure(
        form.get("assigned_fee_structure_year") or form.get("academic_year"),
        form.get("grade"),
        form.get("student_type"),
        form.get("assigned_fee_structure_id"),
        write_school_id(),
    )
    payload = fee_payload_from_structure(structure)
    fee.update(payload)
    fee.update({
        "assigned_fee_structure_id": str(structure["_id"]) if structure else "",
        "assigned_fee_structure_year": (structure or {}).get("academic_year", form.get("assigned_fee_structure_year") or form.get("academic_year")),
        "manual_fee_structure": {},
        "net_receivable": max(payload["total_fee"] - discount, 0),
    })
    return fee


def effective_fee_for_student(student):
    if not student:
        fee_fields = {key: 0 for key, _ in FEE_HEADS}
        return {"structure": None, "fee_fields": fee_fields, "fee_heads": [], "total_fee": 0, "discount": 0, "net_receivable": 0}

    if student.get("fee_structure_mode") == "manual" or student_manual_fee_heads(student):
        heads = student_manual_fee_heads(student) or normalize_fee_heads(student.get("fee_heads")) or fee_heads_from_legacy(student)
        structure = None
    else:
        structure = find_fee_structure(
            student.get("assigned_fee_structure_year") or student.get("academic_year"),
            student.get("grade"),
            student.get("student_type"),
            student.get("assigned_fee_structure_id"),
            student.get("school_id"),
        )
        heads = structure_fee_heads(structure) or normalize_fee_heads(student.get("fee_heads")) or fee_heads_from_legacy(student)

    fee_fields = legacy_fields_from_heads(heads)
    total_fee = fee_total(heads) if heads else fee_total(student)
    discount = money(student.get("discount_amount"))
    return {
        "structure": structure,
        "fee_fields": fee_fields,
        "fee_heads": heads,
        "total_fee": total_fee,
        "discount": discount,
        "net_receivable": max(total_fee - discount, 0),
    }


def sync_students_with_fee_structure(structure):
    structure_id = str(structure.get("_id", "")) if structure.get("_id") else ""
    if not structure_id:
        existing = find_fee_structure(structure.get("academic_year"), structure.get("grade"), structure.get("student_type"), school_id=structure.get("school_id"))
        structure_id = str(existing["_id"]) if existing else ""
    fee_payload = fee_payload_from_structure(structure)
    total_fee = fee_payload["total_fee"]
    query = {
        "fee_structure_mode": {"$ne": "manual"},
        "$or": [
            {"assigned_fee_structure_id": structure_id},
            {
                "assigned_fee_structure_id": {"$in": [None, ""]},
                "academic_year": structure.get("academic_year"),
                "grade": structure.get("grade"),
                "student_type": structure.get("student_type"),
            },
        ],
    }
    if not structure_id:
        query = {
            "fee_structure_mode": {"$ne": "manual"},
            "assigned_fee_structure_id": {"$in": [None, ""]},
            "academic_year": structure.get("academic_year"),
            "grade": structure.get("grade"),
            "student_type": structure.get("student_type"),
        }
    operations = []
    structure_school_id = structure.get("school_id")
    for student in mongo.db.students.find(scoped_query(query, structure_school_id), {"_id": 1, "discount_amount": 1, "total_paid": 1, "school_id": 1}):
        discount = money(student.get("discount_amount"))
        total_paid = money(student.get("total_paid"))
        net_receivable = max(total_fee - discount, 0)
        operations.append(UpdateOne(
            scoped_update_query({"_id": student["_id"]}, student.get("school_id") or structure_school_id),
            {"$set": {
                **scoped_set(fee_payload),
                "assigned_fee_structure_id": structure_id,
                "assigned_fee_structure_year": structure.get("academic_year"),
                "net_receivable": net_receivable,
                "balance_due": max(net_receivable - total_paid, 0),
            }},
        ))
    if not operations:
        return 0
    result = mongo.db.students.bulk_write(operations, ordered=False)
    return result.modified_count
