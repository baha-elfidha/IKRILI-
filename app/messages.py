"""Messagerie : conversations entre locataires et propriétaires."""
from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import ContactRequest, Conversation, Message, Notification, notify
from app.utils import format_slot, now_tn
from app.security import hit, limited, needs_email_verification

bp = Blueprint("messages", __name__, url_prefix="/messages")

MAX_LEN = 2000


def get_or_create_conversation(listing, tenant):
    conv = Conversation.query.filter_by(listing_id=listing.id, tenant_id=tenant.id).first()
    if not conv:
        conv = Conversation(listing_id=listing.id, tenant_id=tenant.id, owner_id=listing.owner_id)
        db.session.add(conv)
        db.session.flush()
    return conv


def post_message(conv, sender, body, kind="texte", notify_title=None):
    notif_kind = "visite" if kind == "visite" else "message"
    """Ajoute un message, met la conversation à jour et prévient le destinataire.

    sender=None : message automatique d'IKRILI (prévient les deux participants si notify_title est donné).
    """
    now = datetime.utcnow()
    msg = Message(conversation_id=conv.id, sender_id=sender.id if sender else None, kind=kind, body=body[:MAX_LEN],
                  created_at=now)
    db.session.add(msg)
    conv.updated_at = now
    if sender:
        conv.mark_read(sender)
        recipient = conv.other(sender)
        url = url_for("messages.thread", conv_id=conv.id)
        # Une seule notification non lue par conversation : pas de spam pendant un échange rapide
        pending = Notification.query.filter_by(user_id=recipient.id, is_read=False, url=url).filter(
            Notification.kind.in_(["message", "visite"])).first()
        if pending:
            pending.created_at, pending.body = now, f"« {conv.listing.title} » : {body[:200]}"
            pending.title = notify_title or f"Nouveaux messages de {sender.full_name}"
            pending.kind = notif_kind if notif_kind == "visite" else pending.kind
        else:
            notify(recipient.id, notif_kind, notify_title or f"{sender.full_name} t'a envoyé un message",
                   f"« {conv.listing.title} » : {body[:200]}", url)
    return msg


def _conversation_or_404(conv_id):
    conv = db.get_or_404(Conversation, conv_id)
    if not conv.is_member(current_user) and not current_user.is_admin:
        abort(404)
    return conv


@bp.route("/")
@login_required
def inbox():
    convs = (Conversation.query
             .filter(db.or_(Conversation.tenant_id == current_user.id, Conversation.owner_id == current_user.id))
             .order_by(Conversation.updated_at.desc()).all())
    rows = [(c, c.last_message, c.unread_for(current_user)) for c in convs]
    return render_template("messages/inbox.html", rows=[r for r in rows if r[1]])


@bp.route("/<int:conv_id>", methods=["GET", "POST"])
@login_required
def thread(conv_id):
    conv = _conversation_or_404(conv_id)
    member = conv.is_member(current_user)
    if request.method == "POST":
        if not member:
            abort(403)
        if needs_email_verification(current_user):
            flash("Confirme d'abord ton adresse e-mail pour envoyer des messages.", "error")
            return redirect(url_for("auth.verify_pending"))
        body = request.form.get("body", "").strip()
        uid = current_user.id
        new_account = current_user.account_age_hours < 24
        if not body:
            flash("Écris un message avant d'envoyer.", "error")
        elif limited("chat_user", uid) or (new_account and limited("chat_user_new", uid)):
            flash("Tu envoies beaucoup de messages en peu de temps. Patiente quelques minutes.", "error")
        elif conv.other(current_user).is_blocked:
            flash("Ce compte a été suspendu : il ne peut plus recevoir de messages.", "error")
        else:
            hit("chat_user", uid)
            if new_account:
                hit("chat_user_new", uid)
            post_message(conv, current_user, body)
            db.session.commit()
            if request.headers.get("X-Requested-With"):
                return jsonify(ok=True)
        return redirect(url_for("messages.thread", conv_id=conv.id) + "#bas")

    if member:
        conv.mark_read(current_user)
        Notification.query.filter_by(user_id=current_user.id, is_read=False,
                                     url=url_for("messages.thread", conv_id=conv.id)).update({"is_read": True})
        db.session.commit()
    msgs = conv.messages.all()
    pending = (ContactRequest.query.filter_by(listing_id=conv.listing_id, sender_id=conv.tenant_id, kind="visite")
               .filter(ContactRequest.status.in_(["nouveau", "lu"])).all())
    booked = [s for s in conv.listing.slots if s.booked_by_id == conv.tenant_id and s.start_at > now_tn()]
    return render_template("messages/thread.html", conv=conv, msgs=msgs, member=member,
                           other=conv.other(current_user) if member else conv.owner,
                           last_id=msgs[-1].id if msgs else 0, pending=pending, booked=booked,
                           is_owner=current_user.id == conv.owner_id)


@bp.route("/<int:conv_id>/nouveaux")
@login_required
def new_messages(conv_id):
    """Messages arrivés depuis `apres` (mise à jour automatique du fil, toutes les 8 secondes)."""
    conv = _conversation_or_404(conv_id)
    after = request.args.get("apres", type=int) or 0
    msgs = conv.messages.filter(Message.id > after).all()
    if msgs and conv.is_member(current_user):
        conv.mark_read(current_user)
        Notification.query.filter_by(user_id=current_user.id, is_read=False,
                                     url=url_for("messages.thread", conv_id=conv.id)).update({"is_read": True})
        db.session.commit()
    html = render_template("messages/_bubbles.html", msgs=msgs, conv=conv)
    return jsonify(html=html, last_id=msgs[-1].id if msgs else after)


@bp.route("/compte")
@login_required
def count():
    from app.models import unread_messages_count
    return jsonify(unread=unread_messages_count(current_user))
