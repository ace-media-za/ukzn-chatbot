# Deploying the UKZN Chatbot (FastAPI) on DigitalOcean

Local project: `/Users/anele/MAEN/python/ukzn-chatbot`

The app is a **FastAPI** RAG API:
- `main.py` serves `POST /chat` and `GET /health`
- `app/config.py` reads `.env` from the **current working directory**
- It needs **PostgreSQL + pgvector** and **Ollama** with **two** models: `qwen2.5:1.5b` (chat) and `nomic-embed-text` (embeddings)

Apache can't run it directly. The app runs as its own service on `127.0.0.1:8000`, and Apache forwards `/demos/chat/api/` to it.

```
Browser ──HTTPS──► Apache (/demos/chat/api/) ──► FastAPI 127.0.0.1:8000 ──► Postgres (localhost:5432)
                                                                        └──► Ollama droplet (10.x.x.x:11434)
```

**Placeholders used below**

| Placeholder | Meaning |
|---|---|
| `APP_PUBLIC_IP` | Public IP of the App + DB droplet |
| `OLLAMA_PRIVATE_IP` | Private VPC IP of the Ollama droplet (`10.x.x.x`) |
| `YOUR_DB_PASSWORD` | Password of the `appuser` Postgres role |
| `YOUR_LOCAL_DB` | Name of the database on your Mac that holds the ingested tables |
| `YOUR_DOMAIN` | The domain Apache serves |

---

## ⚠️ 0. Move the code out of `/var/www/html`

Everything under `/var/www/html` is a public web folder. Anyone can download `https://your-site/demos/chat/.env`, which contains your database password, plus all your source code.

```bash
sudo mv /var/www/html/demos/chat /opt/ukzn-chatbot
```

Apache will still serve the app at `/demos/chat/...` through the forwarding rule in step 8. The files just won't be public anymore.

---

## 1. App droplet: system packages

```bash
python3 --version          # needs 3.11+ (Ubuntu 24.04 ships 3.12)
psql --version             # note the major version, e.g. 16
sudo apt update
sudo apt install -y python3-venv python3-pip postgresql-16-pgvector   # match your Postgres major
```

If `python3` is 3.10 (Ubuntu 22.04), install 3.11 or newer from the deadsnakes PPA first.

---

## 2. Copy the code (if you need to resync from your Mac)

Run this on your **Mac**, from the project folder. It leaves out the virtualenv, caches, logs and your local `.env`:

```bash
cd /Users/anele/MAEN/python/ukzn-chatbot
rsync -avz --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude .idea --exclude .git --exclude '*.log' --exclude .env \
  ./ root@APP_PUBLIC_IP:/opt/ukzn-chatbot/
```

---

## 3. Virtualenv and dependencies (App droplet)

```bash
cd /opt/ukzn-chatbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 4. `.env` on the server

```bash
sudo nano /opt/ukzn-chatbot/.env
```

```dotenv
DATABASE_URL=postgresql://appuser:YOUR_DB_PASSWORD@localhost:5432/appdb
OLLAMA_HOST=http://OLLAMA_PRIVATE_IP:11434
OLLAMA_CHAT_MODEL=qwen2.5:1.5b
OLLAMA_EMBED_MODEL=nomic-embed-text
```

```bash
sudo chown -R root:www-data /opt/ukzn-chatbot
sudo chmod 640 /opt/ukzn-chatbot/.env      # only root and the service can read it
```

---

## 5. Database: schema and knowledge base

Creating the extension needs a superuser. Everything after that runs as `appuser`:

```bash
sudo -u postgres psql -d appdb -c "CREATE EXTENSION IF NOT EXISTS vector;"
cd /opt/ukzn-chatbot
psql "postgresql://appuser:YOUR_DB_PASSWORD@localhost:5432/appdb" -f sql/schema.sql
```

### Load the data

Copying your already-ingested tables from your Mac is much faster than re-crawling. Re-ingesting would take hours on CPU embeddings.

On your **Mac**:
```bash
pg_dump -Fc --no-owner -t corporaterelations -t main_ukzn \
  "postgresql://USER:PASS@localhost:5432/YOUR_LOCAL_DB" > kb.dump
