from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login_manager

favorites = db.Table(
    "favorites",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id"), primary_key=True),
    db.Column("listing_id", db.Integer, db.ForeignKey("listing.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default="locataire")  # locataire | proprietaire | admin
    is_verified = db.Column(db.Boolean, default=False)       # badge « identité vérifiée » donné par un admin
    email_verified = db.Column(db.Boolean, default=False)    # a cliqué sur le lien reçu par e-mail
    is_blocked = db.Column(db.Boolean, default=False)
    blocked_reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Compte agence immobilière (validé par un administrateur)
    agency_status = db.Column(db.String(20))     # None | en_attente | validee | refusee
    agency_name = db.Column(db.String(120))
    agency_city = db.Column(db.String(120))
    agency_tax_id = db.Column(db.String(40))     # matricule fiscal ou numéro RNE
    agency_bio = db.Column(db.Text)
    agency_requested_at = db.Column(db.DateTime)

    @property
    def is_agency(self):
        return self.agency_status == "validee"

    @property
    def display_name(self):
        return self.agency_name if self.is_agency and self.agency_name else self.full_name

    listings = db.relationship("Listing", backref="owner", lazy=True, cascade="all, delete-orphan")
    favorite_listings = db.relationship("Listing", secondary=favorites, lazy="subquery",
                                        backref=db.backref("favorited_by", lazy=True))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def initials(self):
        parts = self.full_name.split()
        return "".join(p[0] for p in parts[:2]).upper()

    @property
    def owner_rating(self):
        """Note moyenne reçue en tant que propriétaire : (moyenne, nombre) ou (None, 0)."""
        rows = Review.query.filter_by(owner_id=self.id).with_entities(Review.rating_owner).all()
        if not rows:
            return None, 0
        return round(sum(r[0] for r in rows) / len(rows), 1), len(rows)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def account_age_hours(self):
        return (datetime.utcnow() - (self.created_at or datetime.utcnow())).total_seconds() / 3600

    @property
    def is_active(self):  # utilisé par Flask-Login : un compte bloqué ne peut pas rester connecté
        return not self.is_blocked


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class University(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    zone = db.Column(db.String(120))
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)


class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, nullable=False)
    property_type = db.Column(db.String(30), nullable=False)  # appartement | maison | studio | chambre
    typology = db.Column(db.String(10))  # S+0, S+1, S+2...
    rooms = db.Column(db.Integer, default=1)
    surface_m2 = db.Column(db.Integer)
    floor = db.Column(db.String(40))
    price_tnd = db.Column(db.Integer, nullable=False)
    charges_info = db.Column(db.String(200))

    furnished = db.Column(db.Boolean, default=False)
    is_colocation = db.Column(db.Boolean, default=False)
    coloc_places = db.Column(db.Integer)
    girls_only = db.Column(db.Boolean, default=False)
    target = db.Column(db.String(20), default="tous")  # etudiants | familles | tous

    has_internet = db.Column(db.Boolean, default=False)
    has_ac = db.Column(db.Boolean, default=False)
    has_heating = db.Column(db.Boolean, default=False)
    has_parking = db.Column(db.Boolean, default=False)
    has_washing_machine = db.Column(db.Boolean, default=False)
    near_transport = db.Column(db.Boolean, default=False)

    governorate = db.Column(db.String(60), nullable=False, index=True)
    city = db.Column(db.String(80), nullable=False, index=True)  # quartier / délégation
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

    available_from = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True)
    is_verified = db.Column(db.Boolean, default=False)
    # Masquée par un administrateur : le propriétaire ne peut pas la remettre en ligne.
    # Valeurs : None | "moderation" (décision sur l'annonce) | "blocage" (compte bloqué)
    admin_hidden = db.Column(db.String(20))
    featured_until = db.Column(db.DateTime)  # mise en avant payante (« À la une ») jusqu'à cette date

    @property
    def is_featured(self):
        return bool(self.featured_until and self.featured_until > datetime.utcnow() and self.is_active)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    photos = db.relationship("Photo", backref="listing", lazy=True, cascade="all, delete-orphan",
                             order_by="Photo.position")
    requests = db.relationship("ContactRequest", backref="listing", lazy=True, cascade="all, delete-orphan")
    reports = db.relationship("Report", backref="listing", lazy=True, cascade="all, delete-orphan")

    AMENITIES = [
        ("has_internet", "Internet / fibre"),
        ("has_ac", "Climatisation"),
        ("has_heating", "Chauffage"),
        ("has_parking", "Parking"),
        ("has_washing_machine", "Machine à laver"),
        ("near_transport", "Proche transport"),
    ]

    @property
    def amenities(self):
        return [label for field, label in self.AMENITIES if getattr(self, field)]

    @property
    def rating(self):
        """(moyenne du logement, nombre d'avis) ou (None, 0)."""
        if not self.reviews:
            return None, 0
        return round(sum(r.rating_listing for r in self.reviews) / len(self.reviews), 1), len(self.reviews)

    def future_slots(self):
        from app.utils import now_tn
        now = now_tn()
        return [s for s in self.slots if s.start_at > now and not s.booked_by_id]

    @property
    def cover(self):
        return self.photos[0] if self.photos else None

    @property
    def tags(self):
        tags = []
        if self.typology:
            tags.append(self.typology)
        tags.append("Meublé" if self.furnished else "Non meublé")
        if self.is_colocation:
            tags.append("Colocation")
        if self.girls_only:
            tags.append("Filles uniquement")
        return tags


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)  # nom local ou "cld:<public_id>"
    position = db.Column(db.Integer, default=0)

    @property
    def url(self):
        from app.utils import image_url
        return image_url(self.filename, "large")

    @property
    def thumb(self):
        from app.utils import image_url
        return image_url(self.filename, "thumb")

    @property
    def abs_url(self):
        from app.utils import image_url
        return image_url(self.filename, "large", external=True)


