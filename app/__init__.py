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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(listings_bp)

    from app import cli
    cli.register(app)

    from app.utils import format_price
    app.jinja_env.filters["tnd"] = format_price

    with app.app_context():
        db.create_all()
        # Ferme les connexions ouvertes au démarrage : chaque worker gunicorn
        # doit ouvrir les siennes (sinon erreur SSL "bad record mac")
        db.engine.dispose()

    return app
