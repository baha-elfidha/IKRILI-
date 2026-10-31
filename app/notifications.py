from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from app import db
from app.models import Notification

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

KEEP_DAYS = 90  # les notifications lues plus anciennes sont supprimées automatiquement


def unread_count(user):
    return Notification.query.filter_by(user_id=user.id, is_read=False).count()


def time_ago(dt):
    s = int((datetime.utcnow() - dt).total_seconds())
    if s < 60:
        return "à l'instant"
    if s < 3600:
        return f"il y a {s // 60} min"
    if s < 86400:
        return f"il y a {s // 3600} h"
    if s < 7 * 86400:
        d = s // 86400
        return "hier" if d == 1 else f"il y a {d} jours"
    return dt.strftime("%d/%m/%Y")


@bp.route("/")
@login_required
def index():
    Notification.query.filter(Notification.user_id == current_user.id, Notification.is_read.is_(True),
                              Notification.created_at < datetime.utcnow() - timedelta(days=KEEP_DAYS)).delete()
    db.session.commit()
    items = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc()).limit(100).all())
    return render_template("notifications.html", items=items, time_ago=time_ago)


@bp.route("/<int:notif_id>")
@login_required
def open(notif_id):
    """Marque la notification comme lue puis redirige vers sa page."""
    n = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first_or_404()
    if not n.is_read:
        n.is_read = True
        db.session.commit()
    return redirect(n.url or url_for("notifications.index"))


@bp.route("/tout-lu", methods=["POST"])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("notifications.index"))


@bp.route("/compte")
@login_required
def count():
    """Utilisé par la cloche pour se mettre à jour sans recharger la page."""
    return jsonify(unread=unread_count(current_user))
