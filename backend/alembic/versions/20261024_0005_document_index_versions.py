"""Version document contents, configurations and corpus-specific indexes.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "file_name", "sha256"),
    )
    op.create_index(
        "ix_document_versions_project_id", "document_versions", ["project_id"]
    )
    op.create_table(
        "index_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("collection_name", sa.String(255), nullable=False, unique=True),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("documents_sha256", sa.String(64), nullable=False),
        sa.Column("document_chunks", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("chunks_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "fingerprint"),
    )
    op.create_index("ix_index_versions_project_id", "index_versions", ["project_id"])
    op.create_table(
        "configuration_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "sha256"),
    )
    op.create_index(
        "ix_configuration_versions_project_id", "configuration_versions", ["project_id"]
    )
    with op.batch_alter_table("data_sources") as batch:
        batch.add_column(
            sa.Column("document_version_id", UUID(as_uuid=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch.create_foreign_key(
            "fk_sources_document_version",
            "document_versions",
            ["document_version_id"],
            ["id"],
        )
    with op.batch_alter_table("execution_runs") as batch:
        batch.add_column(
            sa.Column("configuration_version_id", UUID(as_uuid=True), nullable=True)
        )
        batch.add_column(
            sa.Column("index_version_id", UUID(as_uuid=True), nullable=True)
        )
        batch.create_foreign_key(
            "fk_runs_configuration_version",
            "configuration_versions",
            ["configuration_version_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_runs_index_version", "index_versions", ["index_version_id"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("execution_runs") as batch:
        batch.drop_constraint("fk_runs_index_version", type_="foreignkey")
        batch.drop_constraint("fk_runs_configuration_version", type_="foreignkey")
        batch.drop_column("index_version_id")
        batch.drop_column("configuration_version_id")
    with op.batch_alter_table("data_sources") as batch:
        batch.drop_constraint("fk_sources_document_version", type_="foreignkey")
        batch.drop_column("document_version_id")
        batch.drop_column("is_active")
    op.drop_index(
        "ix_configuration_versions_project_id", table_name="configuration_versions"
    )
    op.drop_table("configuration_versions")
    op.drop_index("ix_index_versions_project_id", table_name="index_versions")
    op.drop_table("index_versions")
    op.drop_index("ix_document_versions_project_id", table_name="document_versions")
    op.drop_table("document_versions")
