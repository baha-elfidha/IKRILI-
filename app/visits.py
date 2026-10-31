"""Rendez-vous de visite : créneaux proposés par le propriétaire, réservés par le locataire."""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.messages import get_or_create_conversation, post_message
from app.models import ContactRequest, Listing, VisitSlot, notify
from app.security import hit, limited, needs_email_verification
from app.utils import format_slot, now_tn

bp = Blueprint("visits", __name__, url_prefix="/visites")

DURATIONS = [20, 30, 45, 60]
MAX_FUTURE_SLOTS = 60      # par annonce
MAX_PER_BATCH = 16         # créneaux créés en une fois
MAX_DAYS_AHEAD = 60


def _own_listing(listing_id):
    l = db.get_or_404(Listing, listing_id)
    if l.owner_id != current_user.id and not current_user.is_admin:
        abort(403)
    return l


def _parse(date_s, time_s):
    try:
        return datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


@bp.route("/annonce/<int:listing_id>/creneaux", methods=["GET", "POST"])
@login_required
def manage(listing_id):
    l = _own_listing(listing_id)
    now = now_tn()
    if request.method == "POST":
        day = request.form.get("date", "")
        start, end = _parse(day, request.form.get("start")), _parse(day, request.form.get("end"))
        duration = request.form.get("duration", type=int) or 30
        if duration not in DURATIONS:
            duration = 30
        future = [s for s in l.slots if s.start_at > now]
        if not start or not end:
            flash("Choisis une date, une heure de début et une heure de fin.", "error")
        elif start <= now:
            flash("Choisis une date et une heure à venir.", "error")
        elif start > now + timedelta(days=MAX_DAYS_AHEAD):
            flash(f"Tu peux proposer des créneaux jusqu'à {MAX_DAYS_AHEAD} jours à l'avance.", "error")
        elif end <= start:
            flash("L'heure de fin doit être après l'heure de début.", "error")
        else:
            existing = {s.start_at for s in l.slots}
            created, t = 0, start
            while t + timedelta(minutes=duration) <= end and created < MAX_PER_BATCH:
                if len(future) + created >= MAX_FUTURE_SLOTS:
                    break
                if t not in existing:
                    db.session.add(VisitSlot(listing_id=l.id, start_at=t, duration_min=duration))
                    created += 1
                t += timedelta(minutes=duration)
            db.session.commit()
            if created:
                flash(f"{created} créneau{'x' if created > 1 else ''} ajouté{'s' if created > 1 else ''}. "
                      "Les locataires peuvent maintenant réserver une visite.", "success")
            else:
                flash("Aucun créneau ajouté : la plage est trop courte ou ces créneaux existent déjà.", "error")
        return redirect(url_for("visits.manage", listing_id=l.id))

    upcoming = [s for s in l.slots if s.end_at > now]
    days = {}
    for s in upcoming:
        days.setdefault(s.start_at.date(), []).append(s)
    return render_template("visits/manage.html", listing=l, days=sorted(days.items()), durations=DURATIONS,
                           today=now.date().isoformat(), max_day=(now + timedelta(days=MAX_DAYS_AHEAD)).date().isoformat())


@bp.route("/creneau/<int:slot_id>/supprimer", methods=["POST"])
@login_required
def delete_slot(slot_id):
    s = db.get_or_404(VisitSlot, slot_id)
    l = _own_listing(s.listing_id)
    if s.booked_by_id:
        _cancel_booking(s, by_owner=True)
    db.session.delete(s)
    db.session.commit()
    flash("Créneau supprimé.", "success")
    return redirect(url_for("visits.manage", listing_id=l.id))


