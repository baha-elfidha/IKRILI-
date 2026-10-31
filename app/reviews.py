"""Avis des locataires sur le logement et le propriétaire, après une visite."""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Listing, Review, notify
from app.utils import now_tn

bp = Blueprint("reviews", __name__, url_prefix="/avis")


def can_review(user, listing):
    """Un avis est possible après une visite acceptée (dont la date est passée), une seule fois par annonce."""
    if not user.is_authenticated or user.id == listing.owner_id or user.is_blocked:
        return False
    if Review.query.filter_by(listing_id=listing.id, author_id=user.id).first():
        return False
    now = now_tn()
    for req in ContactRequest.query.filter_by(listing_id=listing.id, sender_id=user.id, kind="visite", status="accepte"):
        if req.slot is None or req.slot.start_at <= now:
            return True
    return False


def _back(listing_id):
    return redirect(url_for("main.listing_detail", listing_id=listing_id) + "#avis")


@bp.route("/annonce/<int:listing_id>", methods=["POST"])
@login_required
def create(listing_id):
    l = db.get_or_404(Listing, listing_id)
    if not can_review(current_user, l):
        flash("Tu peux donner ton avis seulement après une visite de ce logement, et une seule fois.", "error")
        return _back(l.id)
    rl, ro = request.form.get("rating_listing", type=int), request.form.get("rating_owner", type=int)
    comment = request.form.get("comment", "").strip()[:1000]
    if rl not in range(1, 6) or ro not in range(1, 6):
        flash("Donne une note de 1 à 5 étoiles au logement et au propriétaire.", "error")
        return _back(l.id)
    db.session.add(Review(listing_id=l.id, owner_id=l.owner_id, author_id=current_user.id,
                          rating_listing=rl, rating_owner=ro, comment=comment or None))
    notify(l.owner_id, "avis", f"Nouvel avis sur ton annonce : {rl}/5",
           f"« {l.title} »" + (f" – {comment[:150]}" if comment else ""),
           url_for("main.listing_detail", listing_id=l.id) + "#avis")
    db.session.commit()
    flash("Merci pour ton avis ! Il aide les autres locataires à choisir.", "success")
    return _back(l.id)


@bp.route("/<int:review_id>/repondre", methods=["POST"])
@login_required
def reply(review_id):
    r = db.get_or_404(Review, review_id)
    if r.owner_id != current_user.id:
        abort(403)
    text = request.form.get("reply", "").strip()[:1000]
    if r.owner_reply:
        flash("Tu as déjà répondu à cet avis.", "info")
    elif not text:
        flash("Écris ta réponse avant d'envoyer.", "error")
    else:
        r.owner_reply, r.replied_at = text, datetime.utcnow()
        notify(r.author_id, "avis", "Le propriétaire a répondu à ton avis", f"« {r.listing.title} » – {text[:150]}",
               url_for("main.listing_detail", listing_id=r.listing_id) + "#avis")
        db.session.commit()
        flash("Réponse publiée.", "success")
    return _back(r.listing_id)


@bp.route("/<int:review_id>/supprimer", methods=["POST"])
@login_required
def delete(review_id):
    r = db.get_or_404(Review, review_id)
    if r.author_id != current_user.id and not current_user.is_admin:
        abort(403)
    lid = r.listing_id
    db.session.delete(r)
    db.session.commit()
    flash("Avis supprimé.", "success")
    return _back(lid)
