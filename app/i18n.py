"""Version arabe des pages publiques.

Principe : les gabarits restent écrits en français. Pour une page publique demandée en arabe,
le HTML produit est traduit à la sortie à l'aide du catalogue `i18n_ar.AR` (phrase française -> phrase arabe).
Les nombres sont remplacés par {0}, {1}… pour qu'une même entrée serve à « 12 annonces » et « 3 annonces ».
Une phrase absente du catalogue reste en français : la page ne casse jamais.
Le contenu écrit par les utilisateurs (titres, descriptions, messages) n'est pas traduit.

Commande utile :  flask i18n-manquants  -> liste les phrases des pages publiques sans traduction.
"""
import html as _html
import re

from flask import g, request

LANGS = ("fr", "ar")
COOKIE = "lang"

# Pages proposées en arabe (le reste du site, espace propriétaire et administration, reste en français)
AR_ENDPOINTS = {
    "main.index", "main.search", "main.listing_detail", "main.safety", "main.favorites", "main.report",
    "roommates.index", "roommates.profile", "insights.market",
    "seo.universities", "seo.university", "seo.areas_index", "seo.area",
    "pwa.install_page", "pwa.offline",
    "auth.login", "auth.register", "auth.forgot_password", "auth.reset_password", "auth.verify_pending",
}

