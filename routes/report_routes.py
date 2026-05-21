from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from extensions import mongo
from services import report_service
from services.fee_service import effective_fee_for_student
from utils.auth import login_required
from utils.helpers import GRADES, FEE_HEADS, active_year, money, oid
from utils.tenant import scoped_query

report_bp = Blueprint("reports", __name__, url_prefix="/reports")

STUDENT_REPORT_PROJECTION = {
    "admission_no": 1, "student_name": 1, "grade": 1, "student_type": 1,
    "mobile": 1, "discount_amount": 1, "discount_reason": 1,
    "fee_structure_mode": 1, "assigned_fee_structure_id": 1,
    "assigned_fee_structure_year": 1, "manual_fee_structure": 1,
    "fee_heads": 1, "total_fee": 1, "total_amount": 1,
    "net_receivable": 1, "total_paid": 1, "balance_due": 1,
    "school_id": 1,
    **{key: 1 for key, _ in FEE_HEADS},
}

PAYMENT_REPORT_PROJECTION = {
    "receipt_no": 1, "receipt_date": 1, "amount_paid": 1,
    "payment_mode": 1, "remarks": 1,
}


@report_bp.route("/")
@login_required
def index():
    return render_template("reports/index.html")


@report_bp.route("/summary")
@login_required
def summary():
    year = _year()
    admissions = {name: report_service.add_admission_totals(rows) for name, rows in report_service.admissions_summary(year).items()}
    return render_template("reports/summary.html", year=year, admissions=admissions, financial=report_service.financial_by_type(year))


@report_bp.route("/admissions")
@login_required
def admissions():
    year = _year()
    blocks = {name: report_service.add_admission_totals(rows) for name, rows in report_service.admissions_summary(year).items()}
    return render_template("reports/admissions.html", year=year, blocks=blocks)


@report_bp.route("/receivable")
@login_required
def receivable():
    year = _year()
    rows = report_service.financial_by_grade_type(year)
    return render_template("reports/receivable.html", year=year, rows=rows)


@report_bp.route("/collections")
@login_required
def collections():
    year = _year()
    rows = report_service.collection_by_type_grade(year)
    return render_template("reports/collections.html", year=year, rows=rows)


@report_bp.route("/discounts")
@login_required
def discounts():
    year = _year()
    rows = report_service.discounts_by_grade(year)
    return render_template("reports/discounts.html", year=year, rows=rows)


@report_bp.route("/fee-structure")
@login_required
def fee_structure():
    year = _year()
    rows = report_service.fee_structures(year)
    return render_template("reports/fee_structure.html", year=year, rows=rows, grades=GRADES, fee_heads=FEE_HEADS)


@report_bp.route("/student-wise", methods=["GET", "POST"])
@login_required
def student_wise():
    year = _year()
    student = None
    payments = []
    effective_fee = None
    total_paid = 0
    balance_due = 0
    if request.values.get("student_id"):
        student = mongo.db.students.find_one(scoped_query({"_id": oid(request.values.get("student_id"))}), STUDENT_REPORT_PROJECTION)
        if not student:
            flash("Student not found", "danger")
            return redirect(url_for("reports.student_wise"))
        payments = list(mongo.db.payments.find(scoped_query({"student_id": str(student["_id"])}, student.get("school_id")), PAYMENT_REPORT_PROJECTION).sort("receipt_date", -1).limit(250))
        effective_fee = effective_fee_for_student(student)
        total_paid = money(student.get("total_paid"))
        if not total_paid:
            total_paid = sum(money(payment.get("amount_paid")) for payment in payments)
        balance_due = max(effective_fee["net_receivable"] - total_paid, 0)
    return render_template("reports/student_wise.html", year=year, grades=GRADES, fee_heads=FEE_HEADS, student=student, payments=payments, effective_fee=effective_fee, total_paid=total_paid, balance_due=balance_due)


@report_bp.route("/date-wise-collections")
@login_required
def date_wise_collections():
    year = _year()
    return render_template("reports/date_wise_collections.html", year=year, rows=report_service.date_wise_collections(year))


@report_bp.route("/class-wise-pending")
@login_required
def class_wise_pending():
    year = _year()
    return render_template("reports/class_wise_pending.html", year=year, rows=report_service.class_wise_pending_dues(year))


@report_bp.route("/receipt-history")
@login_required
def receipt_history():
    year = _year()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = min(max(int(request.args.get("per_page", 100)), 25), 250)
    rows, total = report_service.receipt_history(year, page=page, per_page=per_page)
    return render_template("reports/receipt_history.html", year=year, rows=rows, page=page, per_page=per_page, total=total)


@report_bp.route("/payment-mode-summary")
@login_required
def payment_mode_summary():
    year = _year()
    return render_template("reports/payment_mode_summary.html", year=year, rows=report_service.payment_mode_summary(year))


