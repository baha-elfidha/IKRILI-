"""Estimation du « juste prix » d'un loyer.

Deux modèles complémentaires, entraînés sur les annonces d'IKRILI elles-mêmes :

1. k plus proches voisins (kNN) : on cherche les logements les plus comparables
   (distance géographique, nombre de chambres, surface, type, meublé…) et on prend
   la médiane pondérée de leurs loyers. Fonctionne dès une poignée d'annonces.
2. Régression ridge sur le log du loyer (numpy, forme fermée) : capte l'effet
   moyen de chaque caractéristique. Utilisée seulement à partir de 30 annonces.

L'estimation finale combine les deux (moyenne géométrique) quand c'est possible.
La qualité est mesurée par validation « leave-one-out » (erreur absolue médiane).
"""
import math
import time
from dataclasses import dataclass, field

import numpy as np

from app.utils import haversine_km

K = 8                 # nombre de voisins
MIN_COMPARABLES = 4   # en dessous : pas d'estimation (trop peu fiable)
MAX_DISTANCE = 4.0    # au-delà, un logement n'est plus « comparable »
RIDGE_MIN = 30        # annonces nécessaires pour entraîner la régression
RIDGE_LAMBDA = 1.0
CACHE_TTL = 600       # secondes
TYPES = ["appartement", "maison", "studio", "chambre"]
AMENITY_FIELDS = ["has_internet", "has_ac", "has_heating", "has_parking", "has_washing_machine", "near_transport"]

VERDICTS = {
    "bon_plan": ("Bon plan", "Loyer inférieur aux logements comparables", "bg-okbg text-ok"),
    "moyenne": ("Dans la moyenne du quartier", "Loyer cohérent avec les logements comparables", "bg-brand-soft text-brand-dark"),
    "eleve": ("Au-dessus du marché", "Loyer supérieur aux logements comparables", "bg-pop-soft text-warn"),
}


@dataclass
class Item:
    """Caractéristiques utiles d'un logement (annonce enregistrée ou formulaire en cours)."""
    id: int = 0
    price: float = 0
    lat: float = None
    lng: float = None
    rooms: int = 1
    surface: float = None
    ptype: str = "appartement"
    furnished: bool = False
    coloc: bool = False
    city: str = ""
    governorate: str = ""
    amenities: int = 0
    extra: dict = field(default_factory=dict)


def item_from_listing(l):
    return Item(id=l.id or 0, price=float(l.price_tnd or 0), lat=l.latitude, lng=l.longitude, rooms=l.rooms or 1,
                surface=float(l.surface_m2) if l.surface_m2 else None, ptype=l.property_type or "appartement",
                furnished=bool(l.furnished), coloc=bool(l.is_colocation), city=(l.city or "").strip().lower(),
                governorate=l.governorate or "", amenities=sum(1 for f in AMENITY_FIELDS if getattr(l, f, False)))


def _distance(a, b):
    """« Distance » entre deux logements : 0 = identiques. Pondérations choisies à la main, lisibles."""
    if a.lat is not None and b.lat is not None:
        geo = haversine_km(a.lat, a.lng, b.lat, b.lng) / 2.0          # 2 km = 1 point
    elif a.city and a.city == b.city:
        geo = 0.5
    elif a.governorate and a.governorate == b.governorate:
        geo = 1.5
    else:
        geo = 4.0
    d = geo + 0.7 * abs((a.rooms or 1) - (b.rooms or 1))
    d += 0.0 if a.ptype == b.ptype else 1.0
    d += 0.0 if a.furnished == b.furnished else 0.6
    if a.surface and b.surface:
        d += 1.5 * abs(math.log(a.surface / b.surface))
    return d


def _weighted_quantile(values, weights, q):
    order = np.argsort(values)
    v, w = np.asarray(values, float)[order], np.asarray(weights, float)[order]
    cw = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), cw, v))


