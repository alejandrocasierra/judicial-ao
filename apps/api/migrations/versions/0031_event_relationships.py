"""0031 — Process Graph: relaciones entre eventos + duplicado/inconsistencias.

- `event_relationships`: grafo procesal (precede/causes/responds_to/appeals/refers_to/...).
- `events.duplicate_of`: un evento referenciado apunta a la actuación real que lo originó.
- `events.review_flags`: marcas del revisor (duplicado, fecha_inconsistente, sin_fecha, sin_fuente_real).
"""
from alembic import op
import os

revision = "0031_event_relationships"
down_revision = "0030_procedural_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS duplicate_of uuid REFERENCES events(id)")
    op.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS review_flags jsonb NOT NULL DEFAULT '[]'")
    op.execute("""
        CREATE TABLE IF NOT EXISTS event_relationships (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          organization_id uuid NOT NULL REFERENCES organizations(id),
          case_id uuid NOT NULL REFERENCES cases(id),
          source_event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          target_event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
          relationship text NOT NULL,
          confidence numeric(4,3),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_event_id, target_event_id, relationship)
        )
    """)
    app_user = os.environ.get("DB_APP_USER")
    if app_user:
        op.execute('GRANT SELECT, INSERT, UPDATE, DELETE ON event_relationships TO "%s"' % app_user)
    op.execute("ALTER TABLE event_relationships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE event_relationships FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON event_relationships")
    op.execute("""CREATE POLICY tenant_isolation ON event_relationships
        USING (organization_id = current_org()) WITH CHECK (organization_id = current_org())""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS event_relationships")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS review_flags")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS duplicate_of")
