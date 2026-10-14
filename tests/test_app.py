from io import BytesIO
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import create_app

CSRF_HEADERS = {"X-CSRFToken": "test-csrf-token"}


def authenticated_client(tmp_path, subject="auth0|test-user"):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-session-secret",
            "DOCUMENT_DATABASE": str(tmp_path / "documents.sqlite3"),
            "UPLOAD_ROOT": str(tmp_path / "uploads"),
            "AUTH0_DOMAIN": "",
            "AUTH0_CLIENT_ID": "",
            "AUTH0_CLIENT_SECRET": "",
        }
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["user"] = {"sub": subject, "name": "Test User"}
        session["csrf_token"] = "test-csrf-token"
    return client, app


def test_homepage_requires_login():
    client = create_app().test_client()
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_health_check_is_available_without_cloud_configuration():
    response = create_app().test_client().get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_login_shows_actionable_auth0_setup_when_unconfigured(tmp_path):
    response = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-session-secret",
            "AUTH0_DOMAIN": "",
            "AUTH0_CLIENT_ID": "",
            "AUTH0_CLIENT_SECRET": "",
            "DOCUMENT_DATABASE": str(tmp_path / "login-documents.sqlite3"),
        }
    ).test_client().get("/login")

    assert response.status_code == 503
    assert b"Auth0 setup required" in response.data
    assert b"AUTH0_CALLBACK_URL" in response.data


def test_authenticated_homepage_loads(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.get("/")

    assert response.status_code == 200
    assert b"Medical information" in response.data
    assert b"not for diagnosis" in response.data


def test_api_rejects_anonymous_requests_even_with_supplied_user_id():
    response = create_app().test_client().post(
        "/api/chat", json={"question": "hello", "user_id": "victim"}, headers=CSRF_HEADERS
    )

    assert response.status_code == 401


def test_auth0_access_token_checks_signature_issuer_and_audience(monkeypatch):
    import app as flask_app

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeSigningKey:
        key = public_key

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            return FakeSigningKey()

    monkeypatch.setattr(flask_app, "_auth0_jwks_client", lambda domain: FakeJwksClient())
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://tenant.example.com/",
        "aud": "https://medica-api.example",
        "sub": "auth0|verified-user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(claims, private_key, algorithm="RS256")
    config = {
        "AUTH0_DOMAIN": "tenant.example.com",
        "AUTH0_API_AUDIENCE": "https://medica-api.example",
    }

    assert flask_app._verify_auth0_access_token(token, config)["sub"] == "auth0|verified-user"

    config["AUTH0_API_AUDIENCE"] = "https://wrong-api.example"
    with pytest.raises(jwt.InvalidAudienceError):
        flask_app._verify_auth0_access_token(token, config)


def test_api_cors_preflight_allows_configured_expo_web_origin(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-session-secret",
            "DOCUMENT_DATABASE": str(tmp_path / "cors.sqlite3"),
            "CORS_ALLOWED_ORIGINS": ["http://localhost:8081"],
        }
    )

    response = app.test_client().options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:8081"


def test_bearer_token_identity_is_used_for_chat(monkeypatch, tmp_path):
    import app as flask_app
    import medical_rag
    from user_identity import user_namespace

    observed = {}
    monkeypatch.setattr(
        flask_app,
        "_verify_auth0_access_token",
        lambda token, config: {"sub": "auth0|verified-user", "name": "Verified"},
    )
    monkeypatch.setattr(
        medical_rag,
        "answer_question",
        lambda question, namespace: observed.update(namespace=namespace)
        or {"answer": "Grounded.", "sources": []},
    )
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-session-secret",
            "DOCUMENT_DATABASE": str(tmp_path / "bearer.sqlite3"),
            "AUTH0_DOMAIN": "tenant.example.com",
            "AUTH0_API_AUDIENCE": "https://medica-api.example",
            "CORS_ALLOWED_ORIGINS": [],
        }
    )

    response = app.test_client().post(
        "/api/chat",
        headers={"Authorization": "Bearer signed-token"},
        json={"question": "Question", "user_id": "auth0|attacker"},
    )

    assert response.status_code == 200
    assert observed["namespace"] == user_namespace("auth0|verified-user")