NUM = re.compile(r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?")
TOKEN = re.compile(r"(<!--.*?-->|<[^>]+>)", re.S)
SKIP_TAGS = ("script", "style", "textarea", "pre", "code")
ATTR = re.compile(r'\b(placeholder|aria-label|title|alt|data-label)="([^"]*)"')
META_DESC = re.compile(r'(<meta\s+(?:name="description"|property="og:(?:title|description)")\s+content=")([^"]*)(")')


def current_lang():
    return getattr(g, "lang", "fr")


def page_lang():
    """Langue réellement utilisée pour la page : l'arabe seulement sur les pages publiques traduites."""
    return "ar" if current_lang() == "ar" and request.endpoint in AR_ENDPOINTS else "fr"


def _catalog():
    from app.i18n_ar import AR
    return AR


_ENTITY_CACHE = {"at": 0, "regex": None}


def _entities_regex():
    """Noms propres (universités, zones, gouvernorats, quartiers) remplacés par {n} comme les nombres."""
    import time
    if _ENTITY_CACHE["regex"] is not None and time.time() - _ENTITY_CACHE["at"] < 300:
        return _ENTITY_CACHE["regex"]
    from app import db
    from app.models import Listing, University
    from app.utils import GOVERNORATES
    names = set(GOVERNORATES)
    cacheable = True
    try:
        for u in University.query.all():
            names.update([u.name, u.zone or ""] + [p.strip() for p in (u.zone or "").split(",")])
        for (city,) in db.session.query(Listing.city).distinct().limit(2000):
            if city:
                names.update([city.strip(), city.strip().title()])
    except Exception:  # hors contexte d'application : on ne garde pas ce résultat incomplet
        cacheable = False
    names = sorted((n for n in names if n and len(n) >= 2), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    rx = re.compile(rf"(?<![\wÀ-ÿ])(?:{alt})(?![\wÀ-ÿ])|{NUM.pattern}" if alt else NUM.pattern)
    if cacheable:
        _ENTITY_CACHE.update(at=time.time(), regex=rx)
    return rx


def translate_text(text, catalog=None, missing=None):
    """Traduit une phrase (texte brut, sans HTML). Renvoie None si elle n'est pas dans le catalogue."""
    catalog = catalog if catalog is not None else _catalog()
    key = " ".join(text.split())
    if not key or not re.search(r"[A-Za-zÀ-ÿ]", key):
        return None
    if key in catalog:
        return catalog[key]
    values = []

    def ph(m):
        values.append(m.group(0))
        return "{%d}" % (len(values) - 1)

    tpl = _entities_regex().sub(ph, key)
    if values and tpl in catalog:
        try:
            return catalog[tpl].format(*[catalog.get(v, v) for v in values])
        except (IndexError, KeyError):
            return None
    if missing is not None and tpl.strip("{}0123456789 ,.·–-()") and not re.fullmatch(r"(\{\d+\}[\s,·–()-]*)+", tpl):
        missing.add(tpl if values else key)
    return None


def _tr_segment(seg, catalog, missing):
    core = seg.strip()
    if not core:
        return seg
    plain = _html.unescape(core)
    out = translate_text(plain, catalog, missing)
    if out is None:
        return seg
    lead = seg[: len(seg) - len(seg.lstrip())]
    trail = seg[len(seg.rstrip()):]
    return lead + _html.escape(out, quote=False) + trail


def _tr_attr(m, catalog, missing):
    name, value = m.group(1), m.group(2)
    out = translate_text(_html.unescape(value), catalog, missing)
    return m.group(0) if out is None else f'{name}="{_html.escape(out, quote=True)}"'


def translate_html(page, catalog=None, missing=None):
    catalog = catalog if catalog is not None else _catalog()
    parts = TOKEN.split(page)
    skip = None
    out = []
    for part in parts:
        if part.startswith("<!--"):
            out.append(part)
            continue
        if part.startswith("<"):
            low = part[:12].lower()
            if skip:
                if low.startswith(f"</{skip}"):
                    skip = None
                out.append(part)
                continue
            tag = re.match(r"<\s*([a-zA-Z0-9]+)", part)
            if tag and tag.group(1).lower() in SKIP_TAGS and not part.endswith("/>"):
                skip = tag.group(1).lower()
            if 'translate="no"' in part:
                out.append(part)
                continue
            part = ATTR.sub(lambda m: _tr_attr(m, catalog, missing), part)
            part = META_DESC.sub(lambda m: m.group(1) + _html.escape(
                translate_text(_html.unescape(m.group(2)), catalog, missing) or _html.unescape(m.group(2)), quote=True)
                + m.group(3), part)
            out.append(part)
        elif skip:
            out.append(part)
        else:
            out.append(_tr_segment(part, catalog, missing))
    return "".join(out)


def lang_url(lang):
    """URL de la page courante dans l'autre langue."""
    args = request.args.to_dict(flat=False)
    args.pop("lang", None)
    from urllib.parse import urlencode
    args["lang"] = [lang]
    return request.path + "?" + urlencode(args, doseq=True)


def canonical_url(lang=None):
    from urllib.parse import urlencode
    q = list(request.args.items(multi=True))
    q = [(k, v) for k, v in q if k not in ("lang", "source")]
    if (lang or page_lang()) == "ar":
        q.append(("lang", "ar"))
    base = request.url_root.rstrip("/") + request.path
    return base + ("?" + urlencode(q) if q else "")


def init_app(app):
    @app.before_request
    def _pick_lang():
        asked = request.args.get("lang")
        if asked in LANGS:
            g.lang, g.set_lang = asked, asked
        else:
            cookie = request.cookies.get(COOKIE)
            g.lang = cookie if cookie in LANGS else "fr"

    @app.after_request
    def _translate(resp):
        if getattr(g, "set_lang", None):
            resp.set_cookie(COOKIE, g.set_lang, max_age=365 * 24 * 3600, samesite="Lax",
                            secure=app.config.get("SESSION_COOKIE_SECURE", False))
        if request.endpoint in AR_ENDPOINTS:
            resp.headers.add("Vary", "Cookie")
        if (resp.status_code == 200 and resp.mimetype == "text/html" and not resp.direct_passthrough
                and current_lang() == "ar" and request.endpoint in AR_ENDPOINTS):
            resp.set_data(translate_html(resp.get_data(as_text=True)))
        return resp

    @app.context_processor
    def _ctx():
        lang = page_lang() if request.endpoint else "fr"
        return {"page_lang": lang, "page_dir": "rtl" if lang == "ar" else "ltr", "lang_url": lang_url,
                "canonical_url": canonical_url, "translatable": request.endpoint in AR_ENDPOINTS,
                "ui_lang": current_lang()}

    @app.cli.command("i18n-manquants")
    def missing_cmd():
        """Liste les phrases des pages publiques qui n'ont pas encore de traduction arabe."""
        for key in sorted(collect_missing(app)):
            print(repr(key))


def collect_missing(app, extra_paths=()):
    """Parcourt les pages publiques (en français) et renvoie les phrases absentes du catalogue."""
    from app.models import Listing, University
    from app.seo import areas, uni_slug
    missing = set()
    client = app.test_client()
    with app.app_context():
        paths = ["/", "/recherche", "/recherche?coloc=1", "/colocation/", "/marche", "/logement-etudiant/",
                 "/location/", "/application", "/hors-ligne", "/connexion", "/inscription", "/mot-de-passe-oublie",
                 "/conseils-anti-arnaque", "/page-inexistante"]
        paths += [f"/logement-etudiant/{uni_slug(u)}" for u in University.query.all()[:3]]
        paths += [f"/location/{a[0]}" for a in areas()[:3]]
        paths += [f"/annonce/{l.id}" for l in Listing.query.filter_by(is_active=True).limit(3)]
        paths += list(extra_paths)
    for p in paths:
        r = client.get(p, headers={"User-Agent": "i18n-check"})
        if r.mimetype == "text/html":
            with app.app_context():
                translate_html(r.get_data(as_text=True), missing=missing)
    return missing
