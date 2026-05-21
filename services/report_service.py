from collections import defaultdict
from datetime import datetime

from extensions import mongo
from utils.performance import ttl_cache
from utils.helpers import GRADES, FEE_HEADS, money
from utils.tenant import scoped_query


def year_filter(academic_year):
    return scoped_query({"academic_year": academic_year})


def students(academic_year):
    projection = {
        "academic_year": 1, "grade": 1, "student_name": 1, "student_type": 1,
        "gender": 1, "student_status": 1, "total_fee": 1, "total_amount": 1,
        "net_receivable": 1, "discount_amount": 1, "balance_due": 1,
    }
    return list(mongo.db.students.find(year_filter(academic_year), projection).sort([("grade", 1), ("student_name", 1)]))


def payments(academic_year):
    projection = {"student_id": 1, "amount_paid": 1, "receipt_date": 1, "payment_mode": 1, "grade": 1, "academic_year": 1}
    return list(mongo.db.payments.find(year_filter(academic_year), projection).sort("receipt_date", -1))


def fee_structure_map(academic_year):
    return {
        (row.get("grade"), row.get("student_type")): row
        for row in mongo.db.fee_structures.find(year_filter(academic_year))
    }


def effective_student_financials(student, structures=None):
    total_fee = money(student.get("total_fee", student.get("total_amount", 0)))
    discount = money(student.get("discount_amount"))
    net_receivable = money(student.get("net_receivable"))
    if not net_receivable and total_fee:
        net_receivable = max(total_fee - discount, 0)
    return total_fee, discount, net_receivable


@ttl_cache(seconds=45)
def admissions_summary(academic_year):
    summary = {
        "Day Scholar": _empty_admission_rows(),
        "Residential": _empty_admission_rows(),
    }
    rows = mongo.db.students.aggregate([
        {"$match": year_filter(academic_year)},
        {"$group": {
            "_id": {
                "student_type": {"$ifNull": ["$student_type", "Day Scholar"]},
                "grade": "$grade",
                "student_status": "$student_status",
                "gender": "$gender",
            },
            "count": {"$sum": 1},
        }},
    ])
    for item in rows:
        item_id = item["_id"]
        grade = item_id.get("grade")
        if grade not in GRADES:
            continue
        target = summary.get(item_id.get("student_type"), summary["Day Scholar"])
        row = target[grade]
        gender = (item_id.get("gender") or "").lower()
        is_new = item_id.get("student_status") == "New"
        key = ("new_" if is_new else "old_") + ("girls" if gender == "female" else "boys")
        row[key] += item.get("count", 0)
    return summary


def _empty_admission_rows():
    return {grade: {"old_boys": 0, "old_girls": 0, "new_boys": 0, "new_girls": 0} for grade in GRADES}


def add_admission_totals(rows):
    totals = {"old_boys": 0, "old_girls": 0, "new_boys": 0, "new_girls": 0}
    for row in rows.values():
        for key in totals:
            totals[key] += row[key]
        row["old_total"] = row["old_boys"] + row["old_girls"]
        row["new_total"] = row["new_boys"] + row["new_girls"]
        row["total_boys"] = row["old_boys"] + row["new_boys"]
        row["total_girls"] = row["old_girls"] + row["new_girls"]
        row["total"] = row["old_total"] + row["new_total"]
    totals["old_total"] = totals["old_boys"] + totals["old_girls"]
    totals["new_total"] = totals["new_boys"] + totals["new_girls"]
    totals["total_boys"] = totals["old_boys"] + totals["new_boys"]
    totals["total_girls"] = totals["old_girls"] + totals["new_girls"]
    totals["total"] = totals["old_total"] + totals["new_total"]
    return rows, totals


@ttl_cache(seconds=30)
def financial_by_type(academic_year):
    data = {
        "Day Scholar": _money_row(),
        "Residential": _money_row(),
    }
    for row in mongo.db.students.aggregate(_student_money_pipeline(academic_year, "$student_type")):
        target = data.get(row["_id"] or "Day Scholar", data["Day Scholar"])
        _copy_money_row(target, row)
    for row in mongo.db.payments.aggregate(_payment_sum_pipeline(academic_year, "$student.student_type")):
        student_type = row["_id"] or "Day Scholar"
        data.get(student_type, data["Day Scholar"])["collections"] = money(row.get("collections"))
    _finalize_money_rows(data)
    return data


