from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Listing, Report, University
from app.utils import GOVERNORATES, PROPERTY_TYPES, approx_coords, haversine_km, walking_minutes

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    universities = University.query.order_by(University.name).all()
    active = Listing.query.filter_by(is_active=True)
    recent = active.order_by(Listing.created_at.desc()).limit(8).all()

    # Annonce mise en avant dans le bandeau : la plus récente avec photo
    featured = next((l for l in recent if l.cover), recent[0] if recent else None)

    # Quartiers les plus fournis (données réelles)
    areas = (db.session.query(Listing.city, Listing.governorate, db.func.count(Listing.id),
                              db.func.min(Listing.price_tnd))
             .filter(Listing.is_active.is_(True))
             .group_by(Listing.city, Listing.governorate)
             .order_by(db.func.count(Listing.id).desc()).limit(6).all())

    # Nombre d'annonces à moins de 20 min à pied de chaque université
    located = [l for l in active.all() if l.latitude is not None]
    uni_counts = {}
    for u in universities:
        uni_counts[u.id] = sum(1 for l in located if walking_minutes(
            haversine_km(u.latitude, u.longitude, l.latitude, l.longitude)) <= 20)

    return render_template("index.html", universities=universities, recent=recent, featured=featured,
                           areas=areas, uni_counts=uni_counts, total=active.count(),
                           property_types=PROPERTY_TYPES)


def _bool_arg(name):
    return request.args.get(name) in ("1", "on", "true")


