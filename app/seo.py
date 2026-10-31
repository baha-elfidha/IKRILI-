"""Référencement Google : pages par université et par quartier, sitemap.xml, robots.txt."""
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, url_for

from app import db
from app.models import Listing, University
from app.utils import haversine_km, walking_minutes

bp = Blueprint("seo", __name__)

UNI_RADIUS_MIN = 25   # annonces affichées sur la page d'une université : à 25 min à pied ou moins
MIN_FOR_STATS = 3     # pas de loyer médian calculé sur moins de 3 annonces


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def uni_slug(u):
    return slugify(u.name)


def area_slug(city, governorate):
    city_s, gov_s = slugify(city), slugify(governorate)
    return city_s if city_s == gov_s else f"{city_s}-{gov_s}"


def _active():
    return Listing.query.filter(Listing.is_active.is_(True))


def _stats(listings):
    prices = [l.price_tnd for l in listings]
    coloc = [l.price_tnd for l in listings if l.is_colocation]
    whole = [l.price_tnd for l in listings if not l.is_colocation]
    return {
        "n": len(listings),
        "median": int(statistics.median(whole)) if len(whole) >= MIN_FOR_STATS else None,
        "coloc_median": int(statistics.median(coloc)) if len(coloc) >= MIN_FOR_STATS else None,
        "min": min(prices) if prices else None,
        "furnished": sum(1 for l in listings if l.furnished),
        "coloc": len(coloc),
    }


def areas():
    """Quartiers ayant au moins une annonce en ligne : [(slug, ville, gouvernorat, nombre)]."""
    groups = defaultdict(int)
    names = {}
    for city, gov in db.session.query(Listing.city, Listing.governorate).filter(Listing.is_active.is_(True)).all():
        if not city:
            continue
        key = area_slug(city.strip(), gov)
        groups[key] += 1
        names.setdefault(key, (city.strip().title(), gov))
    return sorted(((k, names[k][0], names[k][1], n) for k, n in groups.items()), key=lambda x: (-x[3], x[1]))


def find_university(slug):
    return next((u for u in University.query.all() if uni_slug(u) == slug), None)


def near_university(u, listings, radius=UNI_RADIUS_MIN):
    out = []
    for l in listings:
        if l.latitude is None:
            continue
        m = walking_minutes(haversine_km(u.latitude, u.longitude, l.latitude, l.longitude))
        if m <= radius:
            out.append((m, l))
    out.sort(key=lambda x: (x[0], -x[1].id))
    return out


# ------------------------------------------------------------------ universités
@bp.route("/logement-etudiant/")
def universities():
    listings = _active().all()
    rows = [(u, uni_slug(u), len(near_university(u, listings))) for u in University.query.order_by(University.name)]
    return render_template("seo/universities.html", rows=rows)


@bp.route("/logement-etudiant/<slug>")
def university(slug):
    u = find_university(slug)
    if not u:
        abort(404)
    listings = _active().all()
    near = near_university(u, listings)
    near_listings = [l for _, l in near]
    stats = _stats(near_listings)
    # Quartiers les plus proches de l'université parmi ceux qui ont des annonces
    area_counts = defaultdict(int)
    for l in near_listings:
        area_counts[(l.city.strip().title(), l.governorate)] += 1
    nearby_areas = [(area_slug(c, g), c, g, n) for (c, g), n in sorted(area_counts.items(), key=lambda x: -x[1])][:8]
    others = [(o, uni_slug(o)) for o in University.query.order_by(University.name) if o.id != u.id]
    faq = _faq_university(u, stats)
    return render_template("seo/university.html", u=u, slug=slug, near=near[:24], stats=stats,
                           nearby_areas=nearby_areas, others=others, faq=faq, radius=UNI_RADIUS_MIN)


def _faq_university(u, s):
    faq = []
    if s["median"]:
        faq.append((f"Combien coûte un logement près de {u.name} ?",
                    f"Sur IKRILI, le loyer médian d'un logement entier à {UNI_RADIUS_MIN} minutes à pied ou moins de "
                    f"{u.name} est d'environ {s['median']} TND par mois, d'après {s['n']} annonces en ligne."))
    if s["coloc_median"]:
        faq.append((f"Combien coûte une chambre en colocation près de {u.name} ?",
                    f"Une chambre en colocation coûte environ {s['coloc_median']} TND par mois autour de {u.name}."))
    faq.append((f"Comment trouver un colocataire près de {u.name} ?",
                f"Crée ton profil de colocataire sur IKRILI en indiquant {u.name} : tu verras les étudiants "
                f"compatibles (budget, rythme de vie, tabac) avec un score, et tu pourras leur écrire."))
    faq.append(("Comment éviter les arnaques au logement ?",
                "Ne verse jamais d'argent avant d'avoir visité le logement et signé un contrat. Sur IKRILI, "
                "réserve une visite et échange dans la messagerie sans donner ton numéro."))
    return faq


# ------------------------------------------------------------------ quartiers
@bp.route("/location/")
def areas_index():
    by_gov = defaultdict(list)
    for row in areas():
        by_gov[row[2]].append(row)
    return render_template("seo/areas.html", by_gov=sorted(by_gov.items()))