@ttl_cache(seconds=30)
def financial_by_grade_type(academic_year):
    data = defaultdict(_money_row)
    for item in mongo.db.students.aggregate(_student_money_pipeline(academic_year, {"grade": "$grade", "student_type": "$student_type"})):
        key = ((item["_id"] or {}).get("grade") or "", (item["_id"] or {}).get("student_type") or "Day Scholar")
        target = data[key]
        target["grade"] = key[0]
        target["student_type"] = key[1]
        _copy_money_row(target, item)
    for paid in mongo.db.payments.aggregate(_payment_sum_pipeline(academic_year, {"grade": "$student.grade", "student_type": "$student.student_type"})):
        key = ((paid["_id"] or {}).get("grade") or "", (paid["_id"] or {}).get("student_type") or "Day Scholar")
        data[key]["grade"] = key[0]
        data[key]["student_type"] = key[1]
        data[key]["collections"] = money(paid.get("collections"))
    _finalize_money_rows(data)
    return sorted(data.values(), key=lambda row: (row["student_type"] or "", GRADES.index(row["grade"]) if row["grade"] in GRADES else 99))


@ttl_cache(seconds=30)
def collection_by_type_grade(academic_year):
    rows = {
        "Day Scholar": _grade_money_rows(),
        "Residential": _grade_money_rows(),
    }
    for student in students(academic_year):
        grade = student.get("grade")
        if grade not in GRADES:
            continue
        row = rows.get(student.get("student_type"), rows["Day Scholar"])[grade]
        _, _, net_receivable = effective_student_financials(student)
        row["receivable"] += net_receivable
    for paid in mongo.db.payments.aggregate(_payment_sum_pipeline(academic_year, {"grade": "$student.grade", "student_type": "$student.student_type"})):
        item_id = paid["_id"] or {}
        grade = item_id.get("grade")
        if grade in GRADES:
            rows.get(item_id.get("student_type"), rows["Day Scholar"])[grade]["collections"] = money(paid.get("collections"))
    for group in rows.values():
        total = {"grade": "Total", "receivable": 0, "collections": 0, "balance_due": 0, "collection_pct": 0}
        for row in group.values():
            row["balance_due"] = max(row["receivable"] - row["collections"], 0)
            row["collection_pct"] = (row["collections"] / row["receivable"] * 100) if row["receivable"] else 0
            total["receivable"] += row["receivable"]
            total["collections"] += row["collections"]
            total["balance_due"] += row["balance_due"]
        total["collection_pct"] = (total["collections"] / total["receivable"] * 100) if total["receivable"] else 0
        group["_total"] = total
    return rows


@ttl_cache(seconds=30)
def discounts_by_grade(academic_year):
    data = defaultdict(lambda: {"grade": "", "student_count": 0, "discount_amount": 0})
    rows = mongo.db.students.aggregate([
        {"$match": {**year_filter(academic_year), "discount_amount": {"$gt": 0}}},
        {"$group": {"_id": "$grade", "student_count": {"$sum": 1}, "discount_amount": {"$sum": "$discount_amount"}}},
    ])
    for item in rows:
        data[item["_id"]] = {"grade": item["_id"], "student_count": item.get("student_count", 0), "discount_amount": money(item.get("discount_amount"))}
    return [data[grade] for grade in GRADES if grade in data]


def fee_structures(academic_year):
    projection = {"fee_structure_name": 1, "fee_structure_type": 1, "academic_year": 1, "grade": 1, "class_name": 1, "student_type": 1, "fee_heads": 1, "total_amount": 1, "total_fee": 1, **{key: 1 for key, _ in FEE_HEADS}}
    return list(mongo.db.fee_structures.find(year_filter(academic_year), projection).sort([("student_type", 1), ("grade", 1)]))


