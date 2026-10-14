# Medical Information RAG Chatbot

A local RAG prototype for retrieving information from medical reference PDFs and generating context-aware answers. This project is for educational and informational use only. It is not a medical device and must not be used for diagnosis, treatment decisions, or emergencies. It has not been clinically validated.

## Requirements

- Python 3.10 or newer
- An Auth0 tenant and Regular Web Application
- A Pinecone account and a dense vector index configured for 384-dimensional embeddings
- A Groq API key
- PDF references that you are permitted to use

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions). Create the Pinecone index with a compatible dimension and a metric such as cosine before indexing. No medical references are bundled with this project.

## Run locally (Windows PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your Pinecone API key, Pinecone index name, Groq API key, and Auth0 application settings. Keep `.env` private; it is excluded from Git. Do not commit API keys, session secrets, or private documents. Do not put patient-identifying or private health information into the chatbot. Questions and retrieved text are sent to the configured Groq and Pinecone services.

### Configure Auth0

1. In Auth0, create an application of type **Regular Web Application**.
2. In the application settings, add `http://127.0.0.1:5000/auth/callback` to **Allowed Callback URLs**.
3. Add `http://127.0.0.1:5000/signed-out` to **Allowed Logout URLs** and `http://127.0.0.1:5000` to **Allowed Web Origins**.
4. Copy the Auth0 domain, client ID, and client secret into `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, and `AUTH0_CLIENT_SECRET`. The domain should not include `https://`.
5. Create an **API** in Auth0 with an identifier you control (for example, `https://medica-api.example`), using RS256. Put that exact identifier into `AUTH0_API_AUDIENCE` in the root `.env` and `EXPO_PUBLIC_AUTH0_API_AUDIENCE` in `mobile/.env`. The API identifier is not a secret.
6. Keep `AUTH0_CALLBACK_URL=http://127.0.0.1:5000/auth/callback`. Generate a session secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and put it in `SESSION_SECRET`.
7. Restart the app, open `http://127.0.0.1:5000`, and sign in through Auth0.

The Android client uses Authorization Code with PKCE and the same Auth0 API audience. Add `medica://callback` to the Native application's Allowed Callback URLs and `medica://signed-out` to its Allowed Logout URLs. Mobile requests send a bearer access token; the Flask API validates its signature, issuer, expiry, and audience before deriving the account namespace from the verified `sub` claim.

For production, use HTTPS, set `APP_ENV=production`, generate a unique strong `SESSION_SECRET`, and add the production callback/logout/origin URLs in Auth0. Never use the local URLs for a deployed site.

## Add references

After signing in, select **Add PDF** in the app. Each account receives a server-derived Pinecone namespace; the browser cannot choose another user's namespace. Documents list and delete operations are scoped to the authenticated Auth0 subject. PDFs are limited to 15 MB and 200 pages.

For administrative batch indexing, pass the Auth0 subject for the target account so the command uses its derived namespace and registers the documents:

```powershell
python ingest.py data\references --auth0-subject "auth0|provider-user-id"
```

The standard end-user path remains the authenticated upload UI. Do not index private documents into the legacy default Pinecone namespace.

Documents expire after `RETENTION_DAYS` (default 90). Expired chunks are excluded from retrieval immediately. Run the cleanup command daily to remove expired vectors, local files, and registry records:

```powershell
python cleanup_expired.py
```

Use Windows Task Scheduler to run that command daily for a local deployment, or an equivalent protected scheduled task in the deployment environment. The registry and uploaded files currently use local SQLite/disk storage, so a production multi-instance deployment must replace those with shared durable storage and a managed database before scaling horizontally.

The earlier prototype indexed documents into Pinecone's default shared namespace. Authenticated users do not search that namespace. Re-upload each needed PDF while signed in to add it to the correct account namespace; clean up the old shared namespace separately after confirming no one needs it.

Start the web app:

```powershell
python app.py
```

Open `http://127.0.0.1:5000` and sign in. Without Auth0 configuration, the app fails closed and displays the required settings. Do not enable Flask debug mode for a public deployment.

## Tests and evaluation

```powershell
python -m pytest -q
```

The tests exercise local request handling, Auth0 session boundaries, CSRF checks, account-scoped registry operations, expiry, source formatting, and abstention behavior without calling paid services. They do not validate medical accuracy or a live Auth0 login.

To assess retrieval, copy `data/evaluation_cases.example.json` to `data/evaluation_cases.json`, adjust queries, expected source filenames, and `expected_terms` to match your reference collection, then run:

```powershell
python evaluate.py data/evaluation_cases.json --top-k 3 --auth0-subject "auth0|provider-user-id"
```

The report includes hit rate@k and mean reciprocal rank@k for labeled cases, relevance scores, matched source/page, and a short local preview for human review. A chunk counts as relevant only when it matches the expected source (if specified) and contains at least one expected term (if specified). This evaluates retrieval only; it does not establish answer correctness or clinical safety. Keep evaluation queries free of private health details.

