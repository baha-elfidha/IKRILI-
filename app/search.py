"""Logique de recherche partagée entre la page de recherche et les alertes."""
from werkzeug.datastructures import MultiDict

from app import db
from app.models import Listing, University
from app.utils import haversine_km, walking_minutes

# Paramètres d'URL reconnus par la recherche (le reste est ignoré dans les alertes)
SEARCH_KEYS = ["q", "gouvernorat", "type", "min", "max", "chambres", "pour", "meuble", "coloc", "filles",
               "universite", "minutes"] + [f for f, _ in Listing.AMENITIES]


def _flag(args, name):
    return args.get(name) in ("1", "on", "true")


def run_search(args, query=None, sort=None):
    """Applique les filtres de recherche.

    args : MultiDict (request.args) ou dict. Renvoie (annonces, université, distances {id: minutes}).
    """
    if not isinstance(args, MultiDict):
        args = MultiDict(args or {})
    q = query if query is not None else Listing.query.filter_by(is_active=True)

    text = args.get("q", "").strip()
    if text:
        like = f"%{text}%"
        q = q.filter(db.or_(Listing.city.ilike(like), Listing.governorate.ilike(like), Listing.title.ilike(like)))
    if args.get("gouvernorat"):
        q = q.filter(Listing.governorate == args["gouvernorat"])
    if args.get("type"):
        q = q.filter(Listing.property_type == args["type"])
    if args.get("min", type=int):
        q = q.filter(Listing.price_tnd >= args.get("min", type=int))
    if args.get("max", type=int):
        q = q.filter(Listing.price_tnd <= args.get("max", type=int))
    if args.get("chambres", type=int):
        q = q.filter(Listing.rooms >= args.get("chambres", type=int))
    if args.get("pour") in ("etudiants", "familles"):
        q = q.filter(Listing.target.in_([args["pour"], "tous"]))
    if _flag(args, "meuble"):
        q = q.filter(Listing.furnished.is_(True))
    if _flag(args, "coloc"):
        q = q.filter(Listing.is_colocation.is_(True))
    if _flag(args, "filles"):
        q = q.filter(Listing.girls_only.is_(True))
    for field, _label in Listing.AMENITIES:
        if _flag(args, field):
            q = q.filter(getattr(Listing, field).is_(True))

    sort = sort or args.get("tri", "recent")
    q = q.order_by(Listing.price_tnd.asc() if sort == "prix" else Listing.created_at.desc())
    listings = q.all()

    # Distance à une université (calculée en Python ; PostGIS quand le volume augmentera)
    university, distances = None, {}
    uni_id = args.get("universite", type=int)
    if uni_id:
        university = db.session.get(University, uni_id)
    if university:
        max_min = args.get("minutes", default=30, type=int)
        kept = []
        for l in listings:
            if l.latitude is None or l.longitude is None:
                continue
            mins = walking_minutes(haversine_km(university.latitude, university.longitude, l.latitude, l.longitude))
            if mins <= max_min:
                distances[l.id] = mins
                kept.append(l)
        listings = kept
        if sort == "proche":
            listings.sort(key=lambda l: distances[l.id])
    return listings, university, distances


def clean_params(args):
    """Garde seulement les critères utiles (pour enregistrer une alerte)."""
    params = {k: args.get(k) for k in SEARCH_KEYS if args.get(k) not in (None, "")}
    if "minutes" in params and "universite" not in params:
        params.pop("minutes")
    return params


def describe(params):
    """Résumé lisible d'une recherche : « S+… · Ariana · max 600 TND · près d'ESPRIT (20 min) »."""
    from app.utils import PROPERTY_TYPES
    parts = []
    if params.get("type"):
        parts.append(dict(PROPERTY_TYPES).get(params["type"], params["type"]))
    if params.get("q"):
        parts.append(f"« {params['q']} »")
    if params.get("gouvernorat"):
        parts.append(params["gouvernorat"])
    if params.get("min") and params.get("max"):
        parts.append(f"{params['min']}–{params['max']} TND")
    elif params.get("max"):
        parts.append(f"max {params['max']} TND")
    elif params.get("min"):
        parts.append(f"min {params['min']} TND")
    if params.get("chambres"):
        parts.append(f"{params['chambres']} ch. ou +")
    if params.get("universite"):
        u = db.session.get(University, int(params["universite"])) if str(params["universite"]).isdigit() else None
        if u:
            parts.append(f"près de {u.name} ({params.get('minutes', 30)} min)")
    labels = {"meuble": "meublé", "coloc": "colocation", "filles": "filles uniquement"}
    labels.update({f: l.lower() for f, l in Listing.AMENITIES})
    for k, l in labels.items():
        if params.get(k):
            parts.append(l)
    if params.get("pour") == "etudiants":
        parts.append("étudiants")
    elif params.get("pour") == "familles":
        parts.append("familles")
    return " · ".join(parts) or "Toutes les annonces"