@bp.route("/location/<slug>")
def area(slug):
    match = next((a for a in areas() if a[0] == slug), None)
    if not match:
        abort(404)
    _, city, gov, _n = match
    listings = [l for l in _active().filter(Listing.governorate == gov).order_by(Listing.created_at.desc()).all()
                if area_slug(l.city.strip(), l.governorate) == slug]
    stats = _stats(listings)
    by_typo = defaultdict(list)
    for l in listings:
        if l.typology and not l.is_colocation:
            by_typo[l.typology.upper()].append(l.price_tnd)
    typologies = sorted((t, int(statistics.median(p)), len(p)) for t, p in by_typo.items() if len(p) >= MIN_FOR_STATS)
    # Universités à proximité (centre approximatif du quartier)
    located = [l for l in listings if l.latitude is not None]
    unis = []
    if located:
        lat = sum(l.latitude for l in located) / len(located)
        lng = sum(l.longitude for l in located) / len(located)
        for u in University.query.all():
            m = walking_minutes(haversine_km(u.latitude, u.longitude, lat, lng))
            if m <= 45:
                unis.append((m, u, uni_slug(u)))
        unis.sort(key=lambda x: x[0])
    siblings = [a for a in areas() if a[2] == gov and a[0] != slug][:10]
    faq = []
    if stats["median"]:
        faq.append((f"Quel est le prix d'une location à {city} ?",
                    f"Sur IKRILI, le loyer médian d'un logement entier à {city} ({gov}) est d'environ "
                    f"{stats['median']} TND par mois, d'après {stats['n']} annonces en ligne."))
    faq.append((f"Y a-t-il des logements meublés à {city} ?",
                f"{stats['furnished']} annonce{'s' if stats['furnished'] != 1 else ''} meublée"
                f"{'s' if stats['furnished'] != 1 else ''} à {city} en ce moment sur IKRILI."))
    return render_template("seo/area.html", slug=slug, city=city, gov=gov, listings=listings[:24], stats=stats,
                           typologies=typologies, unis=unis[:4], siblings=siblings, faq=faq)


# ------------------------------------------------------------------ robots.txt et sitemap.xml
@bp.route("/robots.txt")
def robots():
    lines = ["User-agent: *", "Allow: /",
             "Disallow: /proprietaire/", "Disallow: /admin", "Disallow: /messages", "Disallow: /notifications",
             "Disallow: /favoris", "Disallow: /alertes", "Disallow: /visites", "Disallow: /colocation/profil",
             "Disallow: /prix/estimation", "Disallow: /*/contact", "Disallow: /*/signaler",
             f"Sitemap: {url_for('seo.sitemap', _external=True)}"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    ext = lambda endpoint, **kw: url_for(endpoint, _external=True, **kw)
    today = datetime.utcnow().date().isoformat()
    urls = []  # (loc, lastmod, priority, bilingue)
    for ep, prio in [("main.index", "1.0"), ("main.search", "0.8"), ("seo.universities", "0.8"), ("seo.areas_index", "0.8"),
                     ("roommates.index", "0.7"), ("insights.market", "0.7"), ("pwa.install_page", "0.3"),
                     ("main.safety", "0.4")]:
        urls.append((ext(ep), today, prio, True))
    for ep in ("main.terms", "main.privacy"):
        urls.append((ext(ep), None, "0.2", False))
    for u in University.query.order_by(University.name):
        urls.append((ext("seo.university", slug=uni_slug(u)), today, "0.9", True))
    for slug, *_ in areas():
        urls.append((ext("seo.area", slug=slug), today, "0.8", True))
    for l in _active().order_by(Listing.created_at.desc()).limit(5000):
        urls.append((ext("main.listing_detail", listing_id=l.id), l.created_at.date().isoformat(), "0.6", True))

    def entry(loc, lastmod, prio, bilingual):
        parts = [f"<url><loc>{loc}</loc>"]
        if lastmod:
            parts.append(f"<lastmod>{lastmod}</lastmod>")
        parts.append(f"<priority>{prio}</priority>")
        if bilingual:
            sep = "&amp;" if "?" in loc else "?"
            parts.append(f'<xhtml:link rel="alternate" hreflang="fr" href="{loc}"/>')
            parts.append(f'<xhtml:link rel="alternate" hreflang="ar" href="{loc}{sep}lang=ar"/>')
        parts.append("</url>")
        return "".join(parts)

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
           + "\n".join(entry(*u) for u in urls) + "\n</urlset>\n")
    return Response(xml, mimetype="application/xml")


def breadcrumb_ld(items):
    root = request.url_root.rstrip("/")
    out = []
    for i, (label, href) in enumerate(items, 1):
        entry = {"@type": "ListItem", "position": i, "name": label}
        entry["item"] = (root + href) if href and href.startswith("/") else (href or request.base_url)
        out.append(entry)
    return out


def faq_ld(faq):
    return [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]


def listing_ld(l):
    """Données structurées d'une annonce (schema.org)."""
    data = {"@context": "https://schema.org", "@type": "Apartment" if l.property_type in ("appartement", "studio") else
            ("House" if l.property_type in ("maison", "villa") else "Accommodation"),
            "name": l.title, "url": url_for("main.listing_detail", listing_id=l.id, _external=True),
            "address": {"@type": "PostalAddress", "addressLocality": l.city, "addressRegion": l.governorate,
                        "addressCountry": "TN"},
            "numberOfRooms": l.rooms}
    if l.surface_m2:
        data["floorSize"] = {"@type": "QuantitativeValue", "value": l.surface_m2, "unitCode": "MTK"}
    if l.cover:
        data["image"] = l.cover.abs_url
    data["offers"] = {"@type": "Offer", "price": l.price_tnd, "priceCurrency": "TND",
                      "availability": "https://schema.org/InStock" if l.is_active else "https://schema.org/SoldOut",
                      "businessFunction": "http://purl.org/goodrelations/v1#LeaseOut"}
    return {k: v for k, v in data.items() if v is not None}
