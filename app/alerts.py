"""Alertes de recherche : prévenir un utilisateur quand une nouvelle annonce correspond."""
import json
from datetime import datetime
from urllib.parse import parse_qsl

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.mail import mail_enabled, send_email
from app.models import Listing, SavedSearch, notify
from app.search import clean_params, describe, run_search
from app.security import hit, limited

bp = Blueprint("alerts", __name__, url_prefix="/alertes")

MAX_ALERTS = 10


@bp.route("/")
@login_required
def index():
    items = SavedSearch.query.filter_by(user_id=current_user.id).order_by(SavedSearch.created_at.desc()).all()
    return render_template("alerts.html", items=items, mail_on=mail_enabled())


@bp.route("/creer", methods=["POST"])
@login_required
def create():
    params = clean_params(dict(parse_qsl(request.form.get("qs", ""))))
    back = url_for("main.search", **params)
    if not params:
        flash("Choisis au moins un critère (quartier, budget, université…) avant de créer une alerte.", "error")
        return redirect(back)
    mine = SavedSearch.query.filter_by(user_id=current_user.id).all()
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    if any(s.params == blob for s in mine):
        flash("Tu as déjà une alerte pour cette recherche.", "info")
        return redirect(url_for("alerts.index"))
    if len(mine) >= MAX_ALERTS:
        flash(f"Tu as déjà {MAX_ALERTS} alertes. Supprime-en une pour en créer une nouvelle.", "error")
        return redirect(url_for("alerts.index"))
    if limited("alert_user", current_user.id):
        flash("Tu as créé beaucoup d'alertes en peu de temps. Réessaie plus tard.", "error")
        return redirect(back)
    hit("alert_user", current_user.id)
    db.session.add(SavedSearch(user_id=current_user.id, params=blob, label=describe(params)[:300]))
    db.session.commit()
    flash("Alerte créée ! Tu seras prévenu dès qu'une nouvelle annonce correspondra à ta recherche.", "success")
    return redirect(back)


@bp.route("/<int:alert_id>/<action>", methods=["POST"])
@login_required
def update(alert_id, action):
    a = db.get_or_404(SavedSearch, alert_id)
    if a.user_id != current_user.id:
        abort(404)
    if action == "supprimer":
        db.session.delete(a)
        flash("Alerte supprimée.", "success")
    elif action == "pause":
        a.is_active = not a.is_active
        flash("Alerte réactivée." if a.is_active else "Alerte mise en pause.", "success")
    elif action == "email":
        a.by_email = not a.by_email
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("alerts.index"))


def trigger_alerts(listing):
    """À appeler après la publication d'une annonce. Renvoie le nombre d'utilisateurs prévenus."""
    if not listing.is_active:
        return 0
    sent = 0
    for a in SavedSearch.query.filter(SavedSearch.is_active.is_(True), SavedSearch.user_id != listing.owner_id).all():
        user = a.user
        if user.is_blocked:
            continue
        found, _, distances = run_search(a.criteria, query=Listing.query.filter(Listing.id == listing.id,
                                                                                Listing.is_active.is_(True)))
        if not found:
            continue
        a.match_count = (a.match_count or 0) + 1
        a.last_match_at = datetime.utcnow()
        extra = f" · {distances[listing.id]} min à pied" if listing.id in distances else ""
        url = url_for("main.listing_detail", listing_id=listing.id)
        notify(user.id, "alerte", f"Nouvelle annonce pour ton alerte : {listing.title}",
               f"{listing.city} · {listing.price_tnd} TND/mois{extra} — {a.label}", url)
        if a.by_email and user.email_verified and mail_enabled():
            try:
                send_email(user.email, f"Nouvelle annonce : {listing.title} – IKRILI", "alert", user=user,
                           listing=listing, alert=a, extra=extra,
                           link=url_for("main.listing_detail", listing_id=listing.id, _external=True),
                           manage=url_for("alerts.index", _external=True))
            except Exception:
                current_app.logger.exception("E-mail d'alerte non envoyé")
        sent += 1
    db.session.commit()
    return sent
