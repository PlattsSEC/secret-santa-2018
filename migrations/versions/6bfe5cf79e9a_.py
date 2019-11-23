"""empty message

Revision ID: 6bfe5cf79e9a
Revises: 0889451f6279
Create Date: 2019-11-23 00:39:15.909429

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6bfe5cf79e9a'
down_revision = '0889451f6279'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('participants', sa.Column('user_id', sa.Integer(), nullable=True))
    op.create_unique_constraint(None, 'participants', ['user_id'])
    op.create_foreign_key(None, 'participants', 'users', ['user_id'], ['id'])
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'participants', type_='foreignkey')
    op.drop_constraint(None, 'participants', type_='unique')
    op.drop_column('participants', 'user_id')
    # ### end Alembic commands ###