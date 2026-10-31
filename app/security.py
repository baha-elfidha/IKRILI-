"""Jetons signés (e-mail, mot de passe), limites anti-abus et règles de confiance."""
import random
from datetime import datetime, timedelta
from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import db
from app.mail import mail_enabled
from app.models import RateEvent

# ---------------------------------------------------------------- jetons
RESET_MAX_AGE = 3600            # lien « mot de passe oublié » valable 1 heure
VERIFY_MAX_AGE = 3 * 24 * 3600  # lien de vérification valable 3 jours


def _serializer(salt):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_token(user, purpose):
    # Le début du hash du mot de passe rend le lien de réinitialisation inutilisable
    # dès que le mot de passe a été changé (lien à usage unique).
    return _serializer(purpose).dumps({"id": user.id, "email": user.email, "pw": user.password_hash[-12:]})


def read_token(token, purpose, max_age):
    """Renvoie (données, None) ou (None, 'expire' | 'invalide')."""
    try:
        return _serializer(purpose).loads(token, max_age=max_age), None
    except SignatureExpired:
        return None, "expire"
    except BadSignature:
        return None, "invalide"


# ---------------------------------------------------------------- limites
def client_ip():
    return request.remote_addr or "inconnue"


def too_many(key, limit, seconds):
    since = datetime.utcnow() - timedelta(seconds=seconds)
    return RateEvent.query.filter(RateEvent.key == key, RateEvent.created_at >= since).count() >= limit


def record(key):
    db.session.add(RateEvent(key=key[:120]))
    if random.random() < 0.02:  # nettoyage de temps en temps
        RateEvent.query.filter(RateEvent.created_at < datetime.utcnow() - timedelta(days=2)).delete()
    db.session.commit()


def clear(key):
    RateEvent.query.filter_by(key=key).delete()
    db.session.commit()


# Limites : (nombre maximum, fenêtre en secondes)
LIMITS = {
    "login_ip": (20, 15 * 60),          # échecs de connexion par adresse IP
    "login_email": (5, 15 * 60),        # échecs de connexion par compte
    "register_ip": (5, 3600),           # inscriptions par adresse IP
    "reset_email": (3, 3600),           # demandes « mot de passe oublié » par e-mail
    "reset_ip": (10, 3600),
    "verify_user": (3, 3600),           # renvois du lien de vérification
    "contact_user": (10, 3600),         # messages / demandes de visite par heure
    "contact_user_new": (3, 3600),      # ... pour un compte de moins de 24 h
    "contact_user_day": (30, 86400),
    "chat_user": (60, 3600),            # réponses dans une conversation existante
    "chat_user_new": (20, 3600),
    "alert_user": (10, 3600),           # créations d'alertes de recherche
    "report_ip": (5, 3600),             # signalements par adresse IP
    "publish_user": (5, 86400),         # nouvelles annonces par jour
    "publish_agency": (40, 86400),      # ... pour une agence validée
    "boost_user": (6, 86400),           # demandes de mise en avant par jour
    "agency_user": (3, 86400),          # demandes de compte agence par jour
}


def limited(name, ident):
    limit, seconds = LIMITS[name]
    return too_many(f"{name}:{ident}", limit, seconds)


def hit(name, ident):
    record(f"{name}:{ident}")


# ---------------------------------------------------------------- confiance
def verification_required():
    """La vérification d'e-mail n'est exigée que si l'envoi d'e-mails est configuré."""
    return mail_enabled()


def needs_email_verification(user):
    return verification_required() and not user.email_verified


def can_see_phone(viewer):
    if not viewer.is_authenticated or needs_email_verification(viewer):
        return False
    return viewer.is_admin or viewer.account_age_hours >= current_app.config["PHONE_MIN_ACCOUNT_HOURS"]


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return current_app.login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper
