import os
import threading
import time
from functools import wraps

from pymongo import ASCENDING, DESCENDING

from extensions import mongo


_index_lock = threading.Lock()
_indexes_ready = False
_cache = {}


def ttl_cache(seconds=30):
    """Tiny in-memory cache reused by warm Vercel function instances."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from utils.tenant import current_school_id, is_global_admin
            tenant_key = "GLOBAL" if is_global_admin() else current_school_id()
            key = (tenant_key, fn.__name__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            cached = _cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            value = fn(*args, **kwargs)
            _cache[key] = (now + seconds, value)
            return value
        return wrapper
    return decorator


def ensure_performance_indexes():
    """Create missing Atlas indexes lazily so cold starts do not block startup."""
    global _indexes_ready
    if _indexes_ready or os.getenv("SKIP_INDEX_BOOTSTRAP") == "1":
        return
    with _index_lock:
        if _indexes_ready:
            return
        _create_missing_indexes()
        _indexes_ready = True


def _create_missing_indexes():
    _ensure(mongo.db.students, "students_year_grade_name", [("academic_year", ASCENDING), ("grade", ASCENDING), ("student_name", ASCENDING)])
    _ensure(mongo.db.students, "students_school_year_grade_name", [("school_id", ASCENDING), ("academic_year", ASCENDING), ("grade", ASCENDING), ("student_name", ASCENDING)])
    _ensure(mongo.db.students, "students_year_status_grade", [("academic_year", ASCENDING), ("status", ASCENDING), ("grade", ASCENDING)])
    _ensure(mongo.db.students, "students_school_year_status_grade", [("school_id", ASCENDING), ("academic_year", ASCENDING), ("status", ASCENDING), ("grade", ASCENDING)])
    _ensure(mongo.db.students, "students_year_type_grade", [("academic_year", ASCENDING), ("student_type", ASCENDING), ("grade", ASCENDING)])
    _ensure(mongo.db.students, "students_admission_year_grade", [("admission_no", ASCENDING), ("academic_year", ASCENDING), ("grade", ASCENDING)])
    _ensure(mongo.db.students, "students_school_admission_year_grade", [("school_id", ASCENDING), ("admission_no", ASCENDING), ("academic_year", ASCENDING), ("grade", ASCENDING)])
    _ensure(mongo.db.students, "students_assigned_fee_structure", [("assigned_fee_structure_id", ASCENDING), ("assigned_fee_structure_year", ASCENDING)])
    _ensure(mongo.db.students, "students_school_id", [("school_id", ASCENDING)])

    _ensure(mongo.db.receipts, "receipts_year_created", [("academic_year", ASCENDING), ("created_at", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_school_year_created", [("school_id", ASCENDING), ("academic_year", ASCENDING), ("created_at", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_year_grade_date", [("academic_year", ASCENDING), ("grade", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_year_student_date", [("academic_year", ASCENDING), ("student_id", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_school_student_date", [("school_id", ASCENDING), ("student_id", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_year_mode_date", [("academic_year", ASCENDING), ("payment_mode", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.receipts, "receipts_fee_structure", [("fee_structure_id", ASCENDING), ("academic_year", ASCENDING)])
    _ensure(mongo.db.receipts, "receipts_no", [("receipt_no", ASCENDING)])
    _ensure(mongo.db.receipts, "receipts_school_no", [("school_id", ASCENDING), ("receipt_no", ASCENDING)])

    _ensure(mongo.db.payments, "payments_year_student_date", [("academic_year", ASCENDING), ("student_id", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.payments, "payments_school_student_date", [("school_id", ASCENDING), ("student_id", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.payments, "payments_year_grade_date", [("academic_year", ASCENDING), ("grade", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.payments, "payments_year_mode_date", [("academic_year", ASCENDING), ("payment_mode", ASCENDING), ("receipt_date", DESCENDING)])
    _ensure(mongo.db.payments, "payments_no", [("receipt_no", ASCENDING)])
    _ensure(mongo.db.payments, "payments_school_no", [("school_id", ASCENDING), ("receipt_no", ASCENDING)])

    _ensure(mongo.db.fee_structures, "fees_year_grade_type", [("academic_year", ASCENDING), ("grade", ASCENDING), ("student_type", ASCENDING)])
    _ensure(mongo.db.fee_structures, "fees_school_year_grade_type", [("school_id", ASCENDING), ("academic_year", ASCENDING), ("grade", ASCENDING), ("student_type", ASCENDING)])
    _ensure(mongo.db.fee_structures, "fees_year_created", [("academic_year", ASCENDING), ("created_at", DESCENDING)])
    _ensure(mongo.db.users, "users_username", [("username", ASCENDING)])
    _ensure(mongo.db.users, "users_school_username", [("school_id", ASCENDING), ("username", ASCENDING)])
    _ensure(mongo.db.discounts, "discounts_school_student", [("school_id", ASCENDING), ("student_id", ASCENDING)])
    _ensure(mongo.db.schools, "schools_school_id", [("school_id", ASCENDING)])
    _ensure(mongo.db.schools, "schools_status", [("approval_status", ASCENDING), ("account_status", ASCENDING)])
    _ensure(mongo.db.admin_users, "admin_users_username", [("username", ASCENDING)])
    _ensure(mongo.db.audit_logs, "audit_logs_created", [("created_at", DESCENDING)])
    _ensure(mongo.db.audit_logs, "audit_logs_school_created", [("school_id", ASCENDING), ("created_at", DESCENDING)])
    _ensure(mongo.db.subscriptions, "subscriptions_school_created", [("school_id", ASCENDING), ("created_at", DESCENDING)])
    _ensure(mongo.db.global_settings, "global_settings_key", [("key", ASCENDING)])


def _ensure(collection, name, keys):
    if name not in collection.index_information():
        collection.create_index(keys, name=name, background=True)