@report_bp.route("/student-wise/pdf/<student_id>")
@login_required
def student_wise_pdf(student_id):
    student = mongo.db.students.find_one(scoped_query({"_id": oid(student_id)}), STUDENT_REPORT_PROJECTION)
    if not student:
        flash("Student not found", "danger")
        return redirect(url_for("reports.student_wise"))
    payments = list(mongo.db.payments.find(scoped_query({"student_id": str(student["_id"])}, student.get("school_id")), PAYMENT_REPORT_PROJECTION).sort("receipt_date", -1).limit(500))
    effective_fee = effective_fee_for_student(student)
    total_paid = money(student.get("total_paid")) or sum(money(p.get("amount_paid")) for p in payments)
    balance_due = max(effective_fee["net_receivable"] - total_paid, 0)
    rows = [
        ["Admission No", student.get("admission_no", "")],
        ["Student Name", student.get("student_name", "")],
        ["Grade", student.get("grade", "")],
        ["Student Type", student.get("student_type", "")],
        ["Mobile Number", student.get("mobile", "")],
        ["Total Receivable", effective_fee["net_receivable"]],
        ["Total Paid", total_paid],
        ["Balance Due", balance_due],
    ]
    rows.append(["Payment History", ""])
    rows.extend([[p.get("receipt_no"), f"{p.get('receipt_date')} / {p.get('payment_mode')} / {p.get('amount_paid')}"] for p in payments])
    from utils.pdf_generator import simple_pdf
    return send_file(simple_pdf("Student Wise Report", rows, ["Particular", "Details"]), mimetype="application/pdf", as_attachment=True, download_name=f"student-{student.get('admission_no')}.pdf")


@report_bp.route("/export/<report_name>/<fmt>")
@login_required
def export(report_name, fmt):
    year = _year()
    rows, headers, title = _export_data(report_name, year)
    if fmt == "pdf":
        from utils.pdf_generator import simple_pdf
        return send_file(simple_pdf(title, rows, headers), mimetype="application/pdf", as_attachment=True, download_name=f"{report_name}.pdf")
    from io import BytesIO
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = report_name[:31]
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return send_file(stream, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=f"{report_name}.xlsx")


def _year():
    return request.args.get("academic_year") or active_year()


def _export_data(report_name, year):
    if report_name == "summary":
        data = report_service.financial_by_type(year)
        headers = ["Particulars", "Actual Receivable", "Discounts", "Net Receivable", "Collections", "Balance Due", "Collection %"]
        rows = [[name, r["actual_receivable"], r["discounts"], r["net_receivable"], r["collections"], r["balance_due"], round(r["collection_pct"], 2)] for name, r in data.items()]
        return rows, headers, "Summary Report"
    if report_name == "admissions":
        headers = ["Student Type", "Old Boys", "Old Girls", "Old Total", "New Boys", "New Girls", "New Total", "Total Boys", "Total Girls", "Total"]
        rows = []
        for name, block in report_service.admissions_summary(year).items():
            _, total = report_service.add_admission_totals(block)
            rows.append([name, total["old_boys"], total["old_girls"], total["old_total"], total["new_boys"], total["new_girls"], total["new_total"], total["total_boys"], total["total_girls"], total["total"]])
        return rows, headers, "Admissions Report"
    if report_name == "fee_structure":
        data = report_service.fee_structures(year)
        headers = ["Name", "Grade", "Type", "Fee Heads", "Total Fee"]
        rows = [[
            r.get("fee_structure_name") or r.get("grade"),
            r.get("grade") or r.get("class_name"),
            "Manual / Custom" if r.get("fee_structure_type") == "manual" else "Standard",
            ", ".join(f"{head.get('name')}: {head.get('amount')}" for head in (r.get("fee_heads") or [])) or ", ".join(f"{label}: {r.get(key, 0)}" for key, label in FEE_HEADS),
            r.get("total_amount", r.get("total_fee", 0)),
        ] for r in data]
        return rows, headers, "Fee Structure Report"
    if report_name == "collections":
        data = report_service.financial_by_grade_type(year)
        headers = ["Grade", "Student Type", "Receivable", "Collections", "Balance Due", "Collection %"]
        rows = [[r["grade"], r["student_type"], r["net_receivable"], r["collections"], r["balance_due"], round(r["collection_pct"], 2)] for r in data]
        return rows, headers, "Fees Collection Report"
    if report_name == "date_wise_collections":
        data = report_service.date_wise_collections(year)
        headers = ["Date", "Receipt Count", "Collections"]
        rows = [[r["receipt_date"], r["receipt_count"], r["collections"]] for r in data]
        return rows, headers, "Date-wise Collections"
    if report_name == "class_wise_pending":
        data = report_service.class_wise_pending_dues(year)
        headers = ["Grade", "Student Count", "Pending Due"]
        rows = [[r["grade"], r["student_count"], r["pending_due"]] for r in data]
        return rows, headers, "Class-wise Pending Dues"
    if report_name == "receipt_history":
        data, _ = report_service.receipt_history(year, page=1, per_page=5000)
        headers = ["Receipt No", "Date", "Student", "Class", "Amount", "Mode", "Balance Due"]
        rows = [[r.get("receipt_no"), r.get("receipt_date"), r.get("student_name"), r.get("class_name") or r.get("grade"), r.get("current_payment"), r.get("payment_mode"), r.get("balance_due")] for r in data]
        return rows, headers, "Receipt History"
    if report_name == "payment_mode_summary":
        data = report_service.payment_mode_summary(year)
        headers = ["Payment Mode", "Receipt Count", "Collections"]
        rows = [[r["payment_mode"], r["receipt_count"], r["collections"]] for r in data]
        return rows, headers, "Payment Mode Summary"
    if report_name == "discounts":
        data = report_service.discounts_by_grade(year)
        headers = ["Grade", "Student Count", "Discount Amount"]
        rows = [[r["grade"], r["student_count"], r["discount_amount"]] for r in data]
        return rows, headers, "Discounts Report"
    data = report_service.financial_by_grade_type(year)
    headers = ["Grade", "Student Type", "Actual Receivable", "Discounts", "Net Receivable"]
    rows = [[r["grade"], r["student_type"], r["actual_receivable"], r["discounts"], r["net_receivable"]] for r in data]
    return rows, headers, "Fees Receivable Report"
