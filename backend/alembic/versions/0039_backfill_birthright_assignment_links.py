"""One-time data backfill: links pre-existing AccessAssignment rows to the birthright policy that actually
granted them, for assignments created before birthright_policy_id (migration 0038) existed. Without this,
reconcile_birthright_policies_for_user() can never revoke a genuinely birthright-granted assignment made before
today — it looks structurally identical to a manual grant, so it's correctly (but wrongly, for this historical
data) left untouched.

Matches on the exact justification string evaluate_birthright_policies() has always written
("Birthright policy: <policy name>") plus the same resource_type/resource_id/app_role_external_id the policy
targets — that justification string is written nowhere else in the codebase, so this is a precise match, not a
heuristic guess. Known limitation: if a policy was renamed after granting, the old justification text still
carries the old name and won't match here — a one-time historical cleanup, not an ongoing mechanism (every new
grant going forward gets birthright_policy_id set for real, regardless of later renames).

Revision ID: 0039_backfill_birthright_links
Revises: 0038_birthright_assignment_link
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_backfill_birthright_links"
down_revision = "0038_birthright_assignment_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    policies = conn.execute(sa.text("SELECT id, name, resource_type, resource_id, app_role_external_id FROM birthright_policies")).fetchall()
    for policy in policies:
        conn.execute(
            sa.text(
                """
                UPDATE access_assignments
                SET birthright_policy_id = :policy_id
                WHERE birthright_policy_id IS NULL
                  AND resource_type = :resource_type
                  AND resource_id = :resource_id
                  AND app_role_external_id IS NOT DISTINCT FROM :app_role_external_id
                  AND justification = :justification
                """
            ),
            {
                "policy_id": policy.id,
                "resource_type": policy.resource_type,
                "resource_id": policy.resource_id,
                "app_role_external_id": policy.app_role_external_id,
                "justification": f"Birthright policy: {policy.name}",
            },
        )


def downgrade() -> None:
    # Not reversible: there's no way to tell a backfilled link apart from one set by a real grant after this
    # migration ran. Downgrading 0038 already drops the whole column regardless.
    pass
