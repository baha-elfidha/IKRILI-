"""Mini-migration automatique au démarrage.

db.create_all() crée les nouvelles tables mais n'ajoute pas les nouvelles colonnes
aux tables existantes. Cette fonction ajoute les colonnes manquantes, sans rien
supprimer, sur SQLite comme sur PostgreSQL.
"""
from sqlalchemy import inspect, text

# table -> [(colonne, type SQL, valeur pour les lignes déjà présentes)]
NEW_COLUMNS = {
    "user": [
        # Les comptes créés avant la vérification d'e-mail sont considérés comme vérifiés
        ("email_verified", "BOOLEAN", "TRUE"),
        ("is_blocked", "BOOLEAN", "FALSE"),
        ("blocked_reason", "VARCHAR(200)", None),
    ],
    "listing": [("admin_hidden", "VARCHAR(20)", None)],
    "report": [("status", "VARCHAR(20)", "'nouveau'"), ("handled_at", "TIMESTAMP", None)],
}


def ensure_schema(db):
    insp = inspect(db.engine)
    quote = db.engine.dialect.identifier_preparer.quote
    tables = set(insp.get_table_names())
    with db.engine.begin() as conn:
        for table, columns in NEW_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sqltype, default in columns:
                if name in existing:
                    continue
                ddl = f"ALTER TABLE {quote(table)} ADD COLUMN {quote(name)} {sqltype}"
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))
