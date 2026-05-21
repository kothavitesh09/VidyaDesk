from flask import Blueprint, render_template, request, session, redirect, send_file, url_for

from services.report_service import admissions_summary, add_admission_totals, financial_by_type, dashboard_stats, payment_mode_dashboard_summary
from utils.auth import login_required
from utils.helpers import GRADES, active_year

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    year = request.args.get("academic_year") or active_year()
    payment_filters = _payment_filters(year)
    admissions = admissions_summary(year)
    admission_blocks = {name: add_admission_totals(rows) for name, rows in admissions.items()}
    return render_template(
        "dashboard/index.html",
        stats=dashboard_stats(year),
        admission_blocks=admission_blocks,
        financial=financial_by_type(year),
        payment_modes=payment_mode_dashboard_summary(payment_filters),
        payment_filters=payment_filters,
        grades=GRADES,
        active_year=year,
    )


@dashboard_bp.route("/set-year", methods=["POST"])
@login_required
def set_year():
    session["academic_year"] = request.form.get("academic_year")
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/dashboard/payment-mode-export/<fmt>")
@login_required
def payment_mode_export(fmt):
    year = request.args.get("academic_year") or active_year()
    summary = payment_mode_dashboard_summary(_payment_filters(year))
    headers = ["Payment Mode", "Receipt Count", "Collections", "Percentage"]
    rows = [
        [row["payment_mode"], row["receipt_count"], row["collections"], round(row["percentage"], 2)]
        for row in summary["modes"]
    ]
    rows.append(["Overall Total", "", summary["overall_total"], 100 if summary["overall_total"] else 0])
    if fmt == "pdf":
        from utils.pdf_generator import simple_pdf
        return send_file(simple_pdf("Payment Mode Summary", rows, headers), mimetype="application/pdf", as_attachment=True, download_name="payment-mode-summary.pdf")
    from io import BytesIO
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Payment Mode Summary"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return send_file(stream, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="payment-mode-summary.xlsx")


def _payment_filters(year):
    return {
        "academic_year": year,
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "grade": request.args.get("grade", "").strip(),
        "month": request.args.get("month", "").strip(),
    }
