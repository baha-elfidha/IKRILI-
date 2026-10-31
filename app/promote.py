"""Revenus : mise en avant payante des annonces (« À la une ») et comptes agence.

Le paiement est manuel au lancement : le propriétaire choisit une formule, reçoit une référence,
paie par D17 ou virement en indiquant cette référence, puis un administrateur active la mise en avant.
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Boost, Listing, User, notify
from app.security import admin_required, hit, limited

bp = Blueprint("promote", __name__)


def plans():
    out = []
    for part in (current_app.config.get("BOOST_PLANS") or "").split(","):
        try:
            days, price = (int(x) for x in part.split(":"))
        except ValueError:
            continue
        if days > 0 and price >= 0:
            out.append((days, price))
    return out or [(7, 15), (30, 45)]


def payment_methods():
    c = current_app.config
    methods = []
    if c.get("PAY_D17"):
        methods.append(("D17", f"Envoie le montant au {c['PAY_D17']} depuis l'application D17 (La Poste)."))
    if c.get("PAY_RIB"):
        methods.append(("Virement", f"RIB : {c['PAY_RIB']}" + (f" – au nom de {c['PAY_HOLDER']}" if c.get("PAY_HOLDER") else "")))
    return methods


def _admins():
    return User.query.filter((User.role == "admin")).all()


def _new_reference(listing_id):
    while True:
        ref = f"IKR-{listing_id}-{secrets.token_hex(2).upper()}"
        if not Boost.query.filter_by(reference=ref).first():
            return ref


# ------------------------------------------------------------------ mise en avant (propriétaire)
@bp.route("/proprietaire/annonce/<int:listing_id>/mettre-en-avant", methods=["GET", "POST"])
@login_required
def boost(listing_id):
    l = db.get_or_404(Listing, listing_id)
    if l.owner_id != current_user.id:
        abort(403)
    pending = Boost.query.filter_by(listing_id=l.id, status="en_attente").first()
    if request.method == "POST":
        if not l.is_active:
            flash("Remets d'abord ton annonce en ligne pour la mettre en avant.", "error")
            return redirect(url_for("listings.dashboard"))
        if pending:
            return redirect(url_for("promote.boost_status", reference=pending.reference))
        days = request.form.get("days", type=int)
        plan = next((p for p in plans() if p[0] == days), None)
        if not plan:
            flash("Choisis une formule.", "error")
            return redirect(url_for("promote.boost", listing_id=l.id))
        if limited("boost_user", current_user.id):
            flash("Trop de demandes aujourd'hui. Réessaie demain.", "error")
            return redirect(url_for("listings.dashboard"))
        hit("boost_user", current_user.id)
        b = Boost(listing_id=l.id, user_id=current_user.id, days=plan[0], price_tnd=plan[1],
                  reference=_new_reference(l.id))
        db.session.add(b)
        db.session.flush()
        for a in _admins():
            notify(a.id, "promo", f"Mise en avant demandée : {b.reference}",
                   f"« {l.title} » – {b.days} jours, {b.price_tnd} TND. Vérifie le paiement puis active.",
                   url_for("promote.admin_list"))
        db.session.commit()
        if plan[1] == 0:
            flash("Demande envoyée : elle sera activée sous peu.", "success")
        return redirect(url_for("promote.boost_status", reference=b.reference))
    return render_template("promote/boost.html", listing=l, plans=plans(), pending=pending,
                           history=Boost.query.filter_by(listing_id=l.id).order_by(Boost.created_at.desc()).limit(5).all())


@bp.route("/proprietaire/mise-en-avant/<reference>")
@login_required
def boost_status(reference):
    b = Boost.query.filter_by(reference=reference).first_or_404()
    if b.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template("promote/status.html", b=b, methods=payment_methods())


@bp.route("/proprietaire/mise-en-avant/<reference>/annuler", methods=["POST"])
@login_required
def boost_cancel(reference):
    b = Boost.query.filter_by(reference=reference).first_or_404()
    if b.user_id != current_user.id:
        abort(403)
    if b.status == "en_attente":
        b.status, b.decided_at = "annulee", datetime.utcnow()
        db.session.commit()
        flash("Demande de mise en avant annulée.", "success")
    return redirect(url_for("listings.dashboard"))


# ------------------------------------------------------------------ compte agence
@bp.route("/proprietaire/agence", methods=["GET", "POST"])
@login_required
def agency():
    u = current_user
    if request.method == "POST":
        if u.agency_status == "validee":
            u.agency_bio = (request.form.get("agency_bio") or "").strip()[:1500] or None
            u.agency_city = (request.form.get("agency_city") or "").strip()[:120] or u.agency_city
            db.session.commit()
            flash("Page de l'agence mise à jour.", "success")
            return redirect(url_for("promote.agency"))
        name = (request.form.get("agency_name") or "").strip()[:120]
        tax = (request.form.get("agency_tax_id") or "").strip()[:40]
        city = (request.form.get("agency_city") or "").strip()[:120]
        errors = []
        if len(name) < 3:
            errors.append("Indique le nom de l'agence.")
        if len(tax) < 5:
            errors.append("Indique le matricule fiscal ou le numéro RNE de l'agence.")
        if not city:
            errors.append("Indique la ville de l'agence.")
        if errors:
            for e in errors:
                flash(e, "error")
        elif limited("agency_user", u.id):
            flash("Trop de demandes aujourd'hui. Réessaie demain.", "error")
        else:
            hit("agency_user", u.id)
            u.agency_name, u.agency_tax_id, u.agency_city = name, tax, city
            u.agency_bio = (request.form.get("agency_bio") or "").strip()[:1500] or None
            u.agency_status, u.agency_requested_at = "en_attente", datetime.utcnow()
            for a in _admins():
                notify(a.id, "promo", f"Demande de compte agence : {name}",
                       f"{u.full_name} – {city} – {tax}", url_for("promote.admin_list"))
            db.session.commit()
            flash("Demande envoyée. Nous vérifions les informations de l'agence et te répondons rapidement.", "success")
            return redirect(url_for("promote.agency"))
    return render_template("promote/agency.html", u=u, form=request.form,
                           price=current_app.config.get("AGENCY_MONTHLY_TND", 0), methods=payment_methods())


@bp.route("/agence/<int:user_id>")
def agency_page(user_id):
    u = db.get_or_404(User, user_id)
    if not u.is_agency or u.is_blocked:
        abort(404)
    listings = (Listing.query.filter_by(owner_id=u.id, is_active=True)
                .order_by(Listing.featured_until.desc().nullslast(), Listing.created_at.desc()).all())
    return render_template("promote/agency_page.html", agency=u, listings=listings)


# ------------------------------------------------------------------ administration
@bp.route("/admin/revenus")
@admin_required
def admin_list():
    pending = Boost.query.filter_by(status="en_attente").order_by(Boost.created_at).all()
    active = [b for b in Boost.query.filter_by(status="active").order_by(Boost.ends_at.desc()).limit(50) if b.state == "active"]
    agencies = User.query.filter_by(agency_status="en_attente").order_by(User.agency_requested_at).all()
    validated = User.query.filter_by(agency_status="validee").order_by(User.agency_name).all()
    month = datetime.utcnow() - timedelta(days=30)
    revenue = (db.session.query(db.func.coalesce(db.func.sum(Boost.price_tnd), 0))
               .filter(Boost.status == "active", Boost.decided_at >= month).scalar())
    return render_template("promote/admin.html", tab="revenus", pending=pending, active=active, agencies=agencies,
                           validated=validated, revenue=int(revenue or 0))


@bp.route("/admin/mise-en-avant/<int:boost_id>/<action>", methods=["POST"])
@admin_required
def admin_boost(boost_id, action):
    b = db.get_or_404(Boost, boost_id)
    if b.status != "en_attente" or action not in ("activer", "refuser"):
        abort(400)
    b.decided_at = datetime.utcnow()
    b.note = (request.form.get("note") or "").strip()[:200] or None
    l = b.listing
    if action == "activer":
        start = max(datetime.utcnow(), l.featured_until or datetime.utcnow())
        b.ends_at = l.featured_until = start + timedelta(days=b.days)
        b.status = "active"
        notify(b.user_id, "promo", "Ton annonce est à la une !",
               f"« {l.title} » apparaît en tête des résultats jusqu'au {b.ends_at.strftime('%d/%m/%Y')}.",
               url_for("main.listing_detail", listing_id=l.id))
        flash(f"Mise en avant {b.reference} activée jusqu'au {b.ends_at.strftime('%d/%m/%Y')}.", "success")
    else:
        b.status = "refusee"
        notify(b.user_id, "promo", "Mise en avant non activée",
               f"Référence {b.reference}" + (f" : {b.note}" if b.note else " : paiement non reçu.")
               + " Contacte-nous si tu as déjà payé.", url_for("promote.boost_status", reference=b.reference))
        flash(f"Demande {b.reference} refusée.", "success")
    db.session.commit()
    return redirect(url_for("promote.admin_list"))


@bp.route("/admin/agence/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_agency(user_id, action):
    u = db.get_or_404(User, user_id)
    if action == "valider" and u.agency_status in ("en_attente", "refusee"):
        u.agency_status = "validee"
        notify(u.id, "promo", "Ton compte agence est validé",
               f"{u.agency_name} a maintenant sa page publique et le badge « Agence » sur ses annonces.",
               url_for("promote.agency_page", user_id=u.id))
    elif action == "refuser" and u.agency_status == "en_attente":
        u.agency_status = "refusee"
        note = (request.form.get("note") or "").strip()[:200]
        notify(u.id, "promo", "Demande de compte agence non validée",
               note or "Les informations n'ont pas pu être vérifiées. Réponds-nous pour compléter.",
               url_for("promote.agency"))
    elif action == "retirer" and u.agency_status == "validee":
        u.agency_status = None
    else:
        abort(400)
    db.session.commit()
    flash("Compte agence mis à jour.", "success")
    return redirect(url_for("promote.admin_list"))
