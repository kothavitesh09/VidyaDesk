from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from utils.helpers import fmt_money


def simple_pdf(title, rows, headers):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    table = Table([headers] + rows, repeatRows=1)
    table.setStyle(_table_style())
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer


def receipt_pdf(payment, student):
    rows = [[
        payment.get("receipt_date"),
        student.get("roll_no", ""),
        student.get("grade", ""),
        student.get("student_name", ""),
        fmt_money(payment.get("current_payment") or payment.get("amount_paid")),
        payment.get("payment_mode", ""),
        payment.get("transaction_ref") or payment.get("cheque_no", ""),
    ]]
    return simple_pdf("RECEIPT", rows, ["Date", "Roll No", "Grade", "Name of the Student", "Amt Rs.", "Payment Mode", "Transaction Ref"])


def _table_style():
    return TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])
