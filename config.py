import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _database_url():
    url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "ikrili.db"))
    # Render donne "postgres://" ou "postgresql://" : on force le pilote psycopg2
    # (SQLAlchemy 2.1 utilise sinon psycopg 3, qui n'est pas installé)
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-moi-en-production")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Vérifie la connexion avant usage (utile après une mise en veille du serveur)
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "app", "static", "uploads"))
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 Mo par envoi
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
    LISTINGS_PER_PAGE = 12

    # Informations affichées dans les pages légales (modifiables dans Render > Environment)
    LEGAL_OWNER_NAME = os.environ.get("LEGAL_OWNER_NAME", "Bahaeddine El Fidha")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "contact@ikrili.tn")
    SITE_URL = os.environ.get("SITE_URL", "https://ikrili.onrender.com")
    LEGAL_UPDATED = "27 octobre 2026"

    # E-mails (Brevo) – voir app/mail.py
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp-relay.brevo.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_FROM = os.environ.get("MAIL_FROM", "IKRILI <" + os.environ.get("CONTACT_EMAIL", "contact@ikrili.tn") + ">")

    # Sécurité des cookies : actif en production (Render définit la variable RENDER)
    SESSION_COOKIE_SECURE = bool(os.environ.get("RENDER"))
    REMEMBER_COOKIE_SECURE = bool(os.environ.get("RENDER"))
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_HTTPONLY = True

    # Un compte doit avoir au moins ce nombre d'heures pour voir le téléphone des propriétaires
    PHONE_MIN_ACCOUNT_HOURS = 24

    # ------------------------------------------------------------------ revenus (paiement manuel)
    # Formules de mise en avant « jours:prix_TND », séparées par des virgules
    BOOST_PLANS = os.environ.get("BOOST_PLANS", "7:15,15:25,30:45")
    # Coordonnées de paiement affichées au propriétaire (laisser vide pour masquer un moyen)
    PAY_D17 = os.environ.get("PAY_D17", "")            # numéro de téléphone lié au compte D17
    PAY_RIB = os.environ.get("PAY_RIB", "")            # RIB pour un virement bancaire ou postal
    PAY_HOLDER = os.environ.get("PAY_HOLDER", "")      # nom du titulaire
    # Prix de l'offre agence par mois (0 = gratuite pendant le lancement)
    AGENCY_MONTHLY_TND = int(os.environ.get("AGENCY_MONTHLY_TND", "0") or 0)
