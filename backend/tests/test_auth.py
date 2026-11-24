"""Inicio de sesión y niveles de acceso: reglas, API, protección y línea de comandos."""

import io
import logging
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import create_app
from app.auth import cli
from app.auth.guard import access_required
from app.auth.passwords import hash_password, password_problem, username_problem, verify_password
from app.auth.roles import ADMIN, EDITOR, VIEWER
from app.auth.service import AuthService, Conflict, InvalidCredentials, InvalidInput, NotFound
from app.auth.store import UserNotFound, UserStore, UsernameTaken
from app.auth.throttle import LoginThrottle
from app.utils.locale import t

PASSWORD = "una-clave-valida"
WRITE_URL = "/api/graph/build"


def csrf_of(client):
    """Token de la sesión actual del cliente."""

    return client.get("/api/auth/session").get_json()["data"]["csrf"]


def login(client, username, password, address=None):
    extra = {"environ_base": {"REMOTE_ADDR": address}} if address else {}
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": csrf_of(client)},
        **extra,
    )


def make_user(app, username, role, password=PASSWORD):
    with app.app_context():
        return app.extensions["auth"].create_user(username, password, role)


def signed(client, username, role, password=PASSWORD):
    """Inicia sesión con una cuenta que existe y devuelve su token."""

    response = login(client, username, password)
    assert response.status_code == 200, response.get_json()
    return {"X-CSRF-Token": response.get_json()["data"]["csrf"]}


@pytest.fixture
def app():
    application = create_app()
    application.config.update(TESTING=True)
    return application


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextmanager
def _capture(name: str):
    """Recoge lo que escribe un registro de la aplicación, que no propaga al raíz."""

    logger = logging.getLogger(name)
    collector = _Collector()
    previous_level = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    try:
        yield collector.lines
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous_level)


@pytest.fixture
def captured_logs():
    """Mensajes del registro de autenticación durante la prueba."""

    with _capture("mirofish.auth") as lines:
        yield lines


def test_password_rules():
    assert password_problem("ana", PASSWORD) is None
    assert password_problem("ana", "corta") == "auth.error.passwordTooShort"
    assert password_problem("ana", "x" * 129) == "auth.error.passwordTooLong"
    assert password_problem("ana.torres", "ANA.TORRES") == "auth.error.passwordEqualsUsername"


def test_password_hash_never_reveals_the_password_even_if_malformed():
    password_hash = hash_password(PASSWORD)

    assert PASSWORD not in password_hash
    assert verify_password(password_hash, PASSWORD)
    assert not verify_password(password_hash, "otra-clave-valida")
    assert not verify_password("no-es-un-hash", PASSWORD)
    assert not verify_password("", PASSWORD)


# ---------------------------------------------------------------- almacén


def test_users_persist_and_never_expose_their_hash(tmp_path):
    path = str(tmp_path / "auth" / "users.db")
    store = UserStore(path)
    created = store.create("Ana", hash_password(PASSWORD), EDITOR, must_change_password=True)

    reopened = UserStore(path)
    found = reopened.find("ANA")

    assert found is not None and found.id == created.id
    assert found.username == "Ana"
    assert found.to_dict() == {
        "id": created.id,
        "username": "Ana",
        "role": "editor",
        "disabled": False,
        "must_change_password": True,
        "created_at": found.created_at,
    }
    assert "password_hash" not in found.to_dict()
    assert found.password_hash not in repr(found)


