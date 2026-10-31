# IKRILI

Application web pour trouver un logement à louer en Tunisie : appartements, maisons, studios et colocations, pour les étudiants comme pour les familles.

Stack : **Flask · PostgreSQL · SQLAlchemy · Tailwind (CDN) · Leaflet / OpenStreetMap**.

## Fonctionnalités

- Comptes locataire et propriétaire (inscription, connexion, mots de passe hachés).
- Publication, modification, masquage (« louée ») et suppression d'annonces, avec photos et position sur une carte.
- Recherche : ville ou quartier, gouvernorat, type, budget, chambres, meublé, colocation (dont filles uniquement), équipements.
- Filtre **« à moins de X minutes à pied de mon université »** et tri par proximité.
- Fiche annonce : galerie, équipements, universités proches, carte avec zone approximative (l'adresse exacte n'est jamais affichée).
- Demande de visite et messages au propriétaire ; espace propriétaire pour accepter ou refuser les visites.
- Favoris, signalement d'annonces suspectes, page de conseils anti-arnaque.
- Mot de passe oublié (lien par e-mail, valable 1 h, à usage unique) et vérification de l'adresse e-mail.
- Espace administrateur (`/admin`) : signalements, masquage d'annonces, blocage de comptes, badge « vérifié ».
- Anti-abus : limites de tentatives de connexion, d'inscriptions, de messages et de signalements ; téléphone des propriétaires visible seulement pour les comptes de plus de 24 h.
- Protection CSRF sur tous les formulaires, cookies sécurisés en production.

## Lancer en local

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # facultatif : sans DATABASE_URL, SQLite est utilisé
flask --app wsgi seed              # universités + 4 annonces de démo
flask --app wsgi run --debug
```

Ouvre http://127.0.0.1:5000. Compte de démo : `demo@ikrili.tn` / `demo12345`.

Pour utiliser PostgreSQL en local, crée une base `ikrili` et renseigne `DATABASE_URL` dans `.env`.

## Mettre en ligne sur Render

1. Crée un dépôt GitHub et pousse ce dossier.
2. Sur [render.com](https://render.com), choisis **New → Blueprint** et sélectionne le dépôt. Render lit `render.yaml` et crée la base PostgreSQL et le service web.
3. Au premier démarrage, la commande `seed --no-demo` ajoute les universités, sans annonces de démo.
4. Ton site est accessible sur `https://ikrili.onrender.com` (ou un nom proche). Ajoute ensuite ton domaine (`ikrili.tn`) dans **Settings → Custom Domains**.

Pour te donner les droits d'administrateur, ouvre le **Shell** du service sur Render et lance :

```bash
flask --app wsgi make-admin ton-email@exemple.com
```

### E-mails (Brevo)

Crée un compte gratuit sur brevo.com, valide l'adresse d'expéditeur, puis ajoute dans Render → Environment :
`MAIL_USERNAME` et `MAIL_PASSWORD` (Brevo → SMTP & API → onglet SMTP) et `MAIL_FROM` (ex. `IKRILI <contact@ikrili.tn>`).
Tant que ces variables sont absentes, les e-mails sont seulement écrits dans les journaux et la vérification d'e-mail n'est pas exigée.

### À savoir avant le lancement public

- **Photos** : ajoute la variable `CLOUDINARY_URL` (compte gratuit sur cloudinary.com) dans Render → Environment. Sans elle, les photos sont stockées sur le disque du serveur et **effacées à chaque redéploiement**.
- **Plan gratuit** : il met le site en veille après inactivité et la base gratuite est limitée dans le temps. Vérifie les conditions actuelles de Render et passe sur un plan payant pour la production.
- **Tailwind via CDN** : pratique pour démarrer ; pour la production, compile Tailwind (CLI) pour un CSS plus léger.
- **Coordonnées des universités** : elles sont approximatives dans `app/cli.py`, vérifie-les sur une carte.
- **Données personnelles** : prévois des conditions d'utilisation, une politique de confidentialité et renseigne-toi sur la déclaration auprès de l'INPDP.

## Structure

```
config.py            configuration (base, clé secrète, uploads)
wsgi.py              point d'entrée (gunicorn wsgi:app)
app/__init__.py      création de l'application
app/models.py        User, University, Listing, Photo, ContactRequest, Report
app/main.py          accueil, recherche, fiche, contact, favoris, signalement
app/listings.py      espace propriétaire (publier, modifier, demandes reçues)
app/auth.py          inscription, connexion
app/cli.py           commandes « seed » et « make-admin »
app/templates/       pages HTML
```

## Prochaines étapes possibles

- Validation des annonces et des signalements par un administrateur.
- Vérification d'identité des propriétaires (pièce d'identité, téléphone par SMS).
- Messagerie en conversation (fil de discussion) et notifications par e-mail.
- Recherche géographique avec PostGIS quand le nombre d'annonces augmente.
- Version arabe et application mobile.
