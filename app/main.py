from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.messages import get_or_create_conversation, post_message
from app.models import ContactRequest, Conversation, Listing, Notification, Report, University, User, notify
from app.security import client_ip, hit, limited, needs_email_verification
from app.search import clean_params, run_search
from app.utils import GOVERNORATES, PROPERTY_TYPES, approx_coords, haversine_km, now_tn, walking_minutes

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
    listings, university, distances = run_search(args)

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
            "photo": l.cover.thumb if l.cover else None,
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
    if not listing.is_active and (not current_user.is_authenticated
                                  or (current_user.id != listing.owner_id and not current_user.is_admin)):
        abort(404)
    nearby = []
    if listing.latitude is not None:
        for u in University.query.all():
            mins = walking_minutes(haversine_km(u.latitude, u.longitude, listing.latitude, listing.longitude))
            if mins <= 40:
                nearby.append((u, mins))
        nearby.sort(key=lambda x: x[1])
    is_fav = current_user.is_authenticated and listing in current_user.favorite_listings
    conv = my_booking = None
    if current_user.is_authenticated and current_user.id != listing.owner_id:
        conv = Conversation.query.filter_by(listing_id=listing.id, tenant_id=current_user.id).first()
        now = now_tn()
        my_booking = next((s for s in listing.slots if s.booked_by_id == current_user.id and s.start_at > now), None)
    slot_days = {}
    for slot in listing.future_slots()[:40]:
        slot_days.setdefault(slot.start_at.date(), []).append(slot)
    return render_template("listing.html", listing=listing, nearby=nearby[:4], is_fav=is_fav,
                           approx=approx_coords(listing), conv=conv, my_booking=my_booking,
                           slot_days=sorted(slot_days.items())[:7],
                           owner_rating=listing.owner.owner_rating)


@bp.route("/annonce/<int:listing_id>/contact", methods=["POST"])
@login_required
def contact(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.owner_id == current_user.id:
        flash("C'est ta propre annonce.", "info")
        return redirect(url_for("main.listing_detail", listing_id=listing.id))
    back = redirect(url_for("main.listing_detail", listing_id=listing.id))
    if needs_email_verification(current_user):
        flash("Confirme d'abord ton adresse e-mail pour contacter les propriétaires.", "error")
        return redirect(url_for("auth.verify_pending"))
    if not listing.is_active:
        flash("Cette annonce n'est plus disponible.", "error")
        return back
    uid = current_user.id
    new_account = current_user.account_age_hours < 24
    kind = "visite" if request.form.get("kind") == "visite" else "message"
    body = request.form.get("body", "").strip()[:2000]
    existing = Conversation.query.filter_by(listing_id=listing.id, tenant_id=uid).first()
    if existing and kind == "message":
        # Conversation déjà ouverte : on y envoie le message comme une réponse
        return redirect(url_for("messages.thread", conv_id=existing.id), code=307)
    if (limited("contact_user", uid) or limited("contact_user_day", uid)
            or (new_account and limited("contact_user_new", uid))):
        flash("Tu as envoyé beaucoup de messages en peu de temps. Réessaie un peu plus tard.", "error")
        return back
    if kind == "visite" and not body:
        body = "Bonjour, je souhaiterais visiter ce logement. Quelles sont vos disponibilités ?"
    if kind == "visite" and ContactRequest.query.filter(
            ContactRequest.listing_id == listing.id, ContactRequest.sender_id == uid,
            ContactRequest.kind == "visite", ContactRequest.status.in_(["nouveau", "lu"])).first():
        flash("Tu as déjà une demande de visite en attente pour ce logement.", "info")
        return redirect(url_for("messages.thread", conv_id=existing.id)) if existing else back
    if not body:
        flash("Écris un message avant d'envoyer.", "error")
        return back
    hit("contact_user", uid); hit("contact_user_day", uid)
    if new_account:
        hit("contact_user_new", uid)
    conv = get_or_create_conversation(listing, current_user)
    if kind == "visite":
        db.session.add(ContactRequest(listing_id=listing.id, sender_id=uid, kind="visite", body=body))
        post_message(conv, current_user, body, kind="visite",
                     notify_title=f"{current_user.full_name} demande une visite")
        flash("Demande de visite envoyée au propriétaire.", "success")
    else:
        post_message(conv, current_user, body)
        flash("Message envoyé au propriétaire.", "success")
    db.session.commit()
    return redirect(url_for("messages.thread", conv_id=conv.id))


@bp.route("/annonce/<int:listing_id>/favori", methods=["POST"])
@login_required
def toggle_favorite(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing in current_user.favorite_listings:
        current_user.favorite_listings.remove(listing)
    else:
        current_user.favorite_listings.append(listing)
        if listing.owner_id != current_user.id:
            # Une seule notification non lue par annonce : on évite le spam si quelqu'un clique plusieurs fois
            pending = Notification.query.filter_by(user_id=listing.owner_id, kind="favori", is_read=False,
                                                   url=url_for("main.listing_detail", listing_id=listing.id)).first()
            if not pending:
                notify(listing.owner_id, "favori", "Ton annonce a été ajoutée en favori",
                       f"« {listing.title} » intéresse un locataire.",
                       url_for("main.listing_detail", listing_id=listing.id))
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
        if limited("report_ip", client_ip()):
            flash("Tu as déjà envoyé plusieurs signalements. Réessaie plus tard.", "error")
            return redirect(url_for("main.listing_detail", listing_id=listing.id))
        if reason not in dict(REPORT_REASONS):
            flash("Choisis un motif.", "error")
        else:
            hit("report_ip", client_ip())
            db.session.add(Report(listing_id=listing.id, reason=reason,
                                  details=request.form.get("details", "").strip()[:2000],
                                  reporter_id=current_user.id if current_user.is_authenticated else None))
            for admin in User.query.filter_by(role="admin").all():
                notify(admin.id, "signalement", "Annonce signalée : " + dict(REPORT_REASONS)[reason],
                       f"« {listing.title} » – {listing.city}",
                       url_for("main.listing_detail", listing_id=listing.id))
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
