import os
import tempfile

# Must be set before the app is imported: tests always use a throwaway SQLite file.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

# Unit tests never start the real Agent Reach process; tests that need it opt in explicitly.
os.environ["SEARCH_ENGINE"] = "exa"