@bp.route("/recherche")
def search():
    args = request.args
    q = Listing.query.filter_by(is_active=True)

    text = args.get("q", "").strip()
    if text:
        like = f"%{text}%"
        q = q.filter(db.or_(Listing.city.ilike(like), Listing.governorate.ilike(like),
                            Listing.title.ilike(like)))
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
    if _bool_arg("meuble"):
        q = q.filter(Listing.furnished.is_(True))
    if _bool_arg("coloc"):
        q = q.filter(Listing.is_colocation.is_(True))
    if _bool_arg("filles"):
        q = q.filter(Listing.girls_only.is_(True))
    for field, _label in Listing.AMENITIES:
        if _bool_arg(field):
            q = q.filter(getattr(Listing, field).is_(True))

    sort = args.get("tri", "recent")
    if sort == "prix":
        q = q.order_by(Listing.price_tnd.asc())
    else:
        q = q.order_by(Listing.created_at.desc())

    listings = q.all()

    # Filtre de distance par rapport à une université (calculé en Python ;
    # à remplacer par PostGIS quand le volume d'annonces augmentera).
    university = None
    distances = {}
    uni_id = args.get("universite", type=int)
    if uni_id:
        university = db.session.get(University, uni_id)
    if university:
        max_min = args.get("minutes", default=30, type=int)
        kept = []
        for l in listings:
            if l.latitude is None or l.longitude is None:
                continue
            mins = walking_minutes(haversine_km(university.latitude, university.longitude,
                                                l.latitude, l.longitude))
            if mins <= max_min:
                distances[l.id] = mins
                kept.append(l)
        listings = kept
        if sort == "proche":
            listings.sort(key=lambda l: distances[l.id])

    # Points pour la carte : toutes les annonces filtrées (pas seulement la page),
    # avec une position volontairement approximative.
    map_points = []
    for l in listings[:300]:
        pos = approx_coords(l)
        if not pos:
            continue
        map_points.append({
            "id": l.id, "lat": pos[0], "lng": pos[1], "price": l.price_tnd,
            "title": l.title, "place": f"{l.city}, {l.governorate}",
            "url": url_for("main.listing_detail", listing_id=l.id),
            "photo": url_for("static", filename="uploads/" + l.cover.filename) if l.cover else None,
        })
    map_university = ({"lat": university.latitude, "lng": university.longitude, "name": university.name,
                       "minutes": args.get("minutes", default=30, type=int)} if university else None)

    per_page = 12
    page = max(1, args.get("page", default=1, type=int))
    total = len(listings)
    pages = max(1, (total + per_page - 1) // per_page)
    listings = listings[(page - 1) * per_page: page * per_page]

    return render_template(
        "search.html", listings=listings, total=total, page=page, pages=pages,
        university=university, distances=distances,
        universities=University.query.order_by(University.name).all(),
        governorates=GOVERNORATES, property_types=PROPERTY_TYPES,
        amenities=Listing.AMENITIES, args=args,
        map_points=map_points, map_university=map_university,
        chips=_quick_chips(args), active_filters=_count_filters(args),
    )


_FILTER_KEYS = ["gouvernorat", "type", "min", "max", "chambres", "pour", "meuble", "coloc", "filles",
                "universite"] + [f for f, _ in Listing.AMENITIES]


def _count_filters(args):
    return sum(1 for k in _FILTER_KEYS if args.get(k))


def _quick_chips(args):
    """Filtres rapides affichés en pastilles : un clic ajoute ou retire le filtre."""
    defs = [("Étudiants", "pour", "etudiants"), ("Familles", "pour", "familles"),
            ("Meublé", "meuble", "1"), ("Colocation", "coloc", "1"),
            ("Moins de 500 TND", "max", "500"), ("Internet", "has_internet", "1"),
            ("Parking", "has_parking", "1")]
    chips = []
    for label, key, value in defs:
        params = {k: v for k, v in args.items() if k != "page"}
        active = params.get(key) == value
        if active:
            params.pop(key)
        else:
            params[key] = value
        chips.append({"label": label, "active": active, "url": url_for("main.search", **params)})
    return chips


@bp.route("/annonce/<int:listing_id>")
def listing_detail(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if not listing.is_active and (not current_user.is_authenticated or current_user.id != listing.owner_id):
        abort(404)
    nearby = []
    if listing.latitude is not None:
        for u in University.query.all():
            mins = walking_minutes(haversine_km(u.latitude, u.longitude, listing.latitude, listing.longitude))
            if mins <= 40:
                nearby.append((u, mins))
        nearby.sort(key=lambda x: x[1])
    is_fav = current_user.is_authenticated and listing in current_user.favorite_listings
    return render_template("listing.html", listing=listing, nearby=nearby[:4], is_fav=is_fav,
                           approx=approx_coords(listing))


@bp.route("/annonce/<int:listing_id>/contact", methods=["POST"])
@login_required
def contact(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.owner_id == current_user.id:
        flash("C'est ta propre annonce.", "info")
        return redirect(url_for("main.listing_detail", listing_id=listing.id))
    kind = "visite" if request.form.get("kind") == "visite" else "message"
    body = request.form.get("body", "").strip()
    if kind == "visite" and not body:
        body = "Bonjour, je souhaiterais visiter ce logement. Quelles sont vos disponibilités ?"
    if not body:
        flash("Écris un message avant d'envoyer.", "error")
    else:
        db.session.add(ContactRequest(listing_id=listing.id, sender_id=current_user.id, kind=kind, body=body))
        db.session.commit()
        flash("Demande de visite envoyée au propriétaire." if kind == "visite"
              else "Message envoyé au propriétaire.", "success")
    return redirect(url_for("main.listing_detail", listing_id=listing.id))


@bp.route("/annonce/<int:listing_id>/favori", methods=["POST"])
@login_required
def toggle_favorite(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing in current_user.favorite_listings:
        current_user.favorite_listings.remove(listing)
    else:
        current_user.favorite_listings.append(listing)
    db.session.commit()
    return redirect(request.referrer or url_for("main.listing_detail", listing_id=listing.id))


@bp.route("/favoris")
@login_required
def favorites():
    return render_template("favorites.html", listings=current_user.favorite_listings)


REPORT_REASONS = [
    ("arnaque", "Demande d'argent avant visite / arnaque"),
    ("fausses_photos", "Photos ou informations fausses"),
    ("deja_loue", "Logement déjà loué"),
    ("autre", "Autre"),
]


@bp.route("/annonce/<int:listing_id>/signaler", methods=["GET", "POST"])
def report(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if request.method == "POST":
        reason = request.form.get("reason")
        if reason not in dict(REPORT_REASONS):
            flash("Choisis un motif.", "error")
        else:
            db.session.add(Report(listing_id=listing.id, reason=reason,
                                  details=request.form.get("details", "").strip(),
                                  reporter_id=current_user.id if current_user.is_authenticated else None))
            db.session.commit()
            flash("Merci, ton signalement a été transmis à l'équipe IKRILI.", "success")
            return redirect(url_for("main.listing_detail", listing_id=listing.id))
    return render_template("report.html", listing=listing, reasons=REPORT_REASONS)


@bp.route("/conseils-anti-arnaque")
def safety():
    return render_template("safety.html")


def _legal_context():
    cfg = current_app.config
    return dict(owner_name=cfg["LEGAL_OWNER_NAME"], contact_email=cfg["CONTACT_EMAIL"],
                site_url=cfg["SITE_URL"], updated=cfg["LEGAL_UPDATED"])


@bp.route("/conditions-utilisation")
def terms():
    return render_template("legal/cgu.html", **_legal_context())


@bp.route("/confidentialite")
def privacy():
    return render_template("legal/confidentialite.html", **_legal_context())