class ContactRequest(db.Model):
    """Message ou demande de visite envoyée par un locataire au propriétaire."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    kind = db.Column(db.String(20), default="message")  # message | visite
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="nouveau")  # nouveau | lu | accepte | refuse | annule
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    slot_id = db.Column(db.Integer, db.ForeignKey("visit_slot.id", ondelete="SET NULL"))  # créneau réservé

    sender = db.relationship("User")
    slot = db.relationship("VisitSlot")


class Notification(db.Model):
    """Notification affichée dans le site (cloche du menu)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)  # visite | message | accepte | refuse | favori | signalement | bienvenue
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(300))
    url = db.Column(db.String(300))  # pas de clé étrangère vers l'annonce : elle peut être supprimée
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    STYLES = {  # couleur de la pastille, icône (chemins SVG)
        "visite": ("bg-brand-soft text-brand", '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>'),
        "message": ("bg-brand-soft text-brand", '<path d="M4 5h16v11H8l-4 4z"/><path d="M8 10h8"/>'),
        "accepte": ("bg-okbg text-ok", '<path d="M5 12l5 5L20 7"/>'),
        "refuse": ("bg-slate-100 text-slate-700", '<path d="M6 6l12 12M18 6L6 18"/>'),
        "favori": ("bg-pop-soft text-[#C2364B]", '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'),
        "signalement": ("bg-pop-soft text-warn", '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17v.5"/>'),
        "alerte": ("bg-sun-soft text-ink", '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'),
        "visite_prevue": ("bg-okbg text-ok", '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/><path d="M9 15l2 2 4-4"/>'),
        "avis": ("bg-sun-soft text-ink", '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>'),
        "promo": ("bg-sun-soft text-ink", '<path d="M12 2l2.4 6.9H22l-6 4.4 2.3 7L12 16l-6.3 4.3 2.3-7-6-4.4h7.6z"/>'),
        "bienvenue": ("bg-sun-soft text-ink", '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>'),
    }

    @property
    def style(self):
        return self.STYLES.get(self.kind, self.STYLES["message"])


