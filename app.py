from flask import Flask

from config import Config
from extensions import mongo


def _init_optional_compression(app):
    try:
        from flask_compress import Compress
    except ImportError:
        return
    Compress(app)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    mongo.init_app(app)
    _init_optional_compression(app)

    from routes.auth_routes import auth_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.student_routes import student_bp
    from routes.fee_routes import fee_bp
    from routes.receipt_routes import receipt_bp
    from routes.report_routes import report_bp
    from routes.super_admin_routes import super_admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(fee_bp)
    app.register_blueprint(receipt_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(super_admin_bp)

    @app.context_processor
    def inject_common():
        from utils.helpers import academic_years, active_year, fmt_money, student_status
        return {
            "academic_years": academic_years,
            "current_academic_year": active_year,
            "fmt_money": fmt_money,
            "student_status": student_status,
        }

    @app.before_request
    def lazy_bootstrap():
        from flask import request
        if request.path.startswith("/static/"):
            return
        if app.config.get("_BOOTSTRAP_READY"):
            return
        from utils.performance import ensure_performance_indexes
        from utils.helpers import ensure_defaults
        from utils.tenant import ensure_legacy_school_ids
        ensure_defaults()
        ensure_legacy_school_ids(mongo)
        ensure_performance_indexes()
        app.config["_BOOTSTRAP_READY"] = True

    @app.after_request
    def add_cache_headers(response):
        if response.direct_passthrough:
            return response
        if response.status_code == 200:
            from flask import request
            if request.path.startswith("/static/"):
                response.cache_control.public = True
                response.cache_control.max_age = 31536000
                response.cache_control.immutable = True
            else:
                response.cache_control.no_store = True
        return response

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, host="127.0.0.1", port=5000)
