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


def save_image(file_storage):
    """Enregistre une photo et renvoie son nom de fichier (None si invalide)."""
    if not file_storage or not file_storage.filename or not allowed_image(file_storage.filename):
        return None
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name
