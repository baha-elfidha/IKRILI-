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
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    filename = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, default=0)


class ContactRequest(db.Model):
    """Message ou demande de visite envoyée par un locataire au propriétaire."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    kind = db.Column(db.String(20), default="message")  # message | visite
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="nouveau")  # nouveau | lu | accepte | refuse
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship("User")


class Report(db.Model):
    """Signalement d'une annonce suspecte (arnaque, fausse photo...)."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    reason = db.Column(db.String(60), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
