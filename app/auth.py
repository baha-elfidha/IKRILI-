from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.models import User

bp = Blueprint("auth", __name__)


def _safe_next(target):
    if target and urlparse(target).netloc == "" and target.startswith("/"):
        return target
    return None


@bp.route("/inscription", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = request.form
    if request.method == "POST":
        full_name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        phone = form.get("phone", "").strip()
        password = form.get("password", "")
        role = form.get("role", "locataire")
        errors = []
        if len(full_name) < 3:
            errors.append("Indique ton nom complet.")
        if "@" not in email:
            errors.append("Adresse e-mail invalide.")
        if len(password) < 8:
            errors.append("Le mot de passe doit contenir au moins 8 caractères.")
        if role not in ("locataire", "proprietaire"):
            role = "locataire"
        if not form.get("accept_terms"):
            errors.append("Tu dois accepter les conditions d'utilisation et la politique de confidentialité.")
        if User.query.filter_by(email=email).first():
            errors.append("Un compte existe déjà avec cet e-mail.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            user = User(full_name=full_name, email=email, phone=phone, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Bienvenue sur IKRILI !", "success")
            if role == "proprietaire":
                return redirect(url_for("listings.create"))
            return redirect(url_for("main.index"))
    return render_template("auth/register.html", form=form)


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(_safe_next(request.args.get("next")) or url_for("main.index"))
        flash("E-mail ou mot de passe incorrect.", "error")
    return render_template("auth/login.html")


@bp.route("/deconnexion")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))
