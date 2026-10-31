from alembic import context

from fleck_api.db import make_engine
from fleck_api.models import Base

# FLECK_DATABASE_URL by default; tests pass -x-style override via config attribute "url".
url = context.config.attributes.get("url")
with make_engine(url).connect() as conn:
    context.configure(connection=conn, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
    conn.commit()
