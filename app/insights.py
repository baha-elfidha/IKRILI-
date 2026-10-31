"""Données : estimation du prix, statistiques des propriétaires, tableau de bord du marché."""
import statistics
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, abort, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app import csrf, db
from app.models import ContactRequest, Conversation, Listing, ListingView, University, VisitSlot, favorites
from app import pricing
from app.utils import PROPERTY_TYPES, haversine_km, walking_minutes

bp = Blueprint("insights", __name__)

MIN_GROUP = 3  # on n'affiche pas une moyenne calculée sur moins de 3 annonces
BOTS = ("bot", "spider", "crawl", "slurp", "preview", "facebookexternalhit", "whatsapp", "uptimerobot", "monitor")


# ------------------------------------------------------------------ vues des annonces
def record_view(listing):
    """Compte une vue par visiteur, par annonce et par jour (hors propriétaire et robots)."""
    if current_user.is_authenticated and current_user.id == listing.owner_id:
        return
    ua = (request.user_agent.string or "").lower()
    if not ua or any(b in ua for b in BOTS):
        return
    today = date.today()
    key = f"v{today.isoformat()}"
    seen = session.get(key, [])
    if listing.id in seen:
        return
    session[key] = (seen + [listing.id])[-200:]
    for k in [k for k in session.keys() if k.startswith("v20") and k != key]:
        session.pop(k, None)
    row = ListingView.query.filter_by(listing_id=listing.id, day=today).first()
    if row:
        row.views = ListingView.views + 1
    else:
        db.session.add(ListingView(listing_id=listing.id, day=today, views=1))
    try:
        db.session.commit()
    except Exception:  # deux visites simultanées le même jour : on retente une fois
        db.session.rollback()
        ListingView.query.filter_by(listing_id=listing.id, day=today).update({"views": ListingView.views + 1})
        db.session.commit()


def views_by_day(listing_ids, days=30):
    start = date.today() - timedelta(days=days - 1)
    rows = (db.session.query(ListingView.day, db.func.sum(ListingView.views))
            .filter(ListingView.listing_id.in_(listing_ids), ListingView.day >= start)
            .group_by(ListingView.day).all())
    by = {d: int(n) for d, n in rows}
    return [(start + timedelta(days=i), by.get(start + timedelta(days=i), 0)) for i in range(days)]


def total_views(listing_id, days=None):
    q = db.session.query(db.func.coalesce(db.func.sum(ListingView.views), 0)).filter(ListingView.listing_id == listing_id)
    if days:
        q = q.filter(ListingView.day >= date.today() - timedelta(days=days - 1))
    return int(q.scalar() or 0)


# ------------------------------------------------------------------ estimation (formulaire de publication)
@bp.route("/prix/estimation", methods=["POST"])
@csrf.exempt  # lecture seule, aucune donnée enregistrée
def price_estimate():
    f = request.form
    lat, lng = f.get("latitude", type=float), f.get("longitude", type=float)
    item = pricing.Item(id=f.get("listing_id", type=int) or 0, lat=lat, lng=lng, rooms=f.get("rooms", type=int) or 1,
                        surface=f.get("surface_m2", type=float), ptype=f.get("property_type") or "appartement",
                        furnished=bool(f.get("furnished")), coloc=bool(f.get("is_colocation")),
                        city=(f.get("city") or "").strip().lower(), governorate=f.get("governorate") or "",
                        amenities=sum(1 for a in pricing.AMENITY_FIELDS if f.get(a)))
    est = pricing.estimate(item)
    if not est:
        return jsonify(ok=False)
    v = pricing.verdict(f.get("price_tnd", type=float), est)
    return jsonify(ok=True, estimate=est["estimate"], low=est["low"], high=est["high"], n=est["n"],
                   verdict=v["label"] if v else None, verdict_key=v["key"] if v else None)


