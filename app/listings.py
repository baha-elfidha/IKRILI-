from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Listing, Photo, notify
from app.security import hit, limited, needs_email_verification
from app.utils import GOVERNORATES, PROPERTY_TYPES, delete_image, save_image

bp = Blueprint("listings", __name__, url_prefix="/proprietaire")

MAX_PHOTOS = 10


def _fill_listing(listing, form):
    """Remplit une annonce à partir du formulaire ; renvoie la liste des erreurs."""
    errors = []
    listing.title = form.get("title", "").strip()
    listing.description = form.get("description", "").strip()
    listing.property_type = form.get("property_type", "")
    listing.typology = form.get("typology", "").strip() or None
    listing.rooms = form.get("rooms", type=int) or 1
    listing.surface_m2 = form.get("surface_m2", type=int)
    listing.floor = form.get("floor", "").strip() or None
    listing.price_tnd = form.get("price_tnd", type=int)
    listing.charges_info = form.get("charges_info", "").strip() or None
    listing.governorate = form.get("governorate", "")
    listing.city = form.get("city", "").strip()
    listing.latitude = form.get("latitude", type=float)
    listing.longitude = form.get("longitude", type=float)
    listing.target = form.get("target", "tous")
    listing.furnished = bool(form.get("furnished"))
    listing.is_colocation = bool(form.get("is_colocation"))
    listing.coloc_places = form.get("coloc_places", type=int) if listing.is_colocation else None
    listing.girls_only = bool(form.get("girls_only")) and listing.is_colocation
    for field, _label in Listing.AMENITIES:
        setattr(listing, field, bool(form.get(field)))
    available = form.get("available_from")
    listing.available_from = datetime.strptime(available, "%Y-%m-%d").date() if available else None

    if len(listing.title) < 8:
        errors.append("Le titre doit faire au moins 8 caractères.")
    if len(listing.description) < 30:
        errors.append("Décris le logement en au moins 30 caractères.")
    if listing.property_type not in dict(PROPERTY_TYPES):
        errors.append("Choisis un type de logement.")
    if not listing.price_tnd or listing.price_tnd <= 0:
        errors.append("Indique un loyer mensuel valide.")
    if listing.governorate not in GOVERNORATES:
        errors.append("Choisis un gouvernorat.")
    if not listing.city:
        errors.append("Indique la ville ou le quartier.")
    if listing.target not in ("etudiants", "familles", "tous"):
        listing.target = "tous"
    return errors


def _save_photos(listing):
    """Enregistre les photos envoyées ; prévient l'utilisateur si certaines n'ont pas pu l'être."""
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    failed = 0
    for f in files:
        if len(listing.photos) >= MAX_PHOTOS:
            break
        name = save_image(f)
        if name:
            listing.photos.append(Photo(filename=name, position=len(listing.photos)))
        else:
            failed += 1
    if failed:
        flash(f"{failed} photo{'s' if failed > 1 else ''} n'{'ont' if failed > 1 else 'a'} pas pu être "
              "enregistrée(s) (format JPG, PNG ou WEBP uniquement). Tu peux réessayer en modifiant l'annonce.", "error")