def notify(user_id, kind, title, body=None, url=None):
    """Ajoute une notification à la session ; l'appelant fait le commit."""
    if user_id:
        db.session.add(Notification(user_id=user_id, kind=kind, title=title[:160],
                                    body=(body[:297] + "…") if body and len(body) > 300 else body, url=url))


class Report(db.Model):
    """Signalement d'une annonce suspecte (arnaque, fausse photo...)."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason = db.Column(db.String(60), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="nouveau")  # nouveau | traite | rejete
    handled_at = db.Column(db.DateTime)

    reporter = db.relationship("User")


class RateEvent(db.Model):
    """Trace des actions sensibles, pour limiter les abus (partagée entre les workers)."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# ---------------------------------------------------------------------------
# Messagerie
# ---------------------------------------------------------------------------
class Conversation(db.Model):
    """Fil de discussion entre un locataire et le propriétaire, à propos d'une annonce."""
    __table_args__ = (db.UniqueConstraint("listing_id", "tenant_id", name="uq_conversation_listing_tenant"),)
    id = db.Column(db.Integer, primary_key=True)
    # listing_id vide = conversation entre futurs colocataires (tenant = celui qui écrit, owner = l'autre)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=True, index=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    tenant_read_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_read_at = db.Column(db.DateTime)

    listing = db.relationship("Listing", backref=db.backref("conversations", cascade="all, delete-orphan", lazy=True))
    tenant = db.relationship("User", foreign_keys=[tenant_id])
    owner = db.relationship("User", foreign_keys=[owner_id])
    messages = db.relationship("Message", backref="conversation", lazy="dynamic", cascade="all, delete-orphan",
                               order_by="Message.created_at")

    def other(self, user):
        return self.owner if user.id == self.tenant_id else self.tenant

    @property
    def subject(self):
        return self.listing.title if self.listing else "Recherche de colocation"

    def is_member(self, user):
        return user.is_authenticated and user.id in (self.tenant_id, self.owner_id)

    def read_at(self, user):
        return self.tenant_read_at if user.id == self.tenant_id else self.owner_read_at

    def mark_read(self, user):
        if user.id == self.tenant_id:
            self.tenant_read_at = datetime.utcnow()
        elif user.id == self.owner_id:
            self.owner_read_at = datetime.utcnow()

    def unread_for(self, user):
        since = self.read_at(user)
        q = self.messages.filter(Message.sender_id != user.id)
        return q.filter(Message.created_at > since).count() if since else q.count()

    @property
    def last_message(self):
        # Requête séparée : la relation `messages` est déjà triée par ordre croissant
        return Message.query.filter_by(conversation_id=self.id).order_by(Message.created_at.desc(), Message.id.desc()).first()


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # None = message automatique d'IKRILI
    kind = db.Column(db.String(20), default="texte")  # texte | visite | systeme
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    sender = db.relationship("User")


def unread_messages_count(user):
    """Nombre de messages non lus, toutes conversations confondues."""
    convs = Conversation.query.filter(db.or_(Conversation.tenant_id == user.id, Conversation.owner_id == user.id)).all()
    return sum(c.unread_for(user) for c in convs if not c.read_at(user) or c.updated_at > c.read_at(user))


# ---------------------------------------------------------------------------
# Alertes de recherche
# ---------------------------------------------------------------------------
class SavedSearch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    params = db.Column(db.Text, nullable=False)  # critères au format JSON
    label = db.Column(db.String(300))
    by_email = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_match_at = db.Column(db.DateTime)
    match_count = db.Column(db.Integer, default=0)

    user = db.relationship("User", backref=db.backref("saved_searches", cascade="all, delete-orphan", lazy=True))

    @property
    def criteria(self):
        import json
        try:
            return json.loads(self.params)
        except ValueError:
            return {}


