import requests
from flask import current_app


def send_email(to_email, subject, body):
    worker_url = current_app.config.get("MAIL_WORKER_URL", "")
    api_key = current_app.config.get("MAIL_API_KEY", "")
    if not worker_url or not api_key:
        return {"success": False, "error": "Mail worker is not configured."}

    try:
        response = requests.post(
            f"{worker_url}/send-email",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"to": to_email, "subject": subject, "body": body},
            timeout=15,
        )
        if 200 <= response.status_code < 300:
            return {"success": True, "error": ""}
        try:
            details = response.json()
            error = details.get("error") or details.get("message") or response.text
        except ValueError:
            error = response.text
        return {"success": False, "error": error or f"Mail worker returned HTTP {response.status_code}."}
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)}
