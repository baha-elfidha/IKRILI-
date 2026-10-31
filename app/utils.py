import math
import os
import random
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

GOVERNORATES = [
    "Ariana", "Béja", "Ben Arous", "Bizerte", "Gabès", "Gafsa", "Jendouba", "Kairouan",
    "Kasserine", "Kébili", "Le Kef", "Mahdia", "La Manouba", "Médenine", "Monastir",
    "Nabeul", "Sfax", "Sidi Bouzid", "Siliana", "Sousse", "Tataouine", "Tozeur", "Tunis",
    "Zaghouan",
]

PROPERTY_TYPES = [
    ("appartement", "Appartement"),
    ("maison", "Maison"),
    ("studio", "Studio"),
    ("chambre", "Chambre en colocation"),
]


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance à vol d'oiseau en km entre deux points GPS."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def approx_coords(listing):
    """Position floutée (décalage fixe de 100 à 200 m) pour ne jamais révéler l'adresse exacte.
    Le décalage dépend de l'identifiant : il reste le même à chaque affichage."""
    if listing.latitude is None or listing.longitude is None:
        return None
    rnd = random.Random(listing.id * 7919)
    angle = rnd.uniform(0, 2 * math.pi)
    dist_km = rnd.uniform(0.1, 0.2)
    dlat = (dist_km / 111.0) * math.cos(angle)
    dlng = (dist_km / (111.0 * math.cos(math.radians(listing.latitude)))) * math.sin(angle)
    return round(listing.latitude + dlat, 5), round(listing.longitude + dlng, 5)


def now_tn():
    """Heure actuelle en Tunisie (UTC+1, pas de changement d'heure), sans fuseau."""
    from datetime import datetime, timedelta
    return datetime.utcnow() + timedelta(hours=1)


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


def format_slot(dt):
    """« samedi 7 novembre à 10 h 30 »"""
    h = f"{dt.hour} h" + (f" {dt.minute:02d}" if dt.minute else "")
    return f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} à {h}"


def walking_minutes(km):
    # ~5 km/h, majoré de 20 % car les rues ne sont pas en ligne droite
    return max(1, round(km * 1.2 / 5 * 60))


def format_price(value):
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return value


def allowed_image(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]


# ---------------------------------------------------------------------------
# Stockage des photos
# - Si la variable d'environnement CLOUDINARY_URL est définie, les photos sont
#   envoyées sur Cloudinary (elles survivent aux redéploiements). En base, on
#   enregistre alors "cld:<public_id>".
# - Sinon, elles sont enregistrées dans app/static/uploads (développement local).
# ---------------------------------------------------------------------------
CLD_PREFIX = "cld:"
SIZES = {
    "large": dict(width=1600, height=1600, crop="limit"),
    "thumb": dict(width=640, height=480, crop="fill", gravity="auto"),
}


def cloudinary_enabled():
    return bool(os.environ.get("CLOUDINARY_URL"))


def save_image(file_storage):
    """Enregistre une photo et renvoie la référence à stocker en base (None si invalide ou en échec)."""
    if not file_storage or not file_storage.filename or not allowed_image(file_storage.filename):
        return None
    if cloudinary_enabled():
        import cloudinary.uploader
        try:
            res = cloudinary.uploader.upload(file_storage.stream, folder="ikrili", resource_type="image",
                                             public_id=uuid.uuid4().hex)
            return CLD_PREFIX + res["public_id"]
        except Exception:  # réseau, quota, fichier corrompu...
            current_app.logger.exception("Échec de l'envoi de la photo sur Cloudinary")
            return None
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


def delete_image(ref):
    """Supprime une photo, sur Cloudinary ou sur le disque. Ne lève jamais d'erreur."""
    try:
        if ref.startswith(CLD_PREFIX):
            if cloudinary_enabled():
                import cloudinary.uploader
                cloudinary.uploader.destroy(ref[len(CLD_PREFIX):], invalidate=True)
        else:
            path = os.path.join(current_app.config["UPLOAD_FOLDER"], ref)
            if os.path.exists(path):
                os.remove(path)
    except Exception:
        current_app.logger.exception("Impossible de supprimer la photo %s", ref)


def image_url(ref, size="large", external=False):
    """URL d'affichage d'une photo. Cloudinary optimise format et qualité automatiquement."""
    if ref.startswith(CLD_PREFIX):
        from cloudinary.utils import cloudinary_url
        return cloudinary_url(ref[len(CLD_PREFIX):], secure=True, quality="auto", fetch_format="auto",
                              **SIZES.get(size, SIZES["large"]))[0]
    from flask import url_for
    return url_for("static", filename="uploads/" + ref, _external=external)