def test_usernames_are_unique_ignoring_case_and_spaces(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    store.create("Ana", hash_password(PASSWORD), VIEWER, must_change_password=True)

    with pytest.raises(UsernameTaken):
        store.create(" ana ", hash_password(PASSWORD), ADMIN, must_change_password=True)
    with pytest.raises(ValueError):
        store.create("Bea", hash_password(PASSWORD), "jefe", must_change_password=True)


def test_update_refuses_unknown_fields_and_missing_users(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    created = store.create("Ana", hash_password(PASSWORD), VIEWER, must_change_password=True)

    updated = store.update(created.id, role=ADMIN, must_change_password=False)

    assert (updated.role, updated.must_change_password) == (ADMIN, False)
    assert store.active_admins() == 1
    with pytest.raises(ValueError):
        store.update(created.id, username="otro")
    with pytest.raises(ValueError):
        store.update(created.id, role="jefe")
    with pytest.raises(UserNotFound):
        store.update(999, role=ADMIN)
    assert store.active_admins() == 1


# ---------------------------------------------------------------- reglas de negocio


def service_in(tmp_path) -> AuthService:
    return AuthService(UserStore(str(tmp_path / "users.db")))


def test_authenticate_does_not_say_which_credential_is_wrong(tmp_path):
    service = service_in(tmp_path)
    service.create_user("ana", PASSWORD, EDITOR)
    bea = service.create_user("bea", PASSWORD, VIEWER)
    service.set_disabled(bea.id, True)

    assert service.authenticate("ANA", PASSWORD).username == "ana"
    for username, password in (("ana", "mal"), ("nadie", PASSWORD), ("bea", PASSWORD)):
        with pytest.raises(InvalidCredentials) as error:
            service.authenticate(username, password)
        assert error.value.key == "auth.error.invalidCredentials"


def test_create_user_validates_input(tmp_path):
    service = service_in(tmp_path)

    with pytest.raises(InvalidInput):
        service.create_user("ab", PASSWORD, EDITOR)
    with pytest.raises(InvalidInput):
        service.create_user("ana", "corta", EDITOR)
    with pytest.raises(InvalidInput):
        service.create_user("ana", PASSWORD, "jefe")
    service.create_user("ana", PASSWORD, EDITOR)
    with pytest.raises(Conflict):
        service.create_user("ANA", PASSWORD, VIEWER)
    assert service.store.find("ana").must_change_password is True


def test_the_last_administrator_cannot_be_demoted_or_disabled(tmp_path):
    service = service_in(tmp_path)
    sole = service.create_user("jefa", PASSWORD, ADMIN)

    with pytest.raises(Conflict):
        service.set_role(sole.id, EDITOR)
    with pytest.raises(Conflict):
        service.set_disabled(sole.id, True)

    other = service.create_user("otro", PASSWORD, ADMIN)
    service.set_disabled(sole.id, True)
    assert service.store.active_admins() == 1
    with pytest.raises(Conflict):
        service.set_disabled(other.id, True)
    with pytest.raises(Conflict):
        service.set_role(other.id, EDITOR)


def test_password_change_requires_the_current_one(tmp_path):
    service = service_in(tmp_path)
    user = service.create_user("ana", PASSWORD, EDITOR)

    with pytest.raises(InvalidInput):
        service.change_password(user.id, "no-es", "otra-clave-valida")
    with pytest.raises(InvalidInput):
        service.change_password(user.id, PASSWORD, "corta")

    changed = service.change_password(user.id, PASSWORD, "otra-clave-valida")

    assert changed.must_change_password is False
    assert service.authenticate("ana", "otra-clave-valida")
    with pytest.raises(InvalidCredentials):
        service.authenticate("ana", PASSWORD)


def test_missing_accounts_are_reported_as_not_found(tmp_path):
    service = service_in(tmp_path)

    with pytest.raises(NotFound):
        service.set_role(999, EDITOR)
    with pytest.raises(NotFound):
        service.change_password("no-es-un-id", PASSWORD, "otra-clave-valida")


def test_disabling_requires_a_real_boolean(tmp_path):
    service = service_in(tmp_path)
    user = service.create_user("ana", PASSWORD, VIEWER)

    with pytest.raises(InvalidInput):
        service.set_disabled(user.id, "no")

    assert service.set_disabled(user.id, True).disabled is True


def test_bootstrap_only_creates_an_administrator_on_an_empty_database(tmp_path):
    service = service_in(tmp_path)

    assert service.bootstrap_admin("jefa", PASSWORD) is True
    assert service.bootstrap_admin("otra", PASSWORD) is False
    assert [item.username for item in service.store.list()] == ["jefa"]
    assert service.store.find("jefa").role == ADMIN


# ---------------------------------------------------------------- límite de intentos


def test_attempts_are_counted_per_address_and_username():
    now = [1000.0]
    throttle = LoginThrottle(max_failures=3, window_seconds=60, clock=lambda: now[0])

    for _ in range(3):
        assert throttle.retry_after("10.0.0.1", "ana") == 0
        throttle.record_failure("10.0.0.1", "ana")

    assert throttle.retry_after("10.0.0.1", "ana") == 61
    assert throttle.retry_after("10.0.0.1", "bea") == 0
    assert throttle.retry_after("10.0.0.2", "ana") == 0


def test_the_block_ends_when_the_window_passes_and_a_success_clears_the_count():
    now = [1000.0]
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=lambda: now[0])
    throttle.record_failure("10.0.0.1", "ana")
    now[0] += 40
    throttle.record_failure("10.0.0.1", "ana")
    assert throttle.retry_after("10.0.0.1", "ana") == 21

    now[0] += 21
    # El fallo más antiguo salió de la ventana: queda uno solo y ya no hay bloqueo.
    assert throttle.retry_after("10.0.0.1", "ana") == 0

    now[0] += 19
    throttle.record_failure("10.0.0.1", "ana")
    assert throttle.retry_after("10.0.0.1", "ana") == 21
    throttle.reset("10.0.0.1", "ana")
    assert throttle.retry_after("10.0.0.1", "ana") == 0


def test_exploring_usernames_from_one_address_is_capped():
    throttle = LoginThrottle(max_failures=3, window_seconds=60, per_address=4, clock=lambda: 5.0)

    for name in ("a", "b", "c"):
        assert throttle.retry_after("10.0.0.1", name) == 0
        throttle.record_failure("10.0.0.1", name)
    assert throttle.retry_after("10.0.0.1", "d") == 0
    throttle.record_failure("10.0.0.1", "d")

    assert throttle.retry_after("10.0.0.1", "e") > 0


def test_asking_does_not_grow_the_memory():
    throttle = LoginThrottle()

    for name in ("a", "b", "c"):
        assert throttle.retry_after("10.0.0.1", name) == 0

    assert throttle._failures == {}


# ---------------------------------------------------------------- qué exige cada ruta


@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("GET", "/api/graph/project/list", "read"),
        ("GET", "/api/simulation/sim-1/config/download", "read"),
        ("POST", "/api/graph/build", "write"),
        ("DELETE", "/api/graph/project/proj-1", "write"),
        ("POST", "/api/simulation/prepare/status", "read"),
        ("POST", "/api/simulation/env-status", "read"),
        ("POST", "/api/simulation/interview/history", "read"),
        ("POST", "/api/report/generate/status", "read"),
        ("POST", "/api/report/tools/search", "read"),
        ("POST", "/api/report/generate", "write"),
        ("POST", "/api/simulation/close-env", "write"),
        ("GET", "/api/auth/users", ADMIN),
        ("POST", "/api/auth/users/3/password", ADMIN),
        ("POST", "/api/auth/password", "read"),
        ("POST", "/api/auth/logout", "read"),
    ],
)
def test_access_required_by_route(method, path, expected):
    assert access_required(method, path) == expected


