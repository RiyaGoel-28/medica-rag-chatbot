import os
import hmac
import secrets
from functools import lru_cache
from urllib.parse import urlencode
from datetime import timedelta

from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()


@lru_cache(maxsize=8)
def _auth0_jwks_client(domain):
    from jwt import PyJWKClient

    return PyJWKClient(f"https://{domain}/.well-known/jwks.json", cache_jwk_set=True)


def _verify_auth0_access_token(token, config):
    import jwt

    domain = config.get("AUTH0_DOMAIN", "")
    audience = config.get("AUTH0_API_AUDIENCE", "")
    if not domain or not audience:
        raise ValueError("Auth0 API audience is not configured.")
    key = _auth0_jwks_client(domain).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"https://{domain}/",
        options={"require": ["exp", "iat", "sub", "iss", "aud"]},
    )
    return {"sub": claims["sub"], "name": claims.get("name") or claims["sub"]}


def create_app(test_config=None):
    app = Flask(__name__)
    configured_secret = os.getenv("SESSION_SECRET", "")
    if configured_secret.strip().lower().startswith("replace-with-"):
        configured_secret = ""
    if not configured_secret and os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("Set a strong SESSION_SECRET before running in production.")

    app.config.update(
        SECRET_KEY=configured_secret or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("APP_ENV", "development").lower() == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        AUTH0_DOMAIN=os.getenv("AUTH0_DOMAIN", "").strip().removeprefix("https://").rstrip("/"),
        AUTH0_CLIENT_ID=os.getenv("AUTH0_CLIENT_ID", ""),
        AUTH0_CLIENT_SECRET=os.getenv("AUTH0_CLIENT_SECRET", ""),
        AUTH0_API_AUDIENCE=os.getenv("AUTH0_API_AUDIENCE", ""),
        AUTH0_CALLBACK_URL=os.getenv("AUTH0_CALLBACK_URL", ""),
        CORS_ALLOWED_ORIGINS=[
            origin.strip()
            for origin in os.getenv(
                "CORS_ALLOWED_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081"
            ).split(",")
            if origin.strip()
        ],
        DOCUMENT_DATABASE=os.path.join(app.instance_path, "documents.sqlite3"),
        UPLOAD_ROOT=os.path.join(app.root_path, "data", "uploads"),
        RETENTION_DAYS=int(os.getenv("RETENTION_DAYS", "90")),
    )
    if test_config:
        app.config.update(test_config)
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ALLOWED_ORIGINS"]}},
        supports_credentials=True,
        allow_headers=["Content-Type", "X-CSRFToken", "Authorization"],
        methods=["GET", "POST", "DELETE", "OPTIONS"],
    )
    os.makedirs(app.instance_path, exist_ok=True)

    from document_registry import initialize_registry

    initialize_registry(app.config["DOCUMENT_DATABASE"])

    oauth = OAuth(app)
    auth0 = None
    auth0_values = (app.config["AUTH0_DOMAIN"], app.config["AUTH0_CLIENT_ID"], app.config["AUTH0_CLIENT_SECRET"])
    if all(value and not value.lower().startswith(("replace-with-", "your-tenant")) for value in auth0_values):
        auth0 = oauth.register(
            name="auth0",
            client_id=app.config["AUTH0_CLIENT_ID"],
            client_secret=app.config["AUTH0_CLIENT_SECRET"],
            server_metadata_url=(
                f"https://{app.config['AUTH0_DOMAIN']}/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid profile"},
        )

    from user_identity import user_key, user_namespace

    @app.before_request
    def protect_application():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return None
        public_endpoints = {"health", "login", "auth_callback", "signed_out", "static"}
        if request.endpoint in public_endpoints:
            return None
        account = session.get("user")
        authorization = request.headers.get("Authorization", "")
        bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if request.path.startswith("/api/") and bearer_token:
            try:
                account = _verify_auth0_access_token(bearer_token, app.config)
            except Exception:
                return jsonify(error="The access token is invalid or expired. Sign in again."), 401
            g.current_user = account
            return None
        if not account or not account.get("sub"):
            if request.path.startswith("/api/"):
                return jsonify(error="Sign in to use the reference library."), 401
            return redirect(url_for("login"))
        g.current_user = account
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected = session.get("csrf_token", "")
            supplied = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                abort(400, description="Invalid or missing CSRF token.")
        return None

    @app.context_processor
    def inject_template_session():
        if session.get("user"):
            session.setdefault("csrf_token", secrets.token_urlsafe(32))
            return {"current_user": session["user"], "csrf_token": session["csrf_token"]}
        return {"current_user": None, "csrf_token": ""}

    @app.get("/")
    def index():
        return render_template("index.html", documents=None)

    @app.get("/login")
    def login():
        if auth0 is None:
            return render_template("login.html", configured=False), 503
        callback_url = app.config["AUTH0_CALLBACK_URL"] or url_for(
            "auth_callback", _external=True
        )
        return auth0.authorize_redirect(callback_url)

    @app.get("/auth/callback")
    def auth_callback():
        if auth0 is None:
            return render_template("login.html", configured=False), 503
        try:
            token = auth0.authorize_access_token()
        except Exception:
            app.logger.exception("Auth0 sign-in failed")
            return render_template("login.html", configured=True, error="Sign-in failed. Please try again."), 401
        profile = token.get("userinfo") or {}
        subject = profile.get("sub")
        if not subject:
            return render_template("login.html", configured=True, error="The identity provider returned no account ID."), 401
        session.clear()
        session.permanent = True
        session["user"] = {"sub": subject, "name": profile.get("name") or "Account"}
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("index"))

    @app.get("/signed-out")
    def signed_out():
        return render_template("login.html", configured=auth0 is not None, signed_out=True)

    @app.post("/logout")
    def logout():
        session.clear()
        if auth0 is not None:
            return_to = url_for("signed_out", _external=True)
            query = urlencode({"client_id": app.config["AUTH0_CLIENT_ID"], "returnTo": return_to})
            return redirect(f"https://{app.config['AUTH0_DOMAIN']}/v2/logout?{query}")
        return redirect(url_for("login"))

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="PDF files must be 15 MB or smaller."), 413

    @app.route("/api/documents", methods=["GET", "POST"])
    def upload_document():
        from document_registry import list_documents, upsert_document
        from user_identity import user_key, user_namespace

        account = g.current_user
        owner = user_key(account["sub"])
        namespace = user_namespace(account["sub"])
        if request.method == "GET":
            return jsonify(documents=list_documents(app.config["DOCUMENT_DATABASE"], owner))

        uploaded_file = request.files.get("file")
        if uploaded_file is None or not uploaded_file.filename:
            return jsonify(error="Choose a PDF file to upload."), 400

        retention_days = app.config["RETENTION_DAYS"]
        if not 1 <= retention_days <= 3650:
            return jsonify(error="RETENTION_DAYS must be between 1 and 3650."), 500

        from ingest import MAX_UPLOAD_BYTES, ingest_uploaded_pdf
        from medical_rag import ConfigurationError

        content = uploaded_file.stream.read(MAX_UPLOAD_BYTES + 1)
        try:
            chunks = ingest_uploaded_pdf(
                uploaded_file.filename,
                content,
                app.config["UPLOAD_ROOT"],
                namespace,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except ConfigurationError as exc:
            return jsonify(error=str(exc)), 503
        except Exception:
            app.logger.exception("Unable to index the uploaded PDF")
            return jsonify(error="The PDF could not be indexed. Check service settings and try again."), 502

        metadata = upsert_document(
            app.config["DOCUMENT_DATABASE"],
            owner,
            namespace,
            chunks["document_id"],
            chunks["filename"],
            chunks["stored_path"],
            chunks["chunks"],
            retention_days,
        )

        return jsonify(
            document=chunks["filename"],
            document_id=chunks["document_id"],
            chunks=chunks["chunks"],
            expires_at=metadata["expires_at"],
            message=f"Indexed {chunks['chunks']} chunks from {chunks['filename']}.",
        ), 201

    @app.delete("/api/documents/<document_id>")
    def delete_document(document_id):
        from document_registry import delete_document_record, get_document
        from medical_rag import _get_retriever, _required_settings
        from user_identity import user_key

        account = g.current_user
        owner = user_key(account["sub"])
        record = get_document(app.config["DOCUMENT_DATABASE"], owner, document_id)
        if record is None:
            return jsonify(error="Document not found."), 404
        settings = _required_settings(require_groq=False)
        _get_retriever(settings, record["namespace"]).delete(
            filter={"document_id": document_id}, namespace=record["namespace"]
        )
        try:
            os.remove(record["stored_path"])
        except FileNotFoundError:
            pass
        delete_document_record(app.config["DOCUMENT_DATABASE"], owner, document_id)
        return jsonify(message=f"Deleted {record['filename']}.")

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        question = payload.get("question", "")
        if not isinstance(question, str) or not question.strip():
            return jsonify(error="Enter a question to continue."), 400
        if len(question) > 1000:
            return jsonify(error="Questions must be 1,000 characters or fewer."), 400

        try:
            from medical_rag import ConfigurationError, answer_question

            from user_identity import user_namespace

            result = answer_question(question.strip(), namespace=user_namespace(g.current_user["sub"]))
        except ConfigurationError as exc:
            return jsonify(error=str(exc)), 503
        except Exception:
            app.logger.exception("Unable to process the chat request")
            return jsonify(error="The assistant is temporarily unavailable. Please try again."), 502

        return jsonify(answer=result["answer"], sources=result["sources"])

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)