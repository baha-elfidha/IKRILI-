from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Listing, Report, University
from app.utils import GOVERNORATES, PROPERTY_TYPES, haversine_km, walking_minutes

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    universities = University.query.order_by(University.name).all()
    recent = (Listing.query.filter_by(is_active=True)
              .order_by(Listing.created_at.desc()).limit(8).all())
    return render_template("index.html", universities=universities, recent=recent,
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
    )


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
    return render_template("listing.html", listing=listing, nearby=nearby[:4], is_fav=is_fav)


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