# ---------------------------------------------------------------- la API


def test_the_api_requires_a_session(app):
    client = app.test_client()

    denied = client.get("/api/graph/project/list")
    assert denied.status_code == 401
    assert denied.get_json()["code"] == "login_required"
    assert denied.get_json()["error"] == t("auth.error.loginRequired")
    assert client.post("/api/report/generate", json={}).status_code == 401

    assert client.get("/health").status_code == 200
    state = client.get("/api/auth/session").get_json()
    assert state == {
        "success": True,
        "data": {"user": None, "csrf": state["data"]["csrf"], "setup_needed": True},
    }
    assert len(state["data"]["csrf"]) == 64

    make_user(app, "ana", EDITOR)
    client = app.test_client()
    state = client.get("/api/auth/session").get_json()
    assert state["data"]["setup_needed"] is False
    assert login(client, "ana", PASSWORD).status_code == 200
    me = client.get("/api/auth/session").get_json()["data"]
    assert me["user"]["username"] == "ana" and me["csrf"] and me["setup_needed"] is False


def test_login_needs_the_token_of_the_browser_session(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    credentials = {"username": "ana", "password": PASSWORD}

    assert client.post("/api/auth/login", json=credentials).status_code == 403
    wrong = client.post("/api/auth/login", json=credentials, headers={"X-CSRF-Token": "0" * 64})
    assert wrong.status_code == 403
    assert wrong.get_json()["code"] == "csrf_rejected"
    assert login(client, "ana", PASSWORD).status_code == 200


def test_wrong_credentials_give_the_same_answer_for_unknown_users_and_wrong_passwords(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)

    unknown = login(client, "nadie", PASSWORD)
    wrong = login(client, "ana", "no-es-la-clave")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.get_json() == wrong.get_json()


def test_the_session_cookie_is_protected(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)

    response = login(client, "ana", PASSWORD)

    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert PASSWORD not in cookie


def test_login_gives_a_fresh_session_and_token(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    anonymous = csrf_of(client)

    fresh = login(client, "ana", PASSWORD).get_json()["data"]["csrf"]

    assert fresh != anonymous
    # El token de antes de entrar ya no sirve, y una sesión aún anónima no entra.
    assert client.post(WRITE_URL, json={}, headers={"X-CSRF-Token": anonymous}).status_code == 403
    stranger = app.test_client()
    csrf_of(stranger)
    assert stranger.post(WRITE_URL, json={}, headers={"X-CSRF-Token": csrf_of(stranger)}).status_code == 401


def test_a_viewer_can_read_but_not_change_anything(app):
    client = app.test_client()
    make_user(app, "visan", VIEWER)
    headers = signed(client, "visan", VIEWER)

    assert client.get("/api/graph/project/list").status_code == 200
    for method, url in (("POST", WRITE_URL), ("DELETE", "/api/graph/project/proj-1"), ("POST", "/api/report/generate",)):
        response = client.open(url, method=method, json={}, headers=headers)
        assert response.status_code == 403
        assert response.get_json()["code"] == "forbidden"
    assert client.get("/api/auth/users").status_code == 403


def test_a_viewer_may_use_the_routes_that_only_consult(app, monkeypatch):
    class FakeTools:
        def search_graph(self, **kwargs):
            return SimpleNamespace(to_dict=lambda: {"facts": []})

    monkeypatch.setattr("app.services.zep_tools.ZepToolsService", FakeTools)
    client = app.test_client()
    make_user(app, "visan", VIEWER)
    headers = signed(client, "visan", VIEWER)

    response = client.post(
        "/api/report/tools/search",
        json={"graph_id": "mirofish_1", "query": "¿quién?"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/api/simulation/prepare/status",
        "/api/simulation/env-status",
        "/api/simulation/interview/history",
        "/api/report/generate/status",
    ],
)
def test_a_viewer_reaches_every_status_route_with_an_empty_body(app, path):
    client = app.test_client()
    make_user(app, "visan", VIEWER)
    headers = signed(client, "visan", VIEWER)

    response = client.post(path, json={}, headers=headers)

    # Sin datos la ruta responde que falta algo, pero la protección la dejó pasar.
    assert response.status_code not in (401, 403), (path, response.status_code, response.get_data(as_text=True)[:200])


def test_an_editor_can_change_things_but_not_manage_users(app):
    client = app.test_client()
    make_user(app, "edito", EDITOR)
    headers = signed(client, "edito", EDITOR)

    reached = client.post(WRITE_URL, json={}, headers=headers)

    assert reached.status_code not in (401, 403)
    assert client.get("/api/auth/users").status_code == 403
    assert client.post("/api/auth/users", json={}, headers=headers).status_code == 403


def test_every_change_needs_the_token_of_its_own_session(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    make_user(app, "bea", EDITOR)
    headers = signed(client, "ana", EDITOR)

    for sent in ({}, {"X-CSRF-Token": "0" * 64}):
        response = client.post(WRITE_URL, json={}, headers=sent)
        assert response.status_code == 403
        assert response.get_json()["code"] == "csrf_rejected"

    other = app.test_client()
    foreign = signed(other, "bea", EDITOR)
    assert client.post(WRITE_URL, json={}, headers=foreign).status_code == 403
    assert client.post(WRITE_URL, json={}, headers=headers).status_code not in (401, 403)


def test_logout_closes_the_session(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    headers = signed(client, "ana", EDITOR)

    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/graph/project/list").status_code == 401
    assert client.get("/api/auth/session").get_json()["data"]["user"] is None


def test_a_user_changes_their_own_password(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    headers = signed(client, "ana", EDITOR)

    wrong = client.post(
        "/api/auth/password", json={"current_password": "no-es", "new_password": "otra-clave-valida"}, headers=headers
    )
    assert wrong.status_code == 400
    short = client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "corta"}, headers=headers)
    assert short.status_code == 400

    changed = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": "otra-clave-valida"}, headers=headers
    )

    assert changed.status_code == 200
    assert changed.get_json()["data"]["user"]["must_change_password"] is False
    assert login(client, "ana", PASSWORD).status_code == 401
    assert login(client, "ana", "otra-clave-valida").status_code == 200


def test_an_administrator_manages_users(app):
    client = app.test_client()
    make_user(app, "jefa", ADMIN)
    headers = signed(client, "jefa", ADMIN)

    created = client.post(
        "/api/auth/users", json={"username": "Nueva", "password": PASSWORD, "role": "viewer"}, headers=headers
    )
    assert created.status_code == 201
    user = created.get_json()["data"]["user"]
    assert (user["username"], user["role"], user["must_change_password"]) == ("Nueva", "viewer", True)
    assert "password_hash" not in user and PASSWORD not in created.get_data(as_text=True)

    assert [item["username"] for item in client.get("/api/auth/users").get_json()["data"]["users"]] == ["jefa", "Nueva"]

    promoted = client.patch(f"/api/auth/users/{user['id']}", json={"role": "editor"}, headers=headers)
    assert promoted.get_json()["data"]["user"]["role"] == "editor"
    reset = client.post(f"/api/auth/users/{user['id']}/password", json={"new_password": "otra-clave-valida"}, headers=headers)
    assert reset.status_code == 200
    assert reset.get_json()["data"]["user"]["must_change_password"] is True

    disabled = client.patch(f"/api/auth/users/{user['id']}", json={"disabled": True}, headers=headers)
    assert disabled.get_json()["data"]["user"]["disabled"] is True
    assert login(client, "Nueva", "otra-clave-valida").status_code == 401


def test_invalid_user_changes_are_rejected_before_touching_anything(app):
    client = app.test_client()
    make_user(app, "jefa", ADMIN)
    headers = signed(client, "jefa", ADMIN)
    before = client.get("/api/auth/users").get_json()["data"]["users"]

    for body in (
        {"username": "ab", "password": PASSWORD, "role": "viewer"},
        {"username": "bea", "password": "corta", "role": "viewer"},
        {"username": "bea", "password": PASSWORD, "role": "jefe"},
    ):
        assert client.post("/api/auth/users", json=body, headers=headers).status_code == 400
    assert client.post("/api/auth/users", json={"username": "jefa", "password": PASSWORD, "role": "viewer"}, headers=headers).status_code == 409
    assert client.patch("/api/auth/users/999", json={"role": "viewer"}, headers=headers).status_code == 404
    assert client.post("/api/auth/users/999/password", json={"new_password": PASSWORD}, headers=headers).status_code == 404
    assert client.get("/api/auth/users").get_json()["data"]["users"] == before


def test_the_last_administrator_cannot_be_taken_away_through_the_api(app):
    client = app.test_client()
    sole = make_user(app, "jefa", ADMIN)
    headers = signed(client, "jefa", ADMIN)

    demoted = client.patch(f"/api/auth/users/{sole.id}", json={"role": "viewer"}, headers=headers)
    disabled = client.patch(f"/api/auth/users/{sole.id}", json={"disabled": True}, headers=headers)

    assert demoted.status_code == disabled.status_code == 409
    assert client.get("/api/auth/users").get_json()["data"]["users"][0]["role"] == ADMIN
    assert client.get("/api/graph/project/list").status_code == 200


def test_open_sessions_survive_a_restart_only_with_a_fixed_key(app):
    make_user(app, "ana", EDITOR)
    # SECRET_KEY en el entorno: la clave con la que se firma es siempre la misma.
    app.secret_key = "una-clave-fija-que-estaba-en-el-entorno"
    client = app.test_client()
    assert login(client, "ana", PASSWORD).status_code == 200
    cookie = client.get_cookie("session")

    app.secret_key = "una-clave-fija-que-estaba-en-el-entorno"
    after = app.test_client()
    after.set_cookie("session", cookie.value, domain="localhost")
    assert after.get("/api/auth/session").get_json()["data"]["user"]["username"] == "ana"

    # Sin SECRET_KEY cada arranque genera una clave: las sesiones anteriores no valen.
    app.secret_key = "otra-clave-generada-al-arrancar"
    after = app.test_client()
    after.set_cookie("session", cookie.value, domain="localhost")
    assert after.get("/api/auth/session").get_json()["data"]["user"] is None


def test_a_disabled_account_loses_its_open_sessions(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)
    make_user(app, "jefa", ADMIN)
    signed(client, "ana", EDITOR)
    assert client.get("/api/graph/project/list").status_code == 200

    admin = app.test_client()
    admin_headers = signed(admin, "jefa", ADMIN)
    ana = app.extensions["auth"].store.find("ana")
    admin.patch(f"/api/auth/users/{ana.id}", json={"disabled": True}, headers=admin_headers)

    assert client.get("/api/graph/project/list").status_code == 401


def test_failed_logins_are_throttled(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)

    for _ in range(10):
        assert login(client, "ana", "mal", address="10.0.0.1").status_code == 401

    blocked = login(client, "ana", PASSWORD, address="10.0.0.1")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 601
    assert login(client, "ana", PASSWORD, address="10.0.0.2").status_code == 200


def test_a_correct_login_clears_the_failures_of_that_user(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)

    for _ in range(9):
        assert login(client, "ana", "mal", address="10.0.0.1").status_code == 401
    assert login(client, "ana", PASSWORD, address="10.0.0.1").status_code == 200
    client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_of(client)})
    for _ in range(9):
        assert login(client, "ana", "mal", address="10.0.0.1").status_code == 401


