import os
from datetime import date

import click

from app import db
from app.models import Listing, University, User

UNIVERSITIES = [
    # nom, zone, latitude, longitude (positions approximatives, à vérifier)
    ("ESPRIT", "El Ghazela, Ariana", 36.8992, 10.1897),
    ("Sup'Com", "El Ghazela, Ariana", 36.8917, 10.1872),
    ("FST", "El Manar, Tunis", 36.8381, 10.1471),
    ("ENIT", "El Manar, Tunis", 36.8327, 10.1468),
    ("INSAT", "Centre urbain nord, Tunis", 36.8434, 10.1977),
    ("IHEC Carthage", "Carthage", 36.8592, 10.3283),
]


def register(app):
    @app.cli.command("seed")
    @click.option("--demo/--no-demo", default=True, help="Ajouter des annonces de démonstration.")
    def seed(demo):
        """Ajoute les universités (et des annonces de démo)."""
        if University.query.count() == 0:
            for name, zone, lat, lng in UNIVERSITIES:
                db.session.add(University(name=name, zone=zone, latitude=lat, longitude=lng))
            click.echo("Universités ajoutées.")

        if demo and not User.query.filter_by(email="demo@ikrili.tn").first():
            owner = User(full_name="Propriétaire Démo", email="demo@ikrili.tn",
                         phone="+216 00 000 000", role="proprietaire", is_verified=True)
            owner.set_password("demo12345")
            db.session.add(owner)
            db.session.flush()
            demos = [
                dict(title="S+2 meublé, lumineux, près d'ESPRIT", property_type="appartement", typology="S+2",
                     rooms=2, surface_m2=85, floor="2e étage", price_tnd=850, furnished=True, target="etudiants",
                     governorate="Ariana", city="El Ghazela", latitude=36.8960, longitude=10.1880,
                     has_internet=True, has_ac=True, has_washing_machine=True, near_transport=True,
                     charges_info="Eau et syndic inclus"),
                dict(title="Chambre en colocation, 2 places libres", property_type="chambre", typology="S+3",
                     rooms=1, surface_m2=14, price_tnd=320, furnished=True, is_colocation=True, coloc_places=2,
                     target="etudiants", governorate="Ariana", city="Raoued", latitude=36.9040, longitude=10.1960,
                     has_internet=True, near_transport=True, charges_info="Charges incluses"),
                dict(title="Maison S+3 avec jardin, quartier calme", property_type="maison", typology="S+3",
                     rooms=3, surface_m2=160, price_tnd=1600, furnished=False, target="familles",
                     governorate="Tunis", city="La Marsa", latitude=36.8780, longitude=10.3240,
                     has_parking=True, has_heating=True, has_ac=True),
                dict(title="Studio rénové à deux pas du métro", property_type="studio", typology="S+0",
                     rooms=1, surface_m2=35, floor="3e étage", price_tnd=550, furnished=True, target="tous",
                     governorate="Tunis", city="Lafayette", latitude=36.8150, longitude=10.1800,
                     has_internet=True, has_ac=True, near_transport=True),
            ]
            for d in demos:
                db.session.add(Listing(owner_id=owner.id, is_verified=True, available_from=date.today(),
                                       description=("Logement de démonstration ajouté par la commande "
                                                    "« flask seed ». Supprime-le avant le lancement public."),
                                       **d))
            click.echo("Annonces de démonstration ajoutées (compte demo@ikrili.tn / demo12345).")

        # Variable d'environnement ADMIN_EMAIL : ce compte devient administrateur
        admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        if admin_email:
            admin = User.query.filter_by(email=admin_email).first()
            if admin and admin.role != "admin":
                admin.role = "admin"
                click.echo(f"{admin_email} est maintenant administrateur.")
        db.session.commit()

    @app.cli.command("make-admin")
    @click.argument("email")
    def make_admin(email):
        """Donne le rôle administrateur à un compte existant."""
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            click.echo("Compte introuvable.")
            return
        user.role = "admin"
        db.session.commit()
        click.echo(f"{email} est maintenant administrateur.")