def _knn(target, pool):
    cands = [(d, it) for it in pool if it.id != target.id and it.coloc == target.coloc and it.price > 0
             for d in [_distance(target, it)] if d <= MAX_DISTANCE]
    cands.sort(key=lambda x: x[0])
    cands = cands[:K]
    if len(cands) < MIN_COMPARABLES:
        return None
    prices = [it.price for _, it in cands]
    weights = [1.0 / (0.3 + d) for d, _ in cands]
    return {"estimate": _weighted_quantile(prices, weights, 0.5), "low": _weighted_quantile(prices, weights, 0.25),
            "high": _weighted_quantile(prices, weights, 0.75), "n": len(cands),
            "comparables": [it.id for _, it in cands]}


# ------------------------------------------------------------------ régression ridge
def _features(it, governorates):
    x = [1.0, it.rooms or 1, math.log(it.surface) if it.surface else 0.0, 1.0 if not it.surface else 0.0,
         float(it.furnished), float(it.coloc), float(it.amenities)]
    x += [1.0 if it.ptype == t else 0.0 for t in TYPES[1:]]
    x += [1.0 if it.governorate == g else 0.0 for g in governorates[1:]]
    return x


def _fit_ridge(pool):
    data = [it for it in pool if it.price > 0]
    if len(data) < RIDGE_MIN:
        return None
    govs = sorted({it.governorate for it in data})
    X = np.array([_features(it, govs) for it in data])
    y = np.log([it.price for it in data])
    reg = RIDGE_LAMBDA * np.eye(X.shape[1]); reg[0, 0] = 0  # on ne pénalise pas la constante
    beta = np.linalg.solve(X.T @ X + reg, X.T @ y)
    return {"beta": beta, "govs": govs}


def _ridge_predict(model, it):
    if not model:
        return None
    return float(math.exp(np.dot(_features(it, model["govs"]), model["beta"])))


# ------------------------------------------------------------------ cache et API
_cache = {"at": 0, "pool": None, "ridge": None, "quality": None}


def invalidate():
    _cache["at"] = 0


def _load():
    if _cache["pool"] is not None and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["pool"], _cache["ridge"]
    from app.models import Listing
    # Les annonces masquées par le propriétaire (« louées ») restent de bonnes références de prix ;
    # celles masquées par la modération sont exclues.
    rows = Listing.query.filter(Listing.admin_hidden.is_(None)).all()
    pool = [item_from_listing(l) for l in rows]
    _cache.update(at=time.time(), pool=pool, ridge=_fit_ridge(pool), quality=None)
    return pool, _cache["ridge"]


def estimate(target):
    """Renvoie un dict {estimate, low, high, n, method} ou None si pas assez de données."""
    pool, ridge = _load()
    knn = _knn(target, pool)
    if not knn:
        return None
    est = knn["estimate"]
    method = "knn"
    rp = _ridge_predict(ridge, target)
    if rp:
        est, method = math.sqrt(est * rp), "knn+ridge"
    spread = max(knn["high"] - knn["low"], 0.08 * est)
    low, high = est - spread / 2, est + spread / 2
    r = lambda v: int(round(v / 10.0) * 10)  # arrondi à 10 TND
    return {"estimate": r(est), "low": r(low), "high": r(high), "n": knn["n"], "method": method,
            "comparables": knn["comparables"]}


def verdict(price, est):
    if not est or not price:
        return None
    ratio = price / est["estimate"]
    key = "bon_plan" if ratio <= 0.90 else ("eleve" if ratio >= 1.12 else "moyenne")
    label, hint, css = VERDICTS[key]
    return {"key": key, "label": label, "hint": hint, "css": css, "ratio": ratio,
            "pct": int(round((ratio - 1) * 100))}


def for_listing(listing):
    est = estimate(item_from_listing(listing))
    return est, verdict(listing.price_tnd, est)


def quality():
    """Erreur absolue médiane (en %) en « leave-one-out » sur les annonces : indicateur de fiabilité."""
    pool, ridge = _load()
    if _cache["quality"] is not None:
        return _cache["quality"]
    errs = []
    for it in pool:
        k = _knn(it, pool)
        if k and it.price:
            errs.append(abs(k["estimate"] - it.price) / it.price)
    _cache["quality"] = {"mdape": round(float(np.median(errs)) * 100, 1) if errs else None, "n": len(errs),
                         "total": len(pool), "ridge": ridge is not None}
    return _cache["quality"]