def test_the_first_administrator_comes_from_the_environment(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(Config, "ADMIN_USERNAME", "jefa")
    monkeypatch.setattr(Config, "ADMIN_PASSWORD", PASSWORD)
    client = create_app().test_client()

    response = login(client, "jefa", PASSWORD)

    assert response.status_code == 200
    user = response.get_json()["data"]["user"]
    assert (user["username"], user["role"], user["must_change_password"]) == ("jefa", ADMIN, True)


def test_a_weak_bootstrap_password_creates_nobody_and_says_why(monkeypatch, captured_logs):
    from app.config import Config

    monkeypatch.setattr(Config, "ADMIN_USERNAME", "jefa")
    monkeypatch.setattr(Config, "ADMIN_PASSWORD", "corta")
    client = create_app().test_client()

    assert login(client, "jefa", "corta").status_code == 401
    assert client.get("/api/auth/session").get_json()["data"]["user"] is None
    assert any("jefa" in line and t("auth.error.passwordTooShort") in line for line in captured_logs)
    assert "corta" not in " ".join(captured_logs)


def test_without_any_account_the_api_stays_closed_and_says_how_to_open_it(captured_logs):
    client = create_app().test_client()

    assert client.get("/api/graph/project/list").status_code == 401
    assert client.post("/api/auth/login", json={"username": "ana", "password": PASSWORD}).status_code == 403
    assert any("create-user" in line for line in captured_logs)


def test_passwords_never_reach_the_request_log(app):
    client = app.test_client()
    make_user(app, "ana", EDITOR)

    with _capture("mirofish.request") as lines:
        login(client, "ana", PASSWORD)
        client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "otra-clave-valida"},
            headers={"X-CSRF-Token": csrf_of(client)},
        )

    logged = " ".join(lines)
    assert "POST /api/auth/login" in logged
    assert PASSWORD not in logged
    assert "otra-clave-valida" not in logged
    # Las peticiones a otras rutas sí registran su cuerpo, como siempre.
    with _capture("mirofish.request") as lines:
        client.post("/api/report/generate", json={"note": "cuerpo-de-ejemplo"}, headers={"X-CSRF-Token": csrf_of(client)})
    assert "cuerpo-de-ejemplo" in " ".join(lines)