def test_upload_requires_a_pdf(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.post(
        "/api/documents",
        data={"file": (BytesIO(b"not a pdf"), "notes.txt")},
        content_type="multipart/form-data",
        headers=CSRF_HEADERS,
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Choose a PDF file to upload."


def test_upload_rejects_content_without_pdf_signature(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.post(
        "/api/documents",
        data={"file": (BytesIO(b"not a PDF document"), "reference.pdf")},
        content_type="multipart/form-data",
        headers=CSRF_HEADERS,
    )

    assert response.status_code == 400
    assert "valid PDF" in response.get_json()["error"]


def test_upload_indexes_pdf_and_returns_chunk_count(monkeypatch, tmp_path):
    import ingest

    from user_identity import user_namespace

    observed = {}
    monkeypatch.setattr(
        ingest,
        "ingest_uploaded_pdf",
        lambda filename, content, upload_dir, namespace: observed.update(namespace=namespace)
        or {
            "document_id": "a" * 64,
            "stored_path": str(tmp_path / "uploaded.pdf"),
            "filename": "reference.pdf",
            "chunks": 21,
        },
    )
    client, app = authenticated_client(tmp_path)
    response = client.post(
        "/api/documents",
        data={
            "namespace": "attacker-selected-namespace",
            "file": (BytesIO(b"%PDF-1.4 fake fixture"), "reference.pdf"),
        },
        content_type="multipart/form-data",
        headers=CSRF_HEADERS,
    )

    assert response.status_code == 201
    assert response.get_json()["document"] == "reference.pdf"
    assert response.get_json()["chunks"] == 21
    assert response.get_json()["expires_at"]
    documents = client.get("/api/documents").get_json()["documents"]
    assert len(documents) == 1
    assert documents[0]["filename"] == "reference.pdf"
    assert user_namespace("auth0|test-user") != user_namespace("auth0|another-user")
    assert observed["namespace"] == user_namespace("auth0|test-user")


def test_chat_rejects_empty_question(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.post("/api/chat", json={"question": "  "}, headers=CSRF_HEADERS)

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_chat_rejects_overlong_question(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.post("/api/chat", json={"question": "x" * 1001}, headers=CSRF_HEADERS)

    assert response.status_code == 400


def test_chat_explains_missing_service_configuration(monkeypatch, tmp_path):
    import medical_rag

    for name in ("PINECONE_API_KEY", "PINECONE_INDEX_NAME", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    client, _app = authenticated_client(tmp_path)
    response = client.post(
        "/api/chat", json={"question": "What is in the library?"}, headers=CSRF_HEADERS
    )

    assert response.status_code == 503
    assert "PINECONE_API_KEY" in response.get_json()["error"]


def test_chat_treats_template_credentials_as_unconfigured(monkeypatch, tmp_path):
    import medical_rag

    monkeypatch.setenv("PINECONE_API_KEY", "replace-with-your-pinecone-api-key")
    monkeypatch.setenv("PINECONE_INDEX_NAME", "medical-reference-index")
    monkeypatch.setenv("GROQ_API_KEY", "replace-with-your-groq-api-key")
    client, _app = authenticated_client(tmp_path)
    response = client.post(
        "/api/chat", json={"question": "What is in the library?"}, headers=CSRF_HEADERS
    )

    assert response.status_code == 503
    assert "PINECONE_API_KEY" in response.get_json()["error"]
    assert "GROQ_API_KEY" in response.get_json()["error"]


def test_chat_returns_grounded_response(monkeypatch, tmp_path):
    import medical_rag
    from user_identity import user_namespace

    observed = {}
    monkeypatch.setattr(
        medical_rag,
        "answer_question",
        lambda question, namespace: observed.update(namespace=namespace)
        or {"answer": "Reference-based answer.", "sources": ["guide.pdf, page 2"]},
    )
    client, _app = authenticated_client(tmp_path)
    response = client.post(
        "/api/chat",
        json={"question": "What does the reference say?", "user_id": "auth0|victim"},
        headers=CSRF_HEADERS,
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "answer": "Reference-based answer.",
        "sources": ["guide.pdf, page 2"],
    }
    assert observed["namespace"] == user_namespace("auth0|test-user")


def test_missing_csrf_token_rejects_authenticated_mutation(tmp_path):
    client, _app = authenticated_client(tmp_path)
    response = client.post("/api/chat", json={"question": "hello"})

    assert response.status_code == 400


def test_document_listing_is_scoped_to_authenticated_account(tmp_path):
    from document_registry import upsert_document
    from user_identity import user_key, user_namespace

    client, app = authenticated_client(tmp_path, subject="auth0|alice")
    upsert_document(
        app.config["DOCUMENT_DATABASE"],
        user_key("auth0|bob"),
        user_namespace("auth0|bob"),
        "b" * 64,
        "bob.pdf",
        str(tmp_path / "bob.pdf"),
        3,
        90,
    )

    response = client.get("/api/documents")

    assert response.status_code == 200
    assert response.get_json()["documents"] == []


def test_account_cannot_delete_another_accounts_document(monkeypatch, tmp_path):
    from document_registry import upsert_document
    from user_identity import user_key, user_namespace

    called = []

    class FakeRetriever:
        def delete(self, **kwargs):
            called.append(kwargs)

    import medical_rag

    monkeypatch.setattr(medical_rag, "_get_retriever", lambda settings, namespace: FakeRetriever())
    monkeypatch.setattr(medical_rag, "_required_settings", lambda require_groq=False: {})
    client, app = authenticated_client(tmp_path, subject="auth0|alice")
    upsert_document(
        app.config["DOCUMENT_DATABASE"],
        user_key("auth0|bob"),
        user_namespace("auth0|bob"),
        "b" * 64,
        "bob.pdf",
        str(tmp_path / "bob.pdf"),
        3,
        90,
    )

    response = client.delete("/api/documents/" + "b" * 64, headers=CSRF_HEADERS)

    assert response.status_code == 404
    assert called == []