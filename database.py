import os
from supabase import create_client

# Supabase connection
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def get_db():
    """Connect to Supabase database."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Supabase credentials are missing. "
            "Please configure SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY."
        )

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def test_connection():
    """Test database connection."""
    db = get_db()

    result = db.table("worker_status").select("*").limit(1).execute()

    return result.data


def read_table(table_name):
    """Read records from a database table."""
    db = get_db()

    result = db.table(table_name).select("*").execute()

    return result.data


def insert_record(table_name, data):
    """Insert a new record."""
    db = get_db()

    result = db.table(table_name).insert(data).execute()

    return result.data


def update_record(table_name, data, match_column, match_value):
    """Update a record using a matching column."""
    db = get_db()

    result = (
        db.table(table_name)
        .update(data)
        .eq(match_column, match_value)
        .execute()
    )

    return result.data
