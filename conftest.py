import os
import pytest

@pytest.fixture(autouse=True, scope="session")
def isolate_cleo_sessions(tmp_path_factory):
    """Ensure pytest test runs never touch or persist sessions in ~/.cleo/sessions.json."""
    temp_dir = tmp_path_factory.mktemp("cleo_test_sessions")
    temp_file = str(temp_dir / "sessions.json")
    os.environ["CLEO_SESSIONS_FILE"] = temp_file

    import cleo_server
    cleo_server.SESSIONS_CACHE_FILE = temp_file
    cleo_server._sessions = {}
    yield
    os.environ.pop("CLEO_SESSIONS_FILE", None)
