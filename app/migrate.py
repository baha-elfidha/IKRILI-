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
        ("agency_status", "VARCHAR(20)", None),
        ("agency_name", "VARCHAR(120)", None),
        ("agency_city", "VARCHAR(120)", None),
        ("agency_tax_id", "VARCHAR(40)", None),
        ("agency_bio", "TEXT", None),
        ("agency_requested_at", "TIMESTAMP", None),
    ],
    "listing": [("admin_hidden", "VARCHAR(20)", None), ("featured_until", "TIMESTAMP", None)],
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
        # Les conversations entre colocataires n'ont pas d'annonce : la colonne doit accepter NULL
        if db.engine.dialect.name == "sqlite" and "conversation" in tables:
            _sqlite_conversation_nullable(conn)
        if db.engine.dialect.name == "postgresql" and "conversation" in tables:
            col = next((c for c in insp.get_columns("conversation") if c["name"] == "listing_id"), None)
            if col and not col["nullable"]:
                conn.execute(text("ALTER TABLE conversation ALTER COLUMN listing_id DROP NOT NULL"))


def _sqlite_conversation_nullable(conn):
    """SQLite ne sait pas retirer un NOT NULL : on reconstruit la table (base locale de développement)."""
    cols = conn.execute(text("PRAGMA table_info(conversation)")).fetchall()
    if not any(c[1] == "listing_id" and c[3] for c in cols):
        return
    from app.models import Conversation
    names = ", ".join(c[1] for c in cols)
    conn.execute(text("PRAGMA legacy_alter_table=ON"))  # garde les références de la table message intactes
    for idx in conn.execute(text("PRAGMA index_list(conversation)")).fetchall():
        if idx[3] == "c":
            conn.execute(text(f'DROP INDEX "{idx[1]}"'))
    conn.execute(text("ALTER TABLE conversation RENAME TO conversation_old"))
    Conversation.__table__.create(conn)
    conn.execute(text(f"INSERT INTO conversation ({names}) SELECT {names} FROM conversation_old"))
    conn.execute(text("DROP TABLE conversation_old"))
    conn.execute(text("PRAGMA legacy_alter_table=OFF"))


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