# ---------------------------------------------------------------- línea de comandos


def test_the_command_line_creates_and_administers_accounts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(PASSWORD + "\n"))
    monkeypatch.setattr(cli.Config, "AUTH_DB_PATH", str(tmp_path / "cli" / "users.db"))

    create = ["create-user", "--role", "admin", "--password-stdin"]
    assert cli.main([*create, "--username", "ana"]) == 0
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main([*create, "--username", "bela"]) == 0
    assert cli.main(["list-users"]) == 0
    listed = capsys.readouterr().out
    assert "ana" in listed and "bela" in listed and "admin" in listed and "debe cambiar la contraseña" in listed
    assert PASSWORD not in listed

    assert cli.main(["disable", "--username", "bela"]) == 0
    assert cli.main(["set-role", "--username", "ana", "--role", "viewer"]) == 1
    assert "único" in capsys.readouterr().err
    assert cli.main(["disable", "--username", "nadie"]) == 1

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("otra-clave-valida\n"))
    assert cli.main(["set-password", "--username", "ana", "--password-stdin"]) == 0
    service = AuthService(UserStore(str(tmp_path / "cli" / "users.db")))
    assert service.authenticate("ana", "otra-clave-valida")

    assert cli.main(["enable", "--username", "bela"]) == 0
    assert cli.main(["disable", "--username", "ana"]) == 0
    with pytest.raises(InvalidCredentials):
        service.authenticate("ana", "otra-clave-valida")
    assert cli.main(["enable", "--username", "ana"]) == 0
    assert cli.main(["disable", "--username", "bela"]) == 0
    assert cli.main(["set-role", "--username", "ana", "--role", "viewer"]) == 1
    assert cli.main(["enable", "--username", "bela"]) == 0
    assert cli.main(["set-role", "--username", "ana", "--role", "viewer"]) == 0


