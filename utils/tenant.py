from flask import current_app, has_request_context, session


TENANT_COLLECTIONS = (
    "academic_years",
    "discounts",
    "email_logs",
    "email_templates",
    "fee_structures",
    "payments",
    "receipts",
    "students",
    "users",
)


def default_school_id():
    return current_app.config.get("DEFAULT_SCHOOL_ID", "SCH001") if has_request_context() else "SCH001"


def current_school_id():
    if has_request_context():
        return session.get("school_id") or default_school_id()
    return default_school_id()


def write_school_id():
    school_id = current_school_id()
    return default_school_id() if school_id == "GLOBAL" else school_id


def is_global_admin():
    if not has_request_context():
        return False
    return session.get("role") == "global_admin" or (session.get("username") == "admin" and session.get("school_id") == "GLOBAL")


def scoped_query(query=None, school_id=None, allow_global=True):
    query = dict(query or {})
    if school_id:
        query["school_id"] = school_id
        return query
    if allow_global and is_global_admin():
        return query
    query["school_id"] = current_school_id()
    return query


def scoped_insert(payload, school_id=None):
    payload = dict(payload or {})
    payload.setdefault("school_id", school_id or write_school_id())
    return payload


def scoped_set(payload):
    payload = dict(payload or {})
    payload.pop("school_id", None)
    return payload


def scoped_update_query(query=None, school_id=None):
    return scoped_query(query, school_id=school_id)


def ensure_legacy_school_ids(mongo):
    school_id = default_school_id()
    for collection_name in TENANT_COLLECTIONS:
        mongo.db[collection_name].update_many(
            {"school_id": {"$exists": False}},
            {"$set": {"school_id": school_id}},
        )
