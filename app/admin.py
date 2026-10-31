"""Espace administrateur : signalements, annonces, utilisateurs."""
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import ContactRequest, Listing, Report, User, notify
from app.security import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

REASON_LABELS = {
    "arnaque": "Arnaque / argent demandé avant visite",
    "fausses_photos": "Photos ou informations fausses",
    "deja_loue": "Logement déjà loué",
    "autre": "Autre",
}


def _back(default):
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(default)


@bp.context_processor
def admin_ctx():
    return {"pending_reports": Report.query.filter_by(status="nouveau").count(), "reason_labels": REASON_LABELS}


# ------------------------------------------------------------------ tableau de bord
@bp.route("/")
@admin_required
def index():
    week = datetime.utcnow() - timedelta(days=7)
    stats = {
        "users": User.query.count(),
        "users_week": User.query.filter(User.created_at >= week).count(),
        "online": Listing.query.filter_by(is_active=True).count(),
        "listings_week": Listing.query.filter(Listing.created_at >= week).count(),
        "requests_week": ContactRequest.query.filter(ContactRequest.created_at >= week).count(),
        "blocked": User.query.filter_by(is_blocked=True).count(),
        "unverified_owners": User.query.filter(User.role.in_(["proprietaire"]), User.is_verified.is_(False),
                                               User.listings.any()).count(),
    }
    reports = Report.query.filter_by(status="nouveau").order_by(Report.created_at.desc()).limit(5).all()
    new_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    return render_template("admin/index.html", stats=stats, reports=reports, new_users=new_users, tab="index")


# ------------------------------------------------------------------ signalements
@bp.route("/signalements")
@admin_required
def reports():
    status = request.args.get("statut", "nouveau")
    if status not in ("nouveau", "traite", "rejete"):
        status = "nouveau"
    items = Report.query.filter_by(status=status).order_by(Report.created_at.desc()).limit(200).all()
    counts = {l.listing_id: 0 for l in items}
    for lid in counts:
        counts[lid] = Report.query.filter_by(listing_id=lid).count()
    return render_template("admin/reports.html", items=items, status=status, counts=counts, tab="reports")


@bp.route("/signalement/<int:report_id>/<action>", methods=["POST"])
@admin_required
def handle_report(report_id, action):
    r = db.get_or_404(Report, report_id)
    if action == "masquer":
        _hide_listing(r.listing, "Suite à un signalement : " + REASON_LABELS.get(r.reason, r.reason))
        flash("Annonce masquée et signalement traité.", "success")
    elif action in ("traite", "rejete"):
        r.status, r.handled_at = action, datetime.utcnow()
        flash("Signalement rejeté." if action == "rejete" else "Signalement marqué comme traité.", "success")
    else:
        abort(400)
    db.session.commit()
    return _back(url_for("admin.reports"))


# ------------------------------------------------------------------ annonces
def _hide_listing(listing, why):
    listing.is_active = False
    listing.admin_hidden = "moderation"
    now = datetime.utcnow()
    for rep in listing.reports:
        if rep.status == "nouveau":
            rep.status, rep.handled_at = "traite", now
    notify(listing.owner_id, "signalement", "Ton annonce a été masquée par l'équipe IKRILI",
           f"« {listing.title} ». {why}. Réponds à l'e-mail de contact si tu penses qu'il s'agit d'une erreur.",
           url_for("listings.dashboard"))


@bp.route("/annonces")
@admin_required
def listings():
    q = request.args.get("q", "").strip()
    filt = request.args.get("filtre", "")
    query = Listing.query
    if q:
        if q.isdigit():
            query = query.filter(Listing.id == int(q))
        else:
            like = f"%{q}%"
            query = query.join(User, Listing.owner_id == User.id).filter(db.or_(
                Listing.title.ilike(like), Listing.city.ilike(like), User.email.ilike(like), User.full_name.ilike(like)))
    if filt == "masquees":
        query = query.filter(Listing.admin_hidden.isnot(None))
    elif filt == "signalees":
        query = query.filter(Listing.reports.any(Report.status == "nouveau"))
    elif filt == "non_verifiees":
        query = query.filter(Listing.is_verified.is_(False), Listing.is_active.is_(True))
    items = query.order_by(Listing.created_at.desc()).limit(200).all()
    return render_template("admin/listings.html", items=items, q=q, filt=filt, tab="listings")