def test_the_command_line_never_takes_the_password_as_an_argument(monkeypatch):
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["create-user", "--username", "ana", "--password", "a-la-vista"])
    with pytest.raises(SystemExit):
        parser.parse_args(["create-user", "--username", "ana", "--role", "jefe"])


def test_without_a_terminal_the_command_line_requires_stdin(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))

    with pytest.raises(SystemExit) as error:
        cli.main(["create-user", "--username", "ana"])

    assert error.value.code == 2


def test_the_command_line_can_generate_the_password_and_shows_it_once(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(cli.Config, "AUTH_DB_PATH", str(tmp_path / "cli" / "users.db"))
    service = AuthService(UserStore(str(tmp_path / "cli" / "users.db")))
    shown = r"se muestra solo esta vez: (\S+)"

    assert cli.main(["create-user", "--username", "ana", "--role", "admin", cli.GENERATE_PASSWORD]) == 0
    first = re.search(shown, capsys.readouterr().out)
    assert first
    assert service.authenticate("ana", first.group(1)).must_change_password

    # Un nombre repetido no crea nada, y entonces tampoco muestra una contraseña.
    assert cli.main(["create-user", "--username", "ana", "--role", "admin", cli.GENERATE_PASSWORD]) == 1
    assert not re.search(shown, capsys.readouterr().out)

    assert cli.main(["set-password", "--username", "ana", cli.GENERATE_PASSWORD]) == 0
    second = re.search(shown, capsys.readouterr().out)
    assert second and second.group(1) != first.group(1)
    assert service.authenticate("ana", second.group(1)).must_change_password


def test_the_two_ways_to_hand_in_a_password_exclude_each_other(monkeypatch):
    parser = cli._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["create-user", "--username", "ana", cli.PASSWORD_STDIN, cli.GENERATE_PASSWORD])


def test_example_values_from_the_template_are_never_used_as_secrets(monkeypatch):
    from app.config import _secret_env

    for value in (None, "", "   ", "change_me_to_a_random_value", "ChangeMe"):
        if value is None:
            monkeypatch.delenv("SECRET_KEY", raising=False)
        else:
            monkeypatch.setenv("SECRET_KEY", value)
        assert _secret_env("SECRET_KEY") is None

    monkeypatch.setenv("SECRET_KEY", "  f3a9c1d8e6b4a2c7  ")
    assert _secret_env("SECRET_KEY") == "f3a9c1d8e6b4a2c7"


def test_the_store_recovers_if_its_file_disappears_while_the_app_runs(tmp_path):
    path = tmp_path / "users.db"
    store = UserStore(str(path))
    store.create("ana", hash_password(PASSWORD), EDITOR, must_change_password=True)
    assert store.count() == 1

    for leftover in tmp_path.glob("users.db*"):
        leftover.unlink()

    # Sin cuentas, como al empezar; no falla cada petición hasta reiniciar.
    assert store.count() == 0
    assert store.active_admins() == 0
    assert store.create("jefa", hash_password(PASSWORD), ADMIN, must_change_password=True).role == ADMIN
    assert store.find("jefa").is_active
