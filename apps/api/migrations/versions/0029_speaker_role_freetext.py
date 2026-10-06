"""0029 — speaker_role libre (permite roles personalizados: juez, apoderado, perito, …).

Quita el CHECK que limitaba speaker_role a los valores en inglés
('judge','attorney','witness','expert','party','clerk','other'), para poder asignar
roles en español y roles nuevos desde el dashboard/chat.
"""
from alembic import op

revision = "0029_speaker_role_freetext"
down_revision = "0028_chat_occurrences"
branch_labels = None
depends_on = None

_OLD = ("('judge','attorney','witness','expert','party','clerk','other')")


def upgrade() -> None:
    op.execute("ALTER TABLE speakers DROP CONSTRAINT IF EXISTS speakers_speaker_role_check")


def downgrade() -> None:
    op.execute("ALTER TABLE speakers ADD CONSTRAINT speakers_speaker_role_check "
               f"CHECK (speaker_role IN {_OLD})")