def _owned_or_404(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.owner_id != current_user.id and current_user.role != "admin":
        abort(403)
    return listing


@bp.route("/publier", methods=["GET", "POST"])
@login_required
def create():
    if needs_email_verification(current_user):
        flash("Confirme d'abord ton adresse e-mail pour publier une annonce.", "error")
        return redirect(url_for("auth.verify_pending"))
    listing = Listing()
    if request.method == "POST":
        if limited("publish_user", current_user.id) and not current_user.is_admin:
            flash("Tu as publié beaucoup d'annonces aujourd'hui. Réessaie demain ou contacte-nous.", "error")
            return redirect(url_for("listings.dashboard"))
        listing.owner_id = current_user.id
        listing.is_verified = bool(current_user.is_verified)  # propriétaire vérifié => annonce vérifiée
        errors = _fill_listing(listing, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.session.add(listing)
            _save_photos(listing)
            hit("publish_user", current_user.id)
            if current_user.role == "locataire":
                current_user.role = "proprietaire"
            db.session.commit()
            flash("Ton annonce est en ligne.", "success")
            return redirect(url_for("main.listing_detail", listing_id=listing.id))
    return render_template("listing_form.html", listing=listing, form=request.form,
                           governorates=GOVERNORATES, property_types=PROPERTY_TYPES,
                           amenities=Listing.AMENITIES, editing=False)


@bp.route("/annonce/<int:listing_id>/modifier", methods=["GET", "POST"])
@login_required
def edit(listing_id):
    listing = _owned_or_404(listing_id)
    if request.method == "POST":
        errors = _fill_listing(listing, request.form)
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "error")
        else:
            _save_photos(listing)
            db.session.commit()
            flash("Annonce mise à jour.", "success")
            return redirect(url_for("main.listing_detail", listing_id=listing.id))
    return render_template("listing_form.html", listing=listing, form=request.form,
                           governorates=GOVERNORATES, property_types=PROPERTY_TYPES,
                           amenities=Listing.AMENITIES, editing=True)


@bp.route("/photo/<int:photo_id>/supprimer", methods=["POST"])
@login_required
def delete_photo(photo_id):
    photo = db.get_or_404(Photo, photo_id)
    listing = _owned_or_404(photo.listing_id)
    ref = photo.filename
    db.session.delete(photo)
    for i, p in enumerate(p for p in listing.photos if p.id != photo_id):
        p.position = i
    db.session.commit()
    delete_image(ref)
    if request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With"):
        return jsonify(ok=True)
    flash("Photo supprimée.", "success")
    return redirect(url_for("listings.edit", listing_id=listing.id))


@bp.route("/annonce/<int:listing_id>/statut", methods=["POST"])
@login_required
def toggle_active(listing_id):
    listing = _owned_or_404(listing_id)
    if listing.admin_hidden and not current_user.is_admin:
        flash("Cette annonce a été masquée par l'équipe IKRILI. Contacte-nous pour la remettre en ligne.", "error")
        return redirect(url_for("listings.dashboard"))
    listing.is_active = not listing.is_active
    db.session.commit()
    flash("Annonce réactivée." if listing.is_active else "Annonce masquée (marquée comme louée).", "success")
    return redirect(url_for("listings.dashboard"))


@bp.route("/annonce/<int:listing_id>/supprimer", methods=["POST"])
@login_required
def delete(listing_id):
    listing = _owned_or_404(listing_id)
    refs = [p.filename for p in listing.photos]
    db.session.delete(listing)
    db.session.commit()
    for ref in refs:
        delete_image(ref)
    flash("Annonce supprimée.", "success")
    return redirect(url_for("listings.dashboard"))


@bp.route("/")
@login_required
def dashboard():
    my_listings = Listing.query.filter_by(owner_id=current_user.id).order_by(Listing.created_at.desc()).all()
    received = (ContactRequest.query.join(Listing)
                .filter(Listing.owner_id == current_user.id)
                .order_by(ContactRequest.created_at.desc()).all())
    sent = (ContactRequest.query.filter_by(sender_id=current_user.id)
            .order_by(ContactRequest.created_at.desc()).all())
    stats = {
        "online": sum(1 for l in my_listings if l.is_active),
        "new": sum(1 for r in received if r.status == "nouveau"),
        "accepted": sum(1 for r in received if r.status == "accepte"),
        "favs": sum(len(l.favorited_by) for l in my_listings),
    }
    tab = request.args.get("onglet", "demandes" if stats["new"] else "annonces")
    return render_template("dashboard.html", my_listings=my_listings, received=received, sent=sent,
                           stats=stats, tab=tab)


@bp.route("/demande/<int:request_id>/<action>", methods=["POST"])
@login_required
def answer_request(request_id, action):
    req = db.get_or_404(ContactRequest, request_id)
    if req.listing.owner_id != current_user.id:
        abort(403)
    if action in ("accepte", "refuse", "lu"):
        previous, req.status = req.status, action
        if action in ("accepte", "refuse") and previous != action:
            owner = req.listing.owner
            if action == "accepte":
                contact = owner.email + (f" · {owner.phone}" if owner.phone else "")
                notify(req.sender_id, "accepte", "Ta demande de visite a été acceptée !",
                       f"« {req.listing.title} » – contacte {owner.full_name} pour fixer l'heure : {contact}",
                       url_for("main.listing_detail", listing_id=req.listing_id))
            else:
                notify(req.sender_id, "refuse", "Ta demande de visite n'a pas été retenue",
                       f"« {req.listing.title} ». D'autres logements t'attendent sur IKRILI.",
                       url_for("main.search"))
        db.session.commit()
    return redirect(url_for("listings.dashboard", onglet="demandes"))