scp kb.dump root@APP_PUBLIC_IP:/tmp/
```

On the **App droplet**:
```bash
pg_restore --no-owner --data-only -d "postgresql://appuser:YOUR_DB_PASSWORD@localhost:5432/appdb" /tmp/kb.dump
rm /tmp/kb.dump
```

> Your Mac runs Postgres 16. If the droplet's `psql --version` is **older than 16**, `pg_restore` can't read the dump. In that case dump as plain SQL instead (`-Fp`, then load it with `psql -f`).

Check the data arrived:
```bash
psql "postgresql://appuser:YOUR_DB_PASSWORD@localhost:5432/appdb" \
  -c "SELECT count(*) FROM corporaterelations;" -c "SELECT count(*) FROM main_ukzn;"
```

---

## 6. Ollama droplet: the second model and one setting change

```bash
ollama pull nomic-embed-text
sudo systemctl edit ollama        # change OLLAMA_MAX_LOADED_MODELS to 2
sudo systemctl restart ollama
ollama list                       # both qwen2.5:1.5b and nomic-embed-text listed
```

**Why `2`:** every `/chat` request embeds the question first and then generates the answer. With `1`, Ollama would unload and reload a model on **every** request, adding seconds of delay. The embedding model only uses about 300 MB.

Check that the App droplet can reach Ollama (run on the **App** droplet):
```bash
curl http://OLLAMA_PRIVATE_IP:11434/api/tags
```

---

## 7. Run FastAPI as a service (App droplet)

```bash
sudo nano /etc/systemd/system/ukzn-chatbot.service
```

```ini
[Unit]
Description=UKZN Chat API (FastAPI)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=www-data
Group=www-data
# config.py reads ".env" relative to this directory
WorkingDirectory=/opt/ukzn-chatbot
ExecStart=/opt/ukzn-chatbot/.venv/bin/fastapi run main.py --host 127.0.0.1 --port 8000 --root-path /demos/chat/api
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ukzn-chatbot
systemctl status ukzn-chatbot
curl http://127.0.0.1:8000/health      # {"status":"ok","ollama_missing_models":[]}
```

- `--host 127.0.0.1` means only Apache can reach the app, not the internet.
- One worker is enough, because Ollama is the bottleneck.
- `--root-path` makes `/docs` work behind the `/demos/chat/api` prefix.

---

## 8. Apache: forward `/demos/chat/api/` to FastAPI

```bash
sudo a2enmod proxy proxy_http
```

Add this inside your site's `<VirtualHost>` (e.g. `/etc/apache2/sites-enabled/000-default.conf`, plus the `-le-ssl.conf` file if you use HTTPS):

```apache
ProxyPreserveHost On
ProxyTimeout 180
# flushpackets=on streams tokens to the browser as they're generated
ProxyPass        /demos/chat/api/ http://127.0.0.1:8000/ flushpackets=on
ProxyPassReverse /demos/chat/api/ http://127.0.0.1:8000/
```

```bash
sudo apache2ctl configtest && sudo systemctl reload apache2
```

---

## 9. Test from your Mac

```bash
curl https://YOUR_DOMAIN/demos/chat/api/health

curl -N https://YOUR_DOMAIN/demos/chat/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Who is the UKZN vice-chancellor?"}]}'
```

- The API docs are at `https://YOUR_DOMAIN/demos/chat/api/docs`.
- If you have a chat front end, point it at `/demos/chat/api/chat`.
- Confirm the old public path no longer exposes files: `curl -I https://YOUR_DOMAIN/demos/chat/.env` should return **404**.

---

## 10. Deploying updates later

```bash
# Mac: run the rsync from step 2, then on the App droplet:
cd /opt/ukzn-chatbot && .venv/bin/pip install -r requirements.txt
sudo systemctl restart ukzn-chatbot
journalctl -u ukzn-chatbot -f          # logs
```

---

## 11. Before sharing the URL publicly

The project README already says the API has **no authentication or rate limiting**, and every `/chat` call costs an embedding plus a CPU generation. At minimum:

- **HTTPS:**
  ```bash
  sudo apt install -y certbot python3-certbot-apache
  sudo certbot --apache
  ```
- **Rate limiting:** for example `mod_evasive`, or Cloudflare in front of the site. A few people sending messages in a loop will tie up Ollama for everyone else.
- **CORS:** if the front end is on another domain, allow only that domain.
- **Firewall (App droplet):**
  ```bash
  sudo ufw allow OpenSSH
  sudo ufw allow "Apache Full"
  sudo ufw enable
  ```
  Ports 8000 (FastAPI) and 5432 (Postgres) stay closed because both listen on localhost only.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| Service crashes: validation error | `.env` missing or unreadable. Check `WorkingDirectory` and `chmod 640` / group `www-data` |