# ------------------------------------------------------------------ statistiques propriétaire
@bp.route("/proprietaire/annonce/<int:listing_id>/statistiques")
@login_required
def listing_stats(listing_id):
    l = db.get_or_404(Listing, listing_id)
    if l.owner_id != current_user.id and not current_user.is_admin:
        abort(403)
    series = views_by_day([l.id], 30)
    views30 = sum(n for _, n in series)
    views_all = total_views(l.id)
    favs = db.session.query(db.func.count()).select_from(favorites).filter(favorites.c.listing_id == l.id).scalar()
    convs = Conversation.query.filter_by(listing_id=l.id).count()
    visits = ContactRequest.query.filter_by(listing_id=l.id, kind="visite").count()
    booked = VisitSlot.query.filter(VisitSlot.listing_id == l.id, VisitSlot.booked_by_id.isnot(None)).count()

    # Comparaison avec des annonces similaires (même type, même gouvernorat, colocation ou non)
    similar = (Listing.query.filter(Listing.id != l.id, Listing.property_type == l.property_type,
                                    Listing.governorate == l.governorate, Listing.is_colocation == l.is_colocation,
                                    Listing.admin_hidden.is_(None)).all())
    sim_views = [total_views(s.id, 30) for s in similar if s.is_active]
    sim_prices = [s.price_tnd for s in similar]
    compare = {
        "n": len(similar),
        "views_median": int(statistics.median(sim_views)) if len(sim_views) >= MIN_GROUP else None,
        "price_median": int(statistics.median(sim_prices)) if len(sim_prices) >= MIN_GROUP else None,
        "photos_median": statistics.median([len(s.photos) for s in similar]) if len(similar) >= MIN_GROUP else None,
    }
    est, verdict = pricing.for_listing(l)
    tips = []
    if len(l.photos) < 4:
        tips.append("Ajoute au moins 4 photos lumineuses (séjour, chambres, cuisine, salle de bain).")
    if not l.future_slots():
        tips.append("Propose des créneaux de visite : les locataires réservent sans attendre ta réponse.")
    if verdict and verdict["key"] == "eleve":
        tips.append(f"Ton loyer est environ {verdict['pct']} % au-dessus des logements comparables "
                    f"(estimation {est['low']}–{est['high']} TND).")
    if len(l.description or "") < 200:
        tips.append("Détaille la description : quartier, transports, état, conditions (caution, durée).")
    if not l.surface_m2:
        tips.append("Indique la surface : elle améliore l'estimation du prix et rassure les locataires.")
    if views30 >= 30 and convs == 0:
        tips.append("Beaucoup de vues mais aucun message : vérifie le prix et la qualité des photos.")
    return render_template("insights/listing_stats.html", listing=l, series=series, views30=views30,
                           views_all=views_all, favs=favs, convs=convs, visits=visits, booked=booked,
                           compare=compare, est=est, verdict=verdict, tips=tips,
                           max_day=max([n for _, n in series] + [1]))


# ------------------------------------------------------------------ tableau de bord du marché
def _stats(prices):
    return {"n": len(prices), "median": int(statistics.median(prices)), "avg": int(sum(prices) / len(prices)),
            "min": int(min(prices)), "max": int(max(prices))}


@bp.route("/marche")
def market():
    ptype = request.args.get("type", "")
    coloc = request.args.get("coloc") == "1"
    q = Listing.query.filter(Listing.admin_hidden.is_(None), Listing.is_colocation.is_(coloc))
    if ptype in dict(PROPERTY_TYPES):
        q = q.filter(Listing.property_type == ptype)
    rows = q.all()
    active = [l for l in rows if l.is_active]
    prices = [l.price_tnd for l in rows]

    def group(key_fn):
        g = defaultdict(list)
        for l in rows:
            k = key_fn(l)
            if k:
                g[k].append(l.price_tnd)
        out = [(k, _stats(v)) for k, v in g.items() if len(v) >= MIN_GROUP]
        return sorted(out, key=lambda x: -x[1]["median"])

    by_city = group(lambda l: f"{l.city.strip().title()}, {l.governorate}" if l.city else None)
    by_typology = sorted(group(lambda l: (l.typology or "").upper() or None), key=lambda x: x[0])
    by_gov = group(lambda l: l.governorate)

    by_uni = []
    located = [l for l in rows if l.latitude is not None]
    for u in University.query.order_by(University.name).all():
        near = [l.price_tnd for l in located
                if walking_minutes(haversine_km(u.latitude, u.longitude, l.latitude, l.longitude)) <= 20]
        if len(near) >= MIN_GROUP:
            by_uni.append((u, _stats(near)))
    by_uni.sort(key=lambda x: -x[1]["median"])

    per_m2 = [l.price_tnd / l.surface_m2 for l in rows if l.surface_m2]
    # Histogramme des loyers par tranches de 200 TND
    hist = []
    if prices:
        top = min(max(prices), 3000)
        step = 200 if top > 800 else 100
        edges = list(range(0, int(top) + step, step))
        for a in edges:
            n = sum(1 for p in prices if a <= p < a + step or (a == edges[-1] and p >= a))
            hist.append((a, a + step, n))
        while hist and hist[-1][2] == 0:
            hist.pop()
        while hist and hist[0][2] == 0:
            hist.pop(0)

    return render_template(
        "insights/market.html", total=len(rows), active=len(active),
        overall=_stats(prices) if len(prices) >= MIN_GROUP else None,
        per_m2=int(statistics.median(per_m2)) if len(per_m2) >= MIN_GROUP else None,
        by_city=by_city[:12], by_uni=by_uni, by_typology=by_typology, by_gov=by_gov, hist=hist,
        hist_max=max([h[2] for h in hist] + [1]), quality=pricing.quality(), property_types=PROPERTY_TYPES,
        ptype=ptype, coloc=coloc, min_group=MIN_GROUP,
        bar_max=max([s["median"] for _, s in by_city + by_uni] + [1]))
