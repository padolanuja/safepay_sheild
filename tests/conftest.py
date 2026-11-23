import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    from safepay import seed
    path = tmp_path_factory.mktemp("db") / "test.db"
    counts = seed.build(path)
    return path, counts


@pytest.fixture()
def client(seeded_db):
    from app import create_app
    app = create_app(db_path=seeded_db[0])
    app.config["TESTING"] = True
    return app.test_client()
