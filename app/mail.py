"""Envoi d'e-mails via SMTP (Brevo par défaut).

Variables d'environnement (Render > Environment) :
  MAIL_USERNAME   identifiant SMTP Brevo (souvent ton adresse de connexion Brevo)
  MAIL_PASSWORD   clé SMTP Brevo (Brevo > SMTP & API > SMTP)
  MAIL_FROM       expéditeur validé dans Brevo, ex. "IKRILI <contact@ikrili.tn>"
  MAIL_SERVER     smtp-relay.brevo.com (par défaut)
  MAIL_PORT       587 (par défaut)

Sans MAIL_USERNAME / MAIL_PASSWORD, les e-mails ne partent pas : leur contenu
est écrit dans les journaux (pratique en local) et la vérification d'e-mail
n'est pas exigée.
"""
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from flask import current_app, render_template


def mail_enabled(app=None):
    cfg = (app or current_app).config
    return bool(cfg.get("MAIL_USERNAME") and cfg.get("MAIL_PASSWORD"))


def _send(app, msg):
    cfg = app.config
    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
    except Exception:
        app.logger.exception("Échec de l'envoi de l'e-mail à %s", msg["To"])


def send_email(to, subject, template, **ctx):
    """Prépare l'e-mail (HTML + texte) et l'envoie en arrière-plan, sans bloquer la page."""
    app = current_app._get_current_object()
    html = render_template(f"email/{template}.html", **ctx)
    txt = render_template(f"email/{template}.txt", **ctx)
    if not mail_enabled(app):
        app.logger.warning("E-mail non envoyé (Brevo non configuré) à %s – %s\n%s", to, subject, txt)
        return False
    name, addr = parseaddr(app.config["MAIL_FROM"])
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((name or "IKRILI", addr))
    msg["To"] = to
    msg.set_content(txt)
    msg.add_alternative(html, subtype="html")
    if app.testing:
        app.extensions.setdefault("sent_mails", []).append(msg)
        return True
    threading.Thread(target=_send, args=(app, msg), daemon=True).start()
    return True