| `/health` → connection error | Ollama unreachable. Test `curl http://OLLAMA_PRIVATE_IP:11434` from the App droplet, and check the UFW rule on the Ollama droplet |
| `ollama_missing_models` not empty | `ollama pull <name>` on the Ollama droplet |
| `type "vector" does not exist` | Step 5's `CREATE EXTENSION` as `postgres` |
| Every question gets "couldn't find" | Tables are empty, so the data restore didn't run |
| 503 from Apache | Service down: `systemctl status ukzn-chatbot` |
| Answer arrives all at once, not streamed | `flushpackets=on` is missing from `ProxyPass` |
| `ERROR: Bad source address` in UFW | Use the bare private IP, with no quotes or `/20` |
| Service in `activating (auto-restart)`, `status=3` | Startup failed. See 12.1 |
| Log: `failed to resolve host 'host'` | Placeholder hostname in `DATABASE_URL`. See 12.2 |
| `relation "main_ukzn" does not exist` | The app and DBeaver are on different databases. See 12.3 and 12.4 |
| `database "main_ukzn" does not exist` | `main_ukzn` is a table, not a database. See 12.5 |

### Database fixes (issues hit during the real deploy)

My actual setup: database **`ukzn_kb`**, app user **`ace_user`**.

```dotenv
DATABASE_URL=postgresql://ace_user:YOUR_PASSWORD@localhost:5432/ukzn_kb
```

### 12.1 Service crashes on start (`status=3`)

```
Active: activating (auto-restart) (Result: exit-code)
Process: ... ExecStart=... fastapi run main.py ... (code=exited, status=3)
```

**Exit code 3** means FastAPI loaded, but the startup step in `main.py` (loading settings, the Ollama client and the DB pool) failed. `systemctl status` hides the real error. Get it with:

```bash
journalctl -u ukzn-chatbot -n 60 --no-pager
```

Or run in the foreground as the service user:

```bash
cd /opt/ukzn-chatbot
sudo systemctl stop ukzn-chatbot
sudo -u www-data .venv/bin/fastapi run main.py --host 127.0.0.1 --port 8000
```

| Error in the log | Fix |
|---|---|
| `ValidationError ... database_url Field required` or `PermissionError ... .env` | `www-data` can't read `.env`: `sudo chown root:www-data /opt/ukzn-chatbot/.env && sudo chmod 640 /opt/ukzn-chatbot/.env` |
| `ValidationError ... Input should be a valid URL` | Typo in `.env`: remove quotes, and URL-encode special characters in the password (`@` → `%40`, `#` → `%23`, `:` → `%3A`, `/` → `%2F`) |
| `ModuleNotFoundError: No module named 'app'` | `WorkingDirectory=/opt/ukzn-chatbot` missing in the unit file |
| `[Errno 98] address already in use` | `sudo ss -ltnp \| grep 8000` |

Quick checks:

```bash
ls -l /opt/ukzn-chatbot/.env                       # want: -rw-r----- root www-data
sudo -u www-data cat /opt/ukzn-chatbot/.env > /dev/null && echo readable
grep -c '^DATABASE_URL=' /opt/ukzn-chatbot/.env   # want: 1
```

> A wrong DB password or an unreachable Ollama does **not** cause this crash. `app/db.py` opens the pool without waiting, so those errors only show up later, on requests.

### 12.2 `failed to resolve host 'host'`

```
error connecting in 'pool-1': failed to resolve host 'host': [Errno -3] Temporary failure in name resolution
psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec
```

**Cause:** `DATABASE_URL` still contains the placeholder hostname `host` from `.env.example`.

**Fix:** Postgres is on the same droplet, so use `localhost`:

```dotenv
DATABASE_URL=postgresql://ace_user:YOUR_PASSWORD@localhost:5432/ukzn_kb
```

Test the exact URL the app uses before restarting:

```bash
psql "$(grep '^DATABASE_URL=' /opt/ukzn-chatbot/.env | cut -d= -f2-)" -c "SELECT count(*) FROM main_ukzn;"
sudo systemctl restart ukzn-chatbot
```

> `GET /health` returning **200** doesn't prove the database works, because it only checks Ollama. `POST /chat` is the real end-to-end test.

### 12.3 `relation "main_ukzn" does not exist`

The `psql` test failed, but `SELECT * FROM public.main_ukzn;` worked in DBeaver.

**Cause:** they were connected to **different databases**. DBeaver's URL was `jdbc:postgresql://localhost:5432/postgres`, so the tables had been created in the **`postgres`** database, while `.env` pointed at **`ukzn_kb`**.

