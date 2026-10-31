"""Trouver un colocataire : profils et score de compatibilité."""
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.messages import post_message
from app.models import Conversation, RoommateProfile, University
from app.security import hit, limited, needs_email_verification
from app.utils import GOVERNORATES

bp = Blueprint("roommates", __name__, url_prefix="/colocation")

RHYTHMS = [("tot", "Lève-tôt"), ("mixte", "Ça dépend"), ("tard", "Couche-tard")]
AMBIANCES = [("calme", "Calme, je travaille beaucoup"), ("equilibre", "Équilibré"), ("sociable", "Sociable, j'aime recevoir")]
TIDINESS = [(1, "Détendu"), (2, "Normal"), (3, "Très ordonné")]
SITUATIONS = [("cherche", "Je cherche un logement à partager"), ("a_logement", "J'ai déjà un logement, je cherche quelqu'un")]
ORDER = {"tot": 0, "mixte": 1, "tard": 2, "calme": 0, "equilibre": 1, "sociable": 2}


def _gender_ok(a, b):
    """Respect mutuel des préférences (colocation filles / garçons)."""
    def accepts(x, y):
        if x.wants == "filles":
            return y.gender == "fille"
        if x.wants == "garcons":
            return y.gender == "garcon"
        return True
    return accepts(a, b) and accepts(b, a)


def compatibility(a, b):
    """Score de 0 à 100 et raisons lisibles. a = moi, b = l'autre profil."""
    score, good, warn = 0.0, [], []
    lo, hi = sorted([a.budget_max or 1, b.budget_max or 1])
    ratio = lo / hi
    score += 25 * min(1.0, ratio / 0.85)
    if ratio >= 0.85:
        good.append("Budgets proches")
    elif ratio < 0.6:
        warn.append("Budgets assez différents")
    if a.university_id and a.university_id == b.university_id:
        score += 25; good.append(f"Même université : {b.university.name}")
    elif a.city and b.city and a.city.strip().lower() == b.city.strip().lower():
        score += 18; good.append(f"Même quartier : {b.city}")
    elif a.governorate and a.governorate == b.governorate:
        score += 10; good.append(f"Même gouvernorat : {b.governorate}")
    elif not (a.university_id or a.city or a.governorate) or not (b.university_id or b.city or b.governorate):
        score += 12
    if (a.smoker and not b.ok_smoker) or (b.smoker and not a.ok_smoker):
        warn.append("Tabac : préférences incompatibles")
    else:
        score += 15
        if not a.smoker and not b.smoker:
            good.append("Non-fumeurs tous les deux")
    for field, weight, label in [("rhythm", 10, "rythme"), ("ambiance", 10, "ambiance")]:
        d = abs(ORDER.get(getattr(a, field), 1) - ORDER.get(getattr(b, field), 1))
        score += weight if d == 0 else (weight * 0.6 if d == 1 else 0)
        if d == 0:
            good.append(f"Même {label}")
        elif d == 2:
            warn.append(f"{label.capitalize()} opposé")
    d = abs((a.tidiness or 2) - (b.tidiness or 2))
    score += 10 if d == 0 else (6 if d == 1 else 0)
    if d == 2:
        warn.append("Rapport au ménage très différent")
    if a.move_in and b.move_in:
        gap = abs((a.move_in - b.move_in).days)
        score += 5 if gap <= 30 else (3 if gap <= 60 else 0)
        if gap <= 30:
            good.append("Dates d'emménagement proches")
    else:
        score += 3
    if a.situation == "a_logement" and b.situation == "a_logement":
        score -= 10
    elif a.situation != b.situation:
        good.append("Un logement disponible" if b.situation == "a_logement" else "Cherche une chambre comme la tienne")
    return max(0, min(100, int(round(score)))), good[:4], warn[:2]


def _fill(p, f):
    errors = []
    p.situation = f.get("situation") if f.get("situation") in dict(SITUATIONS) else "cherche"
    p.budget_max = f.get("budget_max", type=int)
    if not p.budget_max or not 50 <= p.budget_max <= 5000:
        errors.append("Indique ton budget maximum (entre 50 et 5 000 TND par mois).")
    uid = f.get("university_id", type=int)
    p.university_id = uid if uid and db.session.get(University, uid) else None
    p.governorate = f.get("governorate") if f.get("governorate") in GOVERNORATES else None
    p.city = (f.get("city") or "").strip()[:80] or None
    try:
        p.move_in = datetime.strptime(f.get("move_in", ""), "%Y-%m-%d").date() if f.get("move_in") else None
    except ValueError:
        p.move_in = None
    p.gender = f.get("gender") if f.get("gender") in ("fille", "garcon") else None
    p.wants = f.get("wants") if f.get("wants") in ("filles", "garcons", "tous") else "tous"
    if p.wants == "filles" and p.gender != "fille" or p.wants == "garcons" and p.gender != "garcon":
        errors.append("Pour une colocation « filles uniquement » ou « garçons uniquement », précise d'abord si tu es une fille ou un garçon.")
    p.smoker, p.ok_smoker = bool(f.get("smoker")), bool(f.get("ok_smoker")) or bool(f.get("smoker"))
    p.rhythm = f.get("rhythm") if f.get("rhythm") in dict(RHYTHMS) else "mixte"
    p.ambiance = f.get("ambiance") if f.get("ambiance") in dict(AMBIANCES) else "equilibre"
    p.tidiness = f.get("tidiness", type=int) if f.get("tidiness", type=int) in (1, 2, 3) else 2
    p.studies = (f.get("studies") or "").strip()[:120] or None
    p.bio = (f.get("bio") or "").strip()[:600] or None
    p.updated_at = datetime.utcnow()
    return errors