# ---------------------------------------------------------------------------
# Visites
# ---------------------------------------------------------------------------
class VisitSlot(db.Model):
    """Créneau de visite proposé par le propriétaire (heure de Tunis)."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False, index=True)
    start_at = db.Column(db.DateTime, nullable=False, index=True)
    duration_min = db.Column(db.Integer, default=30)
    booked_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    booked_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    listing = db.relationship("Listing", backref=db.backref("slots", cascade="all, delete-orphan", lazy=True,
                                                            order_by="VisitSlot.start_at"))
    booked_by = db.relationship("User")

    @property
    def end_at(self):
        from datetime import timedelta
        return self.start_at + timedelta(minutes=self.duration_min or 30)


# ---------------------------------------------------------------------------
# Avis
# ---------------------------------------------------------------------------
class Review(db.Model):
    __table_args__ = (db.UniqueConstraint("listing_id", "author_id", name="uq_review_listing_author"),)
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating_listing = db.Column(db.Integer, nullable=False)  # 1 à 5
    rating_owner = db.Column(db.Integer, nullable=False)    # 1 à 5
    comment = db.Column(db.Text)
    owner_reply = db.Column(db.Text)
    replied_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    listing = db.relationship("Listing", backref=db.backref("reviews", cascade="all, delete-orphan", lazy=True,
                                                            order_by="Review.created_at.desc()"))
    author = db.relationship("User", foreign_keys=[author_id])
    owner = db.relationship("User", foreign_keys=[owner_id])


# ---------------------------------------------------------------------------
# Statistiques
# ---------------------------------------------------------------------------
class ListingView(db.Model):
    """Nombre de vues d'une annonce par jour (une vue par visiteur et par jour)."""
    __table_args__ = (db.UniqueConstraint("listing_id", "day", name="uq_listing_view_day"),)
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False, index=True)
    views = db.Column(db.Integer, default=0, nullable=False)

    listing = db.relationship("Listing", backref=db.backref("view_rows", cascade="all, delete-orphan", lazy=True))


# ---------------------------------------------------------------------------
# Colocation : profils et mise en relation
# ---------------------------------------------------------------------------
class RoommateProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    situation = db.Column(db.String(20), default="cherche")   # cherche (un logement à plusieurs) | a_logement (a une chambre libre)
    budget_max = db.Column(db.Integer, nullable=False)
    university_id = db.Column(db.Integer, db.ForeignKey("university.id"))
    governorate = db.Column(db.String(60))
    city = db.Column(db.String(80))
    move_in = db.Column(db.Date)
    gender = db.Column(db.String(10))          # fille | garcon | None (non précisé)
    wants = db.Column(db.String(10), default="tous")  # filles | garcons | tous
    smoker = db.Column(db.Boolean, default=False)
    ok_smoker = db.Column(db.Boolean, default=False)
    rhythm = db.Column(db.String(10), default="mixte")       # tot | tard | mixte
    tidiness = db.Column(db.Integer, default=2)              # 1 détendu · 2 normal · 3 très ordonné
    ambiance = db.Column(db.String(10), default="equilibre")  # calme | equilibre | sociable
    studies = db.Column(db.String(120))
    bio = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("roommate_profile", uselist=False, cascade="all, delete-orphan"))
    university = db.relationship("University")


class Boost(db.Model):
    """Demande de mise en avant d'une annonce, payée hors ligne (D17, virement) et activée par un admin."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    days = db.Column(db.Integer, nullable=False)
    price_tnd = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(20), unique=True, nullable=False)
    status = db.Column(db.String(20), default="en_attente")  # en_attente | active | refusee | annulee
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    note = db.Column(db.String(200))

    listing = db.relationship("Listing", backref=db.backref("boosts", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User")

    @property
    def state(self):
        if self.status == "active" and self.ends_at and self.ends_at <= datetime.utcnow():
            return "expiree"
        return self.status
