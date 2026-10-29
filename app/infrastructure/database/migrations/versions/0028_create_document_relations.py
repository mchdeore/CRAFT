"""document_relations: a read-only view naming the relations the frozen library rows already hold.

Each row ties a document (item, and version where it matters) to a target: its own document
(version_of), a cube value citing it, a facet text naming it as a source, or its mission tag.
No data is added; similar documents (stored vectors) are computed on read, not listed here.
"""

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW document_relations AS
            SELECT 'version_of'::text AS relation, d.item, d.version,
                   'document'::text AS target_kind, d.item AS target
                FROM doc_versions d
            UNION ALL
            SELECT 'cited_by_value', v.item, v.version, 'value', v.id::text
                FROM cube_values v
            UNION ALL
            SELECT 'facet_source', s.item, NULL, 'facet', f.id::text
                FROM facet_texts f CROSS JOIN LATERAL unnest(f.sources) AS s(item)
            UNION ALL
            SELECT 'same_mission', p.item, NULL, 'mission', p.mission_key
                FROM provider_items p WHERE p.mission_key IS NOT NULL;
        GRANT SELECT ON document_relations TO craft_app;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW document_relations")