@ttl_cache(seconds=30)
def dashboard_stats(academic_year):
    fin = financial_by_type(academic_year)
    total_students = mongo.db.students.count_documents(year_filter(academic_year))
    new_admissions = mongo.db.students.count_documents({**year_filter(academic_year), "student_status": "New"})
    totals = _money_row()
    for row in fin.values():
        for key in totals:
            totals[key] += money(row.get(key))
    totals["balance_due"] = max(totals["net_receivable"] - totals["collections"], 0)
    totals["collection_pct"] = (totals["collections"] / totals["net_receivable"] * 100) if totals["net_receivable"] else 0
    today = datetime.now().strftime("%Y-%m-%d")
    today_row = next(mongo.db.payments.aggregate([
        {"$match": {**year_filter(academic_year), "receipt_date": today}},
        {"$group": {"_id": None, "total": {"$sum": "$amount_paid"}}},
    ]), {})
    today_collections = money(today_row.get("total"))
    return {"total_students": total_students, "new_admissions": new_admissions, "today_collections": today_collections, **totals}


@ttl_cache(seconds=30)
def date_wise_collections(academic_year):
    rows = mongo.db.payments.aggregate([
        {"$match": year_filter(academic_year)},
        {"$group": {"_id": "$receipt_date", "receipt_count": {"$sum": 1}, "collections": {"$sum": "$amount_paid"}}},
        {"$sort": {"_id": -1}},
    ])
    return [{"receipt_date": row["_id"], "receipt_count": row.get("receipt_count", 0), "collections": money(row.get("collections"))} for row in rows]


@ttl_cache(seconds=30)
def class_wise_pending_dues(academic_year):
    rows = defaultdict(lambda: {"grade": "", "student_count": 0, "pending_due": 0})
    data = mongo.db.students.aggregate([
        {"$match": year_filter(academic_year)},
        {"$group": {
            "_id": "$grade",
            "student_count": {"$sum": 1},
            "pending_due": {"$sum": {"$ifNull": ["$balance_due", {"$ifNull": ["$net_receivable", 0]}]}},
        }},
    ])
    for item in data:
        rows[item["_id"]] = {"grade": item["_id"], "student_count": item.get("student_count", 0), "pending_due": money(item.get("pending_due"))}
    return [rows[grade] for grade in GRADES if grade in rows]


def receipt_history(academic_year, page=1, per_page=100):
    projection = {"receipt_no": 1, "receipt_date": 1, "student_name": 1, "class_name": 1, "grade": 1, "current_payment": 1, "payment_mode": 1, "balance_due": 1}
    query = year_filter(academic_year)
    total = mongo.db.receipts.count_documents(query)
    rows = list(
        mongo.db.receipts.find(query, projection)
        .sort("created_at", -1)
        .skip((max(page, 1) - 1) * per_page)
        .limit(per_page)
    )
    return rows, total


@ttl_cache(seconds=30)
def payment_mode_summary(academic_year):
    rows = mongo.db.payments.aggregate([
        {"$match": year_filter(academic_year)},
        {"$group": {"_id": {"$ifNull": ["$payment_mode", "Unspecified"]}, "receipt_count": {"$sum": 1}, "collections": {"$sum": "$amount_paid"}}},
        {"$sort": {"collections": -1}},
    ])
    return [{"payment_mode": row["_id"], "receipt_count": row.get("receipt_count", 0), "collections": money(row.get("collections"))} for row in rows]


