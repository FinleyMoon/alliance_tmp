# SECURITY & VULNERABILITY TRACKING DIRECTORY

This document provides the authoritative registry of all upstream software components, libraries, runtimes, and system packages utilized by the Alliance Platform. To maintain container security and defend against Common Vulnerabilities and Exposures (CVEs), track the projects and advisory feeds listed below.

---

## 1. High-Priority Attack Surfaces

The components below process untrusted external data (network requests, user uploads, encrypted credentials) and require immediate patching whenever an advisory is issued.

### 1.1 Image Processing & File Parsing (Critical Exposure)
User avatars are uploaded by untrusted clients. Image decoders are historically prone to memory corruption, buffer overflows, and decompression bombs.

* **Project:** `Pillow` (Python Imaging Library)
  * **Role:** EXIF stripping, Lanczos image resizing ($128\times 128$), and PNG re-encoding.
  * **Security Advisories:** [https://github.com/python-pillow/Pillow/security/advisories](https://github.com/python-pillow/Pillow/security/advisories)
  * **Release Notes:** [https://pillow.readthedocs.io/en/stable/releasenotes/](https://pillow.readthedocs.io/en/stable/releasenotes/)
  * **Debian Underlying Packages:** `libjpeg-turbo` ([https://github.com/libjpeg-turbo/libjpeg-turbo](https://github.com/libjpeg-turbo/libjpeg-turbo)), `zlib` ([https://zlib.net/](https://zlib.net/)).

### 1.2 Cryptography & Password Hashing
Handles player authentication, session signing, Argon2id key derivation, and VAPID EC P-256 signatures.

* **Project:** `cryptography` (Python Cryptographic Authority / PyCA)
  * **Role:** Native Argon2id KDF, ECDSA SECP256R1 key generation, HMAC-SHA256 session signatures.
  * **Security Advisories:** [https://github.com/pyca/cryptography/security/advisories](https://github.com/pyca/cryptography/security/advisories)
  * **PyPI:** [https://pypi.org/project/cryptography/](https://pypi.org/project/cryptography/)
* **Project:** `argon2-cffi`
  * **Role:** CFFI bindings for the reference Argon2 password hashing algorithm.
  * **Security Advisories:** [https://github.com/hynek/argon2-cffi/security/advisories](https://github.com/hynek/argon2-cffi/security/advisories)
  * **Upstream Reference C Implementation:** [https://github.com/P-H-C/phc-winner-argon2](https://github.com/P-H-C/phc-winner-argon2)

### 1.3 Web Server, ASGI Protocol & Streaming Ingestion
Exposed to direct network traffic via reverse proxy; handles HTTP parsing, multipart body streaming, and persistent WebSockets.

* **Project:** `fastapi`
  * **Security Advisories:** [https://github.com/fastapi/fastapi/security/advisories](https://github.com/fastapi/fastapi/security/advisories)
* **Project:** `uvicorn` (ASGI Web Server)
  * **Security Advisories:** [https://github.com/encode/uvicorn/security/advisories](https://github.com/encode/uvicorn/security/advisories)
* **Project:** `starlette` (FastAPI core foundation)
  * **Security Advisories:** [https://github.com/encode/starlette/security/advisories](https://github.com/encode/starlette/security/advisories)
* **Project:** `python-multipart` (Form and avatar streaming parser)
  * **Security Advisories:** [https://github.com/Kludex/python-multipart/security/advisories](https://github.com/Kludex/python-multipart/security/advisories)
* **Project:** `websockets` (Real-time duplex protocol)
  * **Security Advisories:** [https://github.com/python-websockets/websockets/security/advisories](https://github.com/python-websockets/websockets/security/advisories)
* **Project:** `h11` (HTTP/1.1 protocol parser)
  * **Repository:** [https://github.com/python-hyper/h11](https://github.com/python-hyper/h11)

---

## 2. Container Base Image & System Packages

### 2.1 Python Base Runtime
* **Base Image:** `python:3.12-slim` (Maintained on Debian Bookworm)
  * **Docker Official Images Tracker:** [https://github.com/docker-library/python](https://github.com/docker-library/python)
  * **Python Security Announcements:** [https://github.com/python/cpython/security/advisories](https://github.com/python/cpython/security/advisories)
  * **Debian Security Bug Tracker:** [https://security-tracker.debian.org/tracker/](https://security-tracker.debian.org/tracker/)

### 2.2 System Utilities Inside Container
* **Project:** `supervisor` (supervisord)
  * **Role:** Container init process and daemon manager.
  * **Advisories & Releases:** [https://github.com/Supervisor/supervisor/releases](https://github.com/Supervisor/supervisor/releases)
* **Project:** `curl`
  * **Advisories:** [https://curl.se/docs/security.html](https://curl.se/docs/security.html)
* **Project:** `sqlite3` (Embedded Database Engine)
  * **Role:** Write-Ahead Logging (WAL) state store.
  * **Release & Vulnerability Notes:** [https://www.sqlite.org/chronology.html](https://www.sqlite.org/chronology.html)

---

## 3. Reverse Proxy & Host Infrastructure

* **Project:** `Caddy` Web Server
  * **Role:** SSL/TLS certificate termination, HTTP/2 & HTTP/3 multiplexing, WebSocket reverse proxy.
  * **Security Advisories:** [https://github.com/caddyserver/caddy/security/advisories](https://github.com/caddyserver/caddy/security/advisories)
  * **Release Feed:** [https://github.com/caddyserver/caddy/releases](https://github.com/caddyserver/caddy/releases)

---

## 4. Supporting Python Libraries

| Library | Functionality | Upstream Advisory Tracker |
| :--- | :--- | :--- |
| `pydantic` / `pydantic-core` | Request schema validation | [pydantic Advisories](https://github.com/pydantic/pydantic/security/advisories) |
| `anyio` | Asynchronous concurrency abstraction | [anyio Advisories](https://github.com/agronholm/anyio/security/advisories) |
| `httpx` | Outbound HTTP client (Web Push dispatch) | [httpx Advisories](https://github.com/encode/httpx/security/advisories) |

---

## 5. Automated Vulnerability Scanning Workflow

Rather than manual tracking, use the automated scanners below in local maintenance and CI/CD:

### 5.1 Python Package Audit (`pip-audit`)
Audit installed Python packages against the PyPA Advisory Database and OSV:
```bash
pip install pip-audit
pip-audit -r requirements.txt
```
* **PyPA Advisory Database:** [https://github.com/pypa/advisory-database](https://github.com/pypa/advisory-database)
* **Open Source Vulnerabilities (OSV):** [https://osv.dev/](https://osv.dev/)

### 5.2 Container Image CVE Scan (`Trivy`)
Scan the built Docker container for known OS and Python CVEs:
```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:latest image alliance:latest
```

### 5.3 Automated Dependency Updates in Forgejo (Renovate)
Configure **Renovate** on your Forgejo instance to automatically open Pull Requests when new versions of Python libraries, Debian packages, or base Docker images are released:
* [https://docs.renovatebot.com/modules/platform/gitea/](https://docs.renovatebot.com/modules/platform/gitea/) (Compatible with Forgejo).

---

## 6. Recommended Routine Maintenance Schedule

1. **Weekly:** Rebuild Docker container to pull Debian security patches (`apt-get update && apt-get upgrade`).
2. **Monthly:** Run `pip-audit -r requirements.txt` and update libraries in `requirements.txt`.
3. **Immediate:** Patch and rebuild when a high/critical CVE is announced for `Pillow`, `cryptography`, `fastapi`, or `uvicorn`.