@bp.route("/")
def index():
    me = current_user.roommate_profile if current_user.is_authenticated else None
    if not me:
        n = RoommateProfile.query.filter_by(is_active=True).count()
        return render_template("roommates/landing.html", count=n)
    others = RoommateProfile.query.filter(RoommateProfile.is_active.is_(True), RoommateProfile.user_id != current_user.id).all()
    matches = []
    for p in others:
        if p.user.is_blocked or not _gender_ok(me, p):
            continue
        s, good, warn = compatibility(me, p)
        matches.append((s, p, good, warn))
    matches.sort(key=lambda m: (-m[0], -(m[1].updated_at or m[1].created_at).timestamp()))
    min_score = request.args.get("min", type=int) or 0
    if min_score:
        matches = [m for m in matches if m[0] >= min_score]
    convs = {c.owner_id if c.tenant_id == current_user.id else c.tenant_id: c
             for c in Conversation.query.filter(Conversation.listing_id.is_(None),
                                                db.or_(Conversation.tenant_id == current_user.id,
                                                       Conversation.owner_id == current_user.id)).all()}
    return render_template("roommates/list.html", me=me, matches=matches[:60], convs=convs, min_score=min_score,
                           labels={"rhythm": dict(RHYTHMS), "ambiance": dict(AMBIANCES), "tidiness": dict(TIDINESS)})


@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    p = current_user.roommate_profile
    creating = p is None
    if request.method == "POST":
        p = p or RoommateProfile(user_id=current_user.id)
        errors = _fill(p, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            db.session.rollback()
        else:
            if creating:
                db.session.add(p)
            db.session.commit()
            flash("Profil enregistré ! Voici les personnes les plus compatibles avec toi." if creating
                  else "Profil mis à jour.", "success")
            return redirect(url_for("roommates.index"))
    return render_template("roommates/profile.html", p=p, form=request.form, creating=creating,
                           universities=University.query.order_by(University.name).all(), governorates=GOVERNORATES,
                           rhythms=RHYTHMS, ambiances=AMBIANCES, tidiness=TIDINESS, situations=SITUATIONS)


@bp.route("/profil/<action>", methods=["POST"])
@login_required
def profile_action(action):
    p = current_user.roommate_profile
    if not p:
        abort(404)
    if action == "pause":
        p.is_active = not p.is_active
        flash("Ton profil est de nouveau visible." if p.is_active else "Ton profil est masqué : plus personne ne le voit.", "success")
    elif action == "supprimer":
        db.session.delete(p)
        flash("Profil de colocataire supprimé.", "success")
    else:
        abort(400)
    db.session.commit()
    return redirect(url_for("roommates.index"))


@bp.route("/<int:profile_id>/contacter", methods=["POST"])
@login_required
def contact(profile_id):
    target = db.get_or_404(RoommateProfile, profile_id)
    me = current_user.roommate_profile
    back = redirect(url_for("roommates.index"))
    if not me or not me.is_active:
        flash("Crée (ou réactive) ton profil de colocataire pour contacter quelqu'un.", "error")
        return redirect(url_for("roommates.profile"))
    if needs_email_verification(current_user):
        flash("Confirme d'abord ton adresse e-mail.", "error")
        return redirect(url_for("auth.verify_pending"))
    if target.user_id == current_user.id or not target.is_active or target.user.is_blocked or not _gender_ok(me, target):
        abort(404)
    existing = Conversation.query.filter(Conversation.listing_id.is_(None), db.or_(
        db.and_(Conversation.tenant_id == current_user.id, Conversation.owner_id == target.user_id),
        db.and_(Conversation.tenant_id == target.user_id, Conversation.owner_id == current_user.id))).first()
    if existing:
        return redirect(url_for("messages.thread", conv_id=existing.id))
    body = (request.form.get("body") or "").strip()[:1000]
    if not body:
        flash("Écris un petit message de présentation.", "error")
        return back
    uid = current_user.id
    if limited("contact_user", uid) or limited("contact_user_day", uid) or (
            current_user.account_age_hours < 24 and limited("contact_user_new", uid)):
        flash("Tu as contacté beaucoup de personnes en peu de temps. Réessaie plus tard.", "error")
        return back
    hit("contact_user", uid); hit("contact_user_day", uid)
    if current_user.account_age_hours < 24:
        hit("contact_user_new", uid)
    conv = Conversation(listing_id=None, tenant_id=uid, owner_id=target.user_id)
    db.session.add(conv); db.session.flush()
    post_message(conv, current_user, body, notify_title=f"{current_user.full_name.split()[0]} aimerait être ton·ta colocataire")
    db.session.commit()
    flash("Message envoyé ! La conversation continue dans ta messagerie.", "success")
    return redirect(url_for("messages.thread", conv_id=conv.id))
