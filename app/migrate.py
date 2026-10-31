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
    "contact_request": [("slot_id", "INTEGER", None)],
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


def backfill_conversations(db):
    """Une seule fois : transforme les anciens messages (demandes de contact) en conversations."""
    from app.models import ContactRequest, Conversation, Message
    if Conversation.query.first() or not ContactRequest.query.first():
        return
    convs = {}
    for req in ContactRequest.query.order_by(ContactRequest.created_at).all():
        listing = req.listing
        if listing is None or req.sender_id == listing.owner_id:
            continue
        key = (listing.id, req.sender_id)
        conv = convs.get(key)
        if conv is None:
            conv = Conversation(listing_id=listing.id, tenant_id=req.sender_id, owner_id=listing.owner_id,
                                created_at=req.created_at, updated_at=req.created_at, tenant_read_at=req.created_at,
                                owner_read_at=req.created_at if req.status != "nouveau" else None)
            db.session.add(conv)
            db.session.flush()
            convs[key] = conv
        db.session.add(Message(conversation_id=conv.id, sender_id=req.sender_id, body=req.body, created_at=req.created_at,
                               kind="visite" if req.kind == "visite" else "texte"))
        conv.updated_at = max(conv.updated_at, req.created_at)
    db.session.commit()
