from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.mail import mail_enabled, send_email
from app.models import User, notify
from app.security import (RESET_MAX_AGE, VERIFY_MAX_AGE, client_ip, clear, hit, limited, make_token,
                          read_token)

bp = Blueprint("auth", __name__)

GENERIC_RESET_MSG = ("Si un compte existe avec cette adresse, tu vas recevoir un e-mail avec un lien "
                     "pour choisir un nouveau mot de passe. Pense à regarder dans les spams.")


def _safe_next(target):
    if target and urlparse(target).netloc == "" and target.startswith("/"):
        return target
    return None


def send_verification(user):
    link = url_for("auth.verify_email", token=make_token(user, "verify"), _external=True)
    return send_email(user.email, "Confirme ton adresse e-mail – IKRILI", "verify", user=user, link=link)


# ------------------------------------------------------------------ inscription
@bp.route("/inscription", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = request.form
    if request.method == "POST":
        if limited("register_ip", client_ip()):
            flash("Trop d'inscriptions depuis ta connexion. Réessaie dans une heure.", "error")
            return render_template("auth/register.html", form=form), 429
        full_name = form.get("full_name", "").strip()
        email = form.get("email", "").strip().lower()
        phone = form.get("phone", "").strip()
        password = form.get("password", "")
        role = form.get("role", "locataire")
        errors = []
        if len(full_name) < 3:
            errors.append("Indique ton nom complet.")
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 160:
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
            user = User(full_name=full_name, email=email, phone=phone, role=role,
                        email_verified=not mail_enabled())
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if role == "proprietaire":
                notify(user.id, "bienvenue", "Bienvenue sur IKRILI !",
                       "Publie ta première annonce : c'est gratuit et ça prend 5 minutes.", url_for("listings.create"))
            else:
                notify(user.id, "bienvenue", "Bienvenue sur IKRILI !",
                       "Choisis ta fac dans la recherche pour voir les logements à quelques minutes à pied.",
                       url_for("main.search"))
            db.session.commit()
            hit("register_ip", client_ip())
            login_user(user)
            if not user.email_verified:
                send_verification(user)
                flash(f"Bienvenue sur IKRILI ! Un e-mail de confirmation a été envoyé à {email}.", "success")
                return redirect(url_for("auth.verify_pending"))
            flash("Bienvenue sur IKRILI !", "success")
            if role == "proprietaire":
                return redirect(url_for("listings.create"))
            return redirect(url_for("main.index"))
    return render_template("auth/register.html", form=form)


# ------------------------------------------------------------------ connexion
@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = client_ip()
        if limited("login_ip", ip) or limited("login_email", email):
            flash("Trop de tentatives de connexion. Réessaie dans 15 minutes, ou utilise « Mot de passe oublié ».", "error")
            return render_template("auth/login.html", email=email), 429
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            if user.is_blocked:
                flash("Ce compte a été suspendu. Contacte-nous si tu penses qu'il s'agit d'une erreur.", "error")
                return render_template("auth/login.html", email=email), 403
            clear(f"login_email:{email}")
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(_safe_next(request.args.get("next")) or url_for("main.index"))
        hit("login_ip", ip)
        hit("login_email", email)
        flash("E-mail ou mot de passe incorrect.", "error")
        return render_template("auth/login.html", email=email)
    return render_template("auth/login.html")


@bp.route("/deconnexion")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))


# ------------------------------------------------------------------ mot de passe oublié
@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ip = client_ip()
        # Même message dans tous les cas : on ne révèle pas si un compte existe
        if not limited("reset_email", email) and not limited("reset_ip", ip):
            hit("reset_email", email)
            hit("reset_ip", ip)
            user = User.query.filter_by(email=email).first()
            if user and not user.is_blocked:
                link = url_for("auth.reset_password", token=make_token(user, "reset"), _external=True)
                send_email(user.email, "Réinitialise ton mot de passe – IKRILI", "reset", user=user, link=link)
        flash(GENERIC_RESET_MSG, "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/reinitialiser/<token>", methods=["GET", "POST"])
def reset_password(token):
    data, err = read_token(token, "reset", RESET_MAX_AGE)
    user = db.session.get(User, data["id"]) if data else None
    if not user or user.email != data["email"] or user.password_hash[-12:] != data["pw"]:
        if err == "invalide":
            flash("Ce lien n'est pas valide.", "error")
        else:  # expiré, ou mot de passe déjà changé avec ce lien
            flash("Ce lien a expiré ou a déjà été utilisé. Fais une nouvelle demande.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < 8:
            flash("Le mot de passe doit contenir au moins 8 caractères.", "error")
        elif pw != pw2:
            flash("Les deux mots de passe ne correspondent pas.", "error")
        else:
            user.set_password(pw)
            user.email_verified = True  # il a prouvé qu'il reçoit les e-mails à cette adresse
            db.session.commit()
            clear(f"login_email:{user.email}")
            logout_user()
            flash("Mot de passe modifié. Tu peux te connecter.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", token=token)


# ------------------------------------------------------------------ vérification de l'e-mail
@bp.route("/verifier-email")
@login_required
def verify_pending():
    if current_user.email_verified or not mail_enabled():
        return redirect(url_for("main.index"))
    return render_template("auth/verify_pending.html")


@bp.route("/verifier-email/renvoyer", methods=["POST"])
@login_required
def resend_verification():
    if current_user.email_verified:
        return redirect(url_for("main.index"))
    if limited("verify_user", current_user.id):
        flash("Tu as déjà demandé plusieurs e-mails. Attends un peu et vérifie tes spams.", "error")
    else:
        hit("verify_user", current_user.id)
        send_verification(current_user)
        flash(f"Nouvel e-mail envoyé à {current_user.email}.", "success")
    return redirect(url_for("auth.verify_pending"))


@bp.route("/verifier-email/<token>")
def verify_email(token):
    data, err = read_token(token, "verify", VERIFY_MAX_AGE)
    user = db.session.get(User, data["id"]) if data else None
    if not user or user.email != data["email"]:
        flash("Ce lien a expiré. Connecte-toi et demande un nouvel e-mail de confirmation." if err == "expire"
              else "Ce lien de confirmation n'est pas valide.", "error")
        return redirect(url_for("auth.verify_pending") if current_user.is_authenticated else url_for("auth.login"))
    if not user.email_verified:
        user.email_verified = True
        db.session.commit()
    flash("Adresse e-mail confirmée, merci !", "success")
    if current_user.is_authenticated and current_user.id == user.id:
        return redirect(url_for("listings.create") if user.role == "proprietaire" else url_for("main.search"))
    return redirect(url_for("auth.login"))
