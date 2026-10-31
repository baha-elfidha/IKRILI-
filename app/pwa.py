"""Application installable (PWA) : manifeste, service worker, page hors ligne, page d'installation."""
from flask import Blueprint, current_app, jsonify, make_response, render_template, url_for

bp = Blueprint("pwa", __name__)

SW_VERSION = "ikrili-v1"


@bp.route("/manifest.webmanifest")
def manifest():
    icon = lambda f: url_for("static", filename=f"icons/{f}")
    data = {
        "name": "IKRILI – Logements à louer en Tunisie",
        "short_name": "IKRILI",
        "description": "Appartements, maisons et colocations à louer en Tunisie, pour étudiants et familles.",
        "lang": "fr", "dir": "auto",
        "start_url": url_for("main.index", source="app"),
        "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#FFFFFF", "theme_color": "#2D4BF0",
        "categories": ["lifestyle", "shopping"],
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon("maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": icon("maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Rechercher", "url": url_for("main.search"), "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Messages", "url": url_for("messages.inbox"), "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Publier une annonce", "url": url_for("listings.create"), "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
        ],
    }
    resp = jsonify(data)
    resp.mimetype = "application/manifest+json"
    return resp


@bp.route("/sw.js")
def service_worker():
    js = render_template("pwa/sw.js", version=SW_VERSION, offline=url_for("pwa.offline"),
                         precache=[url_for("pwa.offline"), url_for("static", filename="icons/icon-192.png"),
                                   url_for("static", filename="favicon.svg")])
    resp = make_response(js)
    resp.mimetype = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/hors-ligne")
def offline():
    return render_template("pwa/offline.html")


@bp.route("/application")
def install_page():
    return render_template("pwa/install.html")