`TOP_K` controls retrieved chunks (default 3). `MIN_RELEVANCE_SCORE` sets the minimum Pinecone relevance score accepted for generation (default 0.25); tune it against reviewed examples, not as a guarantee of accuracy.

## Project layout

- `app.py`: Flask UI and chat API
- `medical_rag.py`: Pinecone retrieval, Groq generation, and source labels
- `ingest.py`: PDF extraction, chunking, embedding, and indexing
- `user_identity.py`: stable, non-PII Auth0-subject hashing for Pinecone namespace/owner keys
- `document_registry.py`: SQLite ownership and retention records
- `cleanup_expired.py`: scheduled expiry cleanup for vectors and local PDFs
- `data/uploads/`: content-addressed local copies of PDFs uploaded through the UI (ignored by Git)
- `evaluate.py`: labeled retrieval evaluation without calling Groq
- `templates/` and `static/`: responsive browser interface
- `mobile/`: Expo Android and web client
- `tests/`: local behavior checks

## Safety and evaluation

Answers are prompted to rely only on retrieved excerpts, disclose insufficient evidence, avoid diagnosis and prescribing, and direct personal care decisions to qualified professionals. Retrieval and model output are not guaranteed to be correct. This prototype has not undergone a security, privacy, regulatory, or clinical validation review. Evaluate retrieval relevance, groundedness, refusal behavior, and safety with an appropriate review process before any broader use. For an immediate emergency, contact local emergency services.

## Container deployment

Build and run the production-style Linux container locally:

```powershell
docker build -t medical-rag-chatbot .
docker run --rm -p 5000:5000 --env-file .env medical-rag-chatbot
```

The container uses Gunicorn, runs as a non-root user, and has a `/healthz` health check. `requirements-prod.txt` adds the Linux-only Gunicorn dependency; the regular requirements remain installable on Windows. The container does not include local PDFs. Persistent SQLite/uploads need mounted durable storage for a single instance; use managed shared database and object storage before running multiple instances.

GitHub Actions runs the Python tests, mobile TypeScript check/web export, and container build. AWS ECR/EC2 resources, an AWS role for CI, secrets management, TLS, access control, rate limiting, monitoring, security review, and deployment validation still need to be configured in the owner’s accounts. This repository is not deployed. Do not expose the prototype publicly before adding authentication and appropriate privacy/security controls.

## Android app and Expo web client

The cross-platform client is in `mobile/`. It uses Expo Router and Auth0 Authorization Code with PKCE. It stores native access tokens with SecureStore and uses separate public Auth0 clients for Android (Native application) and browser (SPA application). No Auth0 client secret is included in the mobile build.

1. Create an Auth0 **Native** application for Android. Add `medica://callback` to Allowed Callback URLs and `medica://signed-out` to Allowed Logout URLs.
2. Create an Auth0 **Single Page Application** for the Expo web client. Add the local web callback/logout/origin shown by Expo during development (typically `http://localhost:8081/callback`, `http://localhost:8081/signed-out`, and `http://localhost:8081`) and the equivalent final HTTPS website URLs.
3. Create or reuse an Auth0 API using the same identifier configured in `AUTH0_API_AUDIENCE`. Use RS256 signing.
4. Run `Copy-Item mobile/.env.example mobile/.env` in PowerShell. Set the tenant domain, Native client ID, SPA client ID, and API audience. No client secret belongs in this file.
5. Set `EXPO_PUBLIC_API_URL` to the Flask API base URL. Leave it blank for same-machine web and Android emulator defaults. Physical phones need a reachable host URL for local testing; production needs a deployed HTTPS backend. Add each Expo web origin to backend `CORS_ALLOWED_ORIGINS`.

Run the cross-platform web preview from PowerShell:

```powershell
cd mobile
npm run web
```

The static web bundle can be built with `npm run build:web` and deployed from `mobile/dist` to an HTTPS web host. The existing Flask-rendered website remains at the repository root.

Android release builds use EAS Build, so this Windows machine does not need Java/Android Studio installed:

```powershell
cd mobile
npx eas-cli@latest login
npx eas-cli@latest build:configure
npx eas-cli@latest build --profile preview --platform android
```

For a Play Store release, create a Play Console developer account, complete the store listing, privacy policy, Data safety and medical-app declarations, configure a unique permanent Android application ID, and build an Android App Bundle:

```powershell
npx eas-cli@latest build --profile production --platform android
```

Then submit the signed `.aab` through Play Console, complete required review/testing tracks, and publish it. A release cannot be uploaded until the owner configures an Expo account/EAS credentials, production Auth0 URLs and clients, a publicly reachable HTTPS API, privacy policy, app assets, and Play Console account. Do not claim Google Play approval until Google approves it.

Before a public release, review dependency advisories: the generated SDK 57 dependency tree currently reports 28 npm audit findings (10 moderate, 18 high), and npm's suggested forced fix would downgrade Expo to an incompatible older SDK. Do not publish until the advisories have been reviewed and resolved with SDK-compatible dependency updates. The generated template icon also needs final branded store artwork/screenshots. No Play Store release has been built or submitted from this workspace.