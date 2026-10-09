<!-- v1.0.0 -->
# Deploying on one EC2

```
internet ─443─▶ nginx on the host (certbot TLS)
                 ├─ /               Angular build   /var/www/pos
                 ├─ /static/        collectstatic   /srv/pos/static
                 ├─ /media/         uploads         /srv/pos/media
                 └─ /api/ /admin/ ─▶ 127.0.0.1:8000 gunicorn (web container)
                                                     └─▶ db container (not published)
```

`docker-compose.prod.yml` runs Postgres and Django; `deploy/nginx/pos.conf`
is the nginx site. `/api/docs/` and `/api/schema/` answer 404 on the live
server — generate the Angular client from a local backend.

## First deploy

**1. The instance.** Ubuntu 24.04, 2 GB RAM or more (t3.small). Security
group inbound: 22 from your own IP only, 80 and 443 from anywhere — never 5432
or 8000. Give it an Elastic IP and point the domain's A record at it.

**2. Software.**

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu        # then log out and back in
```

**3. Code.**

```bash
git clone https://github.com/pisethprom-dk/FLL_pos_api.git pos_backend
cd pos_backend
```

**4. `.env`.** `cp .env.example .env`, then set:

```ini
DEBUG=0
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(50))">
ALLOWED_HOSTS=pos.example.com
CSRF_TRUSTED_ORIGINS=https://pos.example.com
CORS_ALLOWED_ORIGINS=
POSTGRES_PASSWORD=<openssl rand -hex 24>
DATABASE_URL=postgres://pos:<the same password>@db:5432/pos
AUTH_COOKIE_SECURE=True
GUNICORN_WORKERS=3
# plain `docker compose …` then means the production stack
COMPOSE_FILE=docker-compose.prod.yml
```

Then `chmod 600 .env`. The server refuses to start with `DEBUG=0` and no
`SECRET_KEY` or `DATABASE_URL`. Postgres takes its password only when its
volume is first created; changing it later means changing it inside Postgres
too.

**5. Folders.**

```bash
sudo mkdir -p /srv/pos/static /srv/pos/media /var/www/pos
sudo chown ubuntu:ubuntu /var/www/pos
```

**6. Start the backend.** Migrations and collectstatic run on every start.

```bash
docker compose up -d --build
docker compose exec web python manage.py seed
docker compose exec web python manage.py createsuperuser
```

Never run `seed_demo`, `seed_stock_demo` or `seed_sales_demo` here.

**7. The Angular app.** Build on your Mac (Node 22) and copy it up:

```bash
cd ../pos_frontend && npx ng build
rsync -a --delete dist/pos_frontend/browser/ ubuntu@<server>:/var/www/pos/
```

**8. nginx.**

```bash
sudo cp deploy/nginx/pos.conf /etc/nginx/sites-available/pos
sudo sed -i 's/pos.example.com/<your domain>/' /etc/nginx/sites-available/pos
sudo ln -s /etc/nginx/sites-available/pos /etc/nginx/sites-enabled/pos
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

**9. HTTPS.** `sudo certbot --nginx -d <your domain>` — it adds HTTPS and
the redirect from http, and renews by itself.

**10. Check.** `https://<your domain>` shows the sign-in page;
`https://<your domain>/api/docs/` is 404; `docker compose logs web` is quiet.

## Updating

```bash
cd ~/pos_backend && git pull && docker compose up -d --build
```

The frontend: build and `rsync` again, as in step 7.

## Backups

The database is in the Docker volume `pos-prod_pgdata`; uploads are in
`/srv/pos/media`. A nightly dump, kept off the instance (S3 or similar):

```bash
mkdir -p ~/backups
docker compose exec -T db pg_dump -U pos pos | gzip > ~/backups/pos-$(date +%F).sql.gz
```