@bp.route("/creneau/<int:slot_id>/reserver", methods=["POST"])
@login_required
def book(slot_id):
    s = db.get_or_404(VisitSlot, slot_id)
    l = s.listing
    back = redirect(url_for("main.listing_detail", listing_id=l.id) + "#visite")
    if needs_email_verification(current_user):
        flash("Confirme d'abord ton adresse e-mail pour réserver une visite.", "error")
        return redirect(url_for("auth.verify_pending"))
    if l.owner_id == current_user.id:
        flash("C'est ta propre annonce.", "info")
        return back
    if not l.is_active:
        flash("Cette annonce n'est plus disponible.", "error")
        return back
    if s.booked_by_id or s.start_at <= now_tn():
        flash("Ce créneau vient d'être pris ou n'est plus disponible. Choisis-en un autre.", "error")
        return back
    mine = [x for x in l.slots if x.booked_by_id == current_user.id and x.start_at > now_tn()]
    if mine:
        flash(f"Tu as déjà une visite prévue {format_slot(mine[0].start_at)}. Annule-la pour en choisir une autre.", "info")
        return back
    uid = current_user.id
    if limited("contact_user", uid) or limited("contact_user_day", uid):
        flash("Tu as fait beaucoup de demandes en peu de temps. Réessaie un peu plus tard.", "error")
        return back
    hit("contact_user", uid); hit("contact_user_day", uid)
    s.booked_by_id, s.booked_at = uid, datetime.utcnow()
    when = format_slot(s.start_at)
    # La demande de visite en attente éventuelle devient la visite confirmée
    req = (ContactRequest.query.filter_by(listing_id=l.id, sender_id=uid, kind="visite")
           .filter(ContactRequest.status.in_(["nouveau", "lu"])).first())
    if req:
        req.status, req.slot_id = "accepte", s.id
    else:
        db.session.add(ContactRequest(listing_id=l.id, sender_id=uid, kind="visite", status="accepte", slot_id=s.id,
                                      body=f"Visite réservée {when}."))
    conv = get_or_create_conversation(l, current_user)
    post_message(conv, None, f"Visite réservée {when} par {current_user.full_name}.", kind="systeme")
    notify(l.owner_id, "visite_prevue", f"{current_user.full_name} a réservé une visite",
           f"« {l.title} » – {when}", url_for("messages.thread", conv_id=conv.id))
    db.session.commit()
    flash(f"Visite réservée {when}. Le propriétaire a été prévenu ; vous pouvez échanger dans la messagerie.", "success")
    return redirect(url_for("messages.thread", conv_id=conv.id))


def _cancel_booking(s, by_owner):
    l, tenant = s.listing, s.booked_by
    when = format_slot(s.start_at)
    conv = get_or_create_conversation(l, tenant)
    req = ContactRequest.query.filter_by(slot_id=s.id).first()
    if req:
        req.status, req.slot_id = "annule", None
    if by_owner:
        post_message(conv, None, f"Le propriétaire a annulé la visite {when}.", kind="systeme")
        notify(tenant.id, "refuse", "Ta visite a été annulée par le propriétaire",
               f"« {l.title} » – {when}. Tu peux choisir un autre créneau.",
               url_for("main.listing_detail", listing_id=l.id))
    else:
        post_message(conv, None, f"{tenant.full_name} a annulé la visite {when}.", kind="systeme")
        notify(l.owner_id, "refuse", f"{tenant.full_name} a annulé sa visite", f"« {l.title} » – {when}",
               url_for("visits.manage", listing_id=l.id))
    s.booked_by_id, s.booked_at = None, None


@bp.route("/creneau/<int:slot_id>/annuler", methods=["POST"])
@login_required
def cancel(slot_id):
    s = db.get_or_404(VisitSlot, slot_id)
    if not s.booked_by_id or current_user.id not in (s.booked_by_id, s.listing.owner_id):
        abort(403)
    by_owner = current_user.id == s.listing.owner_id
    _cancel_booking(s, by_owner=by_owner)
    db.session.commit()
    flash("Visite annulée. L'autre personne a été prévenue.", "success")
    if by_owner:
        return redirect(url_for("visits.manage", listing_id=s.listing_id))
    return redirect(request.form.get("next") if (request.form.get("next") or "").startswith("/") else url_for("visits.mine"))


@bp.route("/")
@login_required
def mine():
    """Mes visites : à venir et passées, côté locataire et côté propriétaire."""
    now = now_tn()
    as_tenant = VisitSlot.query.filter_by(booked_by_id=current_user.id).order_by(VisitSlot.start_at).all()
    as_owner = (VisitSlot.query.join(Listing).filter(Listing.owner_id == current_user.id,
                                                     VisitSlot.booked_by_id.isnot(None))
                .order_by(VisitSlot.start_at).all())
    from app.reviews import can_review
    past_tenant = [s for s in as_tenant if s.start_at <= now][-10:][::-1]
    # Visites acceptées sans créneau (organisées par message) : elles permettent aussi de donner un avis
    accepted = (ContactRequest.query.filter_by(sender_id=current_user.id, kind="visite", status="accepte")
                .filter(ContactRequest.slot_id.is_(None)).order_by(ContactRequest.created_at.desc()).limit(10).all())
    return render_template("visits/mine.html",
                           upcoming_tenant=[s for s in as_tenant if s.start_at > now],
                           upcoming_owner=[s for s in as_owner if s.start_at > now],
                           past_tenant=past_tenant, accepted=accepted, can_review=can_review)
