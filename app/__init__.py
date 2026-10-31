import os

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    # Render place l'application derrière un proxy : on récupère la vraie adresse IP du visiteur
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Connecte-toi pour continuer."
    login_manager.login_message_category = "info"

    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.listings import bp as listings_bp
    from app.notifications import bp as notifications_bp
    from app.admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(admin_bp)

    from app import cli
    cli.register(app)

    from app.utils import format_price
    app.jinja_env.filters["tnd"] = format_price

    from datetime import date

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        from app.notifications import unread_count
        from app.security import can_see_phone, needs_email_verification
        unread = unread_count(current_user) if current_user.is_authenticated else 0
        return {"now_year": date.today().year, "unread_notifs": unread,
                "needs_verification": current_user.is_authenticated and needs_email_verification(current_user),
                "can_see_phone": can_see_phone}

    @app.before_request
    def kick_blocked_users():
        from flask import flash
        from flask_login import current_user, logout_user
        # Note : pour un compte bloqué, is_authenticated vaut déjà False (is_active = False),
        # on teste donc directement l'attribut pour nettoyer la session et prévenir l'utilisateur.
        if getattr(current_user, "is_blocked", False):
            logout_user()
            flash("Ton compte a été suspendu. Contacte-nous si tu penses qu'il s'agit d'une erreur.", "error")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    with app.app_context():
        db.create_all()
        from app.migrate import ensure_schema
        ensure_schema(db)
        # Ferme les connexions ouvertes au démarrage : chaque worker gunicorn
        # doit ouvrir les siennes (sinon erreur SSL "bad record mac")
        db.engine.dispose()

    return app
