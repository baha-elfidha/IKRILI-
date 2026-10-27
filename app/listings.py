from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Listing, Photo
from app.utils import GOVERNORATES, PROPERTY_TYPES, save_image

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
    start = len(listing.photos)
    for i, f in enumerate(request.files.getlist("photos")):
        if start + i >= MAX_PHOTOS:
            break
        name = save_image(f)
        if name:
            listing.photos.append(Photo(filename=name, position=start + i))


def _owned_or_404(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.owner_id != current_user.id and current_user.role != "admin":
        abort(403)
    return listing


@bp.route("/publier", methods=["GET", "POST"])
@login_required
def create():
    listing = Listing()
    if request.method == "POST":
        listing.owner_id = current_user.id
        errors = _fill_listing(listing, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            db.session.add(listing)
            _save_photos(listing)
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


@bp.route("/annonce/<int:listing_id>/statut", methods=["POST"])
@login_required
def toggle_active(listing_id):
    listing = _owned_or_404(listing_id)
    listing.is_active = not listing.is_active
    db.session.commit()
    flash("Annonce réactivée." if listing.is_active else "Annonce masquée (marquée comme louée).", "success")
    return redirect(url_for("listings.dashboard"))


@bp.route("/annonce/<int:listing_id>/supprimer", methods=["POST"])
@login_required
def delete(listing_id):
    listing = _owned_or_404(listing_id)
    db.session.delete(listing)
    db.session.commit()
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
    return render_template("dashboard.html", my_listings=my_listings, received=received, sent=sent)


@bp.route("/demande/<int:request_id>/<action>", methods=["POST"])
@login_required
def answer_request(request_id, action):
    req = db.get_or_404(ContactRequest, request_id)
    if req.listing.owner_id != current_user.id:
        abort(403)
    if action in ("accepte", "refuse", "lu"):
        req.status = action
        db.session.commit()
    return redirect(url_for("listings.dashboard"))