@bp.route("/annonce/<int:listing_id>/<action>", methods=["POST"])
@admin_required
def listing_action(listing_id, action):
    l = db.get_or_404(Listing, listing_id)
    if action == "masquer":
        reason = request.form.get("reason", "").strip() or "Elle ne respecte pas les règles du site"
        _hide_listing(l, reason)
        flash("Annonce masquée. Le propriétaire a été prévenu.", "success")
    elif action == "afficher":
        if l.owner.is_blocked:
            flash("Le propriétaire est bloqué : débloque d'abord son compte.", "error")
            return _back(url_for("admin.listings"))
        l.is_active, l.admin_hidden = True, None
        flash("Annonce remise en ligne.", "success")
    elif action == "verifier":
        l.is_verified = not l.is_verified
        flash("Badge « vérifiée » ajouté." if l.is_verified else "Badge « vérifiée » retiré.", "success")
    else:
        abort(400)
    db.session.commit()
    return _back(url_for("admin.listings"))


# ------------------------------------------------------------------ utilisateurs
@bp.route("/utilisateurs")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    filt = request.args.get("filtre", "")
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.email.ilike(like), User.full_name.ilike(like), User.phone.ilike(like)))
    if filt == "proprietaires":
        query = query.filter(User.listings.any())
    elif filt == "bloques":
        query = query.filter(User.is_blocked.is_(True))
    elif filt == "a_verifier":
        query = query.filter(User.listings.any(), User.is_verified.is_(False))
    elif filt == "email_non_confirme":
        query = query.filter(User.email_verified.is_(False))
    items = query.order_by(User.created_at.desc()).limit(200).all()
    return render_template("admin/users.html", items=items, q=q, filt=filt, tab="users")


@bp.route("/utilisateur/<int:user_id>/<action>", methods=["POST"])
@admin_required
def user_action(user_id, action):
    u = db.get_or_404(User, user_id)
    if u.id == current_user.id or (u.is_admin and action == "bloquer"):
        flash("Action impossible sur ce compte.", "error")
        return _back(url_for("admin.users"))
    if action == "bloquer":
        u.is_blocked = True
        u.blocked_reason = request.form.get("reason", "").strip()[:200] or None
        for l in u.listings:
            if l.is_active:
                l.is_active, l.admin_hidden = False, "blocage"
        flash(f"Compte de {u.full_name} bloqué. Ses annonces sont masquées.", "success")
    elif action == "debloquer":
        u.is_blocked, u.blocked_reason = False, None
        for l in u.listings:
            if l.admin_hidden == "blocage":
                l.is_active, l.admin_hidden = True, None
        flash(f"Compte de {u.full_name} débloqué.", "success")
    elif action == "verifier":
        u.is_verified = not u.is_verified
        for l in u.listings:
            l.is_verified = u.is_verified
        if u.is_verified:
            notify(u.id, "accepte", "Ton identité a été vérifiée",
                   "Tes annonces affichent maintenant le badge « vérifiée ». Elles inspirent plus confiance.",
                   url_for("listings.dashboard"))
        flash("Badge « identité vérifiée » ajouté." if u.is_verified else "Badge retiré.", "success")
    elif action == "confirmer_email":
        u.email_verified = True
        flash(f"E-mail de {u.full_name} confirmé manuellement.", "success")
    else:
        abort(400)
    db.session.commit()
    return _back(url_for("admin.users"))