def payment_mode_dashboard_summary(filters):
    modes = ["Cash", "UPI", "Bank Transfer", "Card", "Cheque"]
    mode_lookup = {mode.lower(): mode for mode in modes}
    match = {"academic_year": filters.get("academic_year")}
    if filters.get("grade"):
        match["$or"] = [{"grade": filters["grade"]}, {"class_name": filters["grade"]}]
    if filters.get("date_from") or filters.get("date_to"):
        match["receipt_date"] = {}
        if filters.get("date_from"):
            match["receipt_date"]["$gte"] = filters["date_from"]
        if filters.get("date_to"):
            match["receipt_date"]["$lte"] = filters["date_to"]
    if filters.get("month"):
        match["receipt_date"] = {"$regex": f"^{filters['month']}"}

    amount_expr = {"$ifNull": ["$current_payment", {"$ifNull": ["$amount_paid", 0]}]}
    grouped = list(mongo.db.receipts.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"$ifNull": ["$payment_mode", "Unspecified"]},
            "collections": {"$sum": amount_expr},
            "receipt_count": {"$sum": 1},
        }},
    ]))
    by_mode = {
        mode: {"payment_mode": mode, "collections": 0, "receipt_count": 0, "percentage": 0}
        for mode in modes
    }
    for row in grouped:
        mode = mode_lookup.get(str(row["_id"] or "").strip().lower())
        if not mode:
            continue
        by_mode[mode]["collections"] = money(row.get("collections"))
        by_mode[mode]["receipt_count"] = row.get("receipt_count", 0)
    total = sum(row["collections"] for row in by_mode.values())
    for row in by_mode.values():
        row["percentage"] = (row["collections"] / total * 100) if total else 0

    trend_rows = list(mongo.db.receipts.aggregate([
        {"$match": {"academic_year": filters.get("academic_year")}},
        {"$group": {
            "_id": {
                "month": {"$substr": ["$receipt_date", 0, 7]},
                "mode": {"$ifNull": ["$payment_mode", "Unspecified"]},
            },
            "collections": {"$sum": amount_expr},
        }},
        {"$sort": {"_id.month": 1}},
    ]))
    months = sorted({row["_id"]["month"] for row in trend_rows if row["_id"].get("month")})
    trend = {
        "labels": months,
        "datasets": [
            {
                "label": mode,
                "data": [
                    money(next((row["collections"] for row in trend_rows if row["_id"].get("month") == month and row["_id"].get("mode") == mode), 0))
                    for month in months
                ],
            }
            for mode in modes
        ],
    }
    projection = {"receipt_no": 1, "receipt_date": 1, "student_name": 1, "payment_mode": 1, "current_payment": 1, "amount_paid": 1, "created_at": 1}
    recent = list(mongo.db.receipts.find(match, projection).sort("created_at", -1).limit(8))
    return {
        "modes": list(by_mode.values()),
        "overall_total": total,
        "chart": {
            "labels": [row["payment_mode"] for row in by_mode.values()],
            "data": [row["collections"] for row in by_mode.values()],
        },
        "trend": trend,
        "recent": recent,
    }


def _money_row():
    return {"actual_receivable": 0, "discounts": 0, "net_receivable": 0, "collections": 0, "balance_due": 0, "collection_pct": 0}


def _grade_money_rows():
    return {grade: {"grade": grade, "receivable": 0, "collections": 0, "balance_due": 0, "collection_pct": 0} for grade in GRADES}


def _finalize_money_rows(rows):
    iterable = rows.values() if hasattr(rows, "values") else rows
    for row in iterable:
        row["balance_due"] = max(row["net_receivable"] - row["collections"], 0)
        row["collection_pct"] = (row["collections"] / row["net_receivable"] * 100) if row["net_receivable"] else 0


def _student_money_pipeline(academic_year, group_id):
    return [
        {"$match": year_filter(academic_year)},
        {"$project": {
            "group_id": group_id,
            "total_fee": {"$ifNull": ["$total_fee", {"$ifNull": ["$total_amount", 0]}]},
            "discount_amount": {"$ifNull": ["$discount_amount", 0]},
            "net_receivable": {"$ifNull": ["$net_receivable", {"$subtract": [{"$ifNull": ["$total_fee", 0]}, {"$ifNull": ["$discount_amount", 0]}]}]},
        }},
        {"$group": {
            "_id": "$group_id",
            "actual_receivable": {"$sum": "$total_fee"},
            "discounts": {"$sum": "$discount_amount"},
            "net_receivable": {"$sum": "$net_receivable"},
        }},
    ]


def _payment_sum_pipeline(academic_year, group_id):
    return [
        {"$match": year_filter(academic_year)},
        {"$addFields": {"student_obj_id": {"$convert": {"input": "$student_id", "to": "objectId", "onError": None, "onNull": None}}}},
        {"$lookup": {"from": "students", "localField": "student_obj_id", "foreignField": "_id", "as": "student"}},
        {"$unwind": {"path": "$student", "preserveNullAndEmptyArrays": True}},
        {"$project": {"group_id": group_id, "amount_paid": {"$ifNull": ["$amount_paid", 0]}}},
        {"$group": {"_id": "$group_id", "collections": {"$sum": "$amount_paid"}}},
    ]


def _copy_money_row(target, source):
    target["actual_receivable"] = money(source.get("actual_receivable"))
    target["discounts"] = money(source.get("discounts"))
    target["net_receivable"] = money(source.get("net_receivable"))


def totals_for(rows, keys):
    total = {key: 0 for key in keys}
    for row in rows:
        for key in keys:
            total[key] += money(row.get(key))
    return total
