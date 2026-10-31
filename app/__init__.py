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
    from app.messages import bp as messages_bp
    from app.alerts import bp as alerts_bp
    from app.visits import bp as visits_bp
    from app.reviews import bp as reviews_bp
    from app.insights import bp as insights_bp
    from app.roommates import bp as roommates_bp
    from app.seo import bp as seo_bp
    from app.pwa import bp as pwa_bp
    from app.promote import bp as promote_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(admin_bp)
    for extra in (messages_bp, alerts_bp, visits_bp, reviews_bp, insights_bp, roommates_bp, seo_bp, pwa_bp,
                  promote_bp):
        app.register_blueprint(extra)

    from app import cli
    cli.register(app)

    from app.utils import format_price
    app.jinja_env.filters["tnd"] = format_price
    from app.seo import breadcrumb_ld, faq_ld, listing_ld, uni_slug, area_slug
    app.jinja_env.globals.update(breadcrumb_ld=breadcrumb_ld, faq_ld=faq_ld, listing_ld=listing_ld,
                                 uni_slug=uni_slug, area_slug=area_slug)
    from app import i18n
    i18n.init_app(app)

    from datetime import date

    @app.context_processor
    def inject_globals():
        from flask_login import current_user
        from app.models import unread_messages_count
        from app.notifications import time_ago, unread_count
        from app.reviews import can_review
        from app.security import can_see_phone, needs_email_verification
        from app.utils import format_slot
        auth = current_user.is_authenticated
        unread = unread_count(current_user) if auth else 0
        return {"now_year": date.today().year, "unread_notifs": unread,
                "unread_msgs": unread_messages_count(current_user) if auth else 0,
                "time_ago": time_ago, "format_slot": format_slot, "can_review": can_review,
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
        from app.migrate import backfill_conversations
        backfill_conversations(db)
        # Ferme les connexions ouvertes au démarrage : chaque worker gunicorn
        # doit ouvrir les siennes (sinon erreur SSL "bad record mac")
        db.engine.dispose()

    return app