**Diagnose:** find which databases have the table:

```bash
for db in $(sudo -u postgres psql -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate"); do
  echo "$db: $(sudo -u postgres psql -d "$db" -Atc "SELECT to_regclass('public.main_ukzn')")"
done
```

```
postgres: main_ukzn      ← tables are here
ukzn_kb:                 ← app points here (empty)
```

Also check which server DBeaver is on:

```sql
SELECT current_database(), current_user, version();
```

`apple-darwin` in the version means DBeaver is on your **Mac**, not the droplet, so check the SSH tab.

### 12.4 Move the tables from `postgres` into `ukzn_kb`

App data belongs in its own database, not the `postgres` maintenance database.

```bash
# 1. pgvector in the target DB
sudo -u postgres psql -d ukzn_kb -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 2. Copy tables (schema + data + indexes) from postgres → ukzn_kb
sudo -u postgres pg_dump -d postgres -Fc --no-owner -t corporaterelations -t main_ukzn -f /tmp/kb.dump
sudo -u postgres pg_restore --no-owner -d ukzn_kb /tmp/kb.dump
sudo rm /tmp/kb.dump
```

Then give the tables to the app user. Copied tables are owned by `postgres`, which `ace_user` can't read:

```sql
-- sudo -u postgres psql -d ukzn_kb
ALTER TABLE corporaterelations OWNER TO ace_user;
ALTER TABLE main_ukzn OWNER TO ace_user;
SELECT count(*) FROM main_ukzn;
SELECT count(*) FROM corporaterelations;
\dt          -- Owner column should show ace_user
\q
```

Owning a table gives full access, and the `id` sequences move with it, so both the API and `scripts/ingest.py` keep working.

Verify that the source and target counts match, and that the app user can read the data:

```bash
sudo -u postgres psql -d postgres -Atc "SELECT count(*) FROM main_ukzn"
sudo -u postgres psql -d ukzn_kb  -Atc "SELECT count(*) FROM main_ukzn"
psql "$(grep '^DATABASE_URL=' /opt/ukzn-chatbot/.env | cut -d= -f2-)" -c "SELECT count(*) FROM main_ukzn;"
```

Restart and test end to end:

```bash
sudo systemctl restart ukzn-chatbot
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Who is the UKZN vice-chancellor?"}]}'
```

Afterwards:
- **DBeaver:** in the Main tab, change Database from `postgres` to `ukzn_kb`.
- **Clean up (permanent):** only after the counts match and `/chat` works:
  ```bash
  sudo -u postgres psql -d postgres -c "DROP TABLE corporaterelations, main_ukzn;"
  ```

> **Quick alternative:** change `/ukzn_kb` to `/postgres` at the end of `DATABASE_URL`. It works immediately, but your app data stays mixed into the maintenance database.

### 12.5 `GRANT ... ON DATABASE main_ukzn` fails

```
postgres=# GRANT ALL PRIVILEGES ON DATABASE main_ukzn TO ace_user;
ERROR:  database "main_ukzn" does not exist
```

**Cause:** `main_ukzn` and `corporaterelations` are **tables** inside `ukzn_kb`, not databases. `ON DATABASE` only accepts names listed by `\l`.

**Fix:** make `ace_user` the owner of the tables, as in 12.4.

Useful `psql` commands:

| Command | Shows |
|---|---|
| `\l` | databases |
| `\c ukzn_kb` | switch database |
| `\dt` | tables in the current database, with owners |
| `\du` | roles/users |

| Error | Cause |
|---|---|
| `permission denied for table main_ukzn` | Table owned by `postgres`. Run the `ALTER TABLE ... OWNER TO ace_user` statements |
| `password authentication failed for user "ace_user"` | Wrong password in `DATABASE_URL`. Reset it: `ALTER USER ace_user PASSWORD '...';` |

---

## 13. Command cheat sheet

```bash
# App service
systemctl status ukzn-chatbot
sudo systemctl restart ukzn-chatbot
journalctl -u ukzn-chatbot -f

# Apache
sudo apache2ctl configtest
sudo systemctl reload apache2
tail -f /var/log/apache2/error.log

# Health checks
curl http://127.0.0.1:8000/health                 # on the App droplet
curl http://OLLAMA_PRIVATE_IP:11434/api/tags       # App droplet → Ollama
curl https://YOUR_DOMAIN/demos/chat/api/health     # from anywhere
```
