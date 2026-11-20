# Ollama Droplet Guide

A complete setup for a dedicated Ollama server on DigitalOcean that serves models privately to a separate App + Database droplet.

```
                 Internet
                    │  (SSH only, port 22)
                    ▼
┌──────────────────────────┐   private VPC (10.x.x.x)   ┌──────────────────────────┐
│  App + DB droplet        │ ─────────────────────────► │  Ollama droplet          │
│  Laravel / Node + Postgres│      port 11434            │  Ollama on 10.x.x.x:11434│
└──────────────────────────┘                            └──────────────────────────┘
        ▲
        │ HTTPS
   End users  (never talk to Ollama directly)
```

**Rules this guide follows**
- Ollama has **no authentication**, so it is never exposed on the public internet.
- The app reaches Ollama over the **private VPC network** only.
- Your laptop reaches Ollama through an **SSH tunnel** only.

---

## Table of contents
1. [Create the droplet](#1-create-the-droplet)
2. [First login and base hardening](#2-first-login-and-base-hardening)
3. [Install Ollama](#3-install-ollama)
4. [Choose and pull models](#4-choose-and-pull-models)
5. [Configure Ollama to listen on the private network](#5-configure-ollama-to-listen-on-the-private-network)
6. [Firewall](#6-firewall)
7. [Connect from the App droplet](#7-connect-from-the-app-droplet)
8. [Use it in your app (Laravel / Node)](#8-use-it-in-your-app)
9. [Connect from your laptop (SSH tunnel)](#9-connect-from-your-laptop-ssh-tunnel)
10. [Embeddings (optional, for search / RAG)](#10-embeddings-optional-for-search--rag)
11. [Performance tuning](#11-performance-tuning)
12. [Monitoring and logs](#12-monitoring-and-logs)
13. [Updating Ollama and models](#13-updating-ollama-and-models)
14. [Troubleshooting](#14-troubleshooting)
15. [Security checklist](#15-security-checklist)
16. [Command cheat sheet](#16-command-cheat-sheet)

---

## 1. Create the droplet

In the DigitalOcean panel, go to **Create → Droplets**:

| Setting | Value | Why |
|---|---|---|
| Region | **Same region as the App droplet** | Droplets only share a private VPC inside one region |
| VPC | Same VPC as the App droplet (the region default) | Required for private networking |
| Image | Ubuntu 24.04 LTS | Officially supported by Ollama's installer |
| Plan | General Purpose `g-4vcpu-16gb` (or larger) | 16 GB RAM fits models up to ~8B (Q4) |
| Authentication | **SSH key** (not password) | Passwords get brute-forced |
| Monitoring | ✅ Enable | Free CPU/RAM graphs and alerts |
| Hostname | e.g. `ollama-1` | |

### What this spec can run (CPU only)

| Model size (Q4) | RAM used | Approx. speed | Good for |
|---|---|---|---|
| 1–3B (`qwen2.5:1.5b`, `llama3.2:3b`) | 1–3 GB | ~10–20 tok/s | Classification, extraction, short answers |
| 7–8B (`qwen2.5:7b`, `llama3.1:8b`) | 5–6 GB | ~3–6 tok/s | General chat, single user |
| 13–14B | 9–10 GB | ~1–2 tok/s | Fits, but too slow for interactive use |
| 30B+ | — | — | Does not fit |

> Speeds are typical CPU figures, not benchmarks of this exact droplet. "Regular" vCPUs are shared, so expect variation.

---

## 2. First login and base hardening

From your Mac:
```bash
ssh root@OLLAMA_PUBLIC_IP
```

> **DBeaver / GUI tools:** they don't expand `~`. Use the full key path, e.g. `/Users/<you>/.ssh/id_ed25519`.

Update the system and enable automatic security updates:
```bash
apt update && apt upgrade -y
apt install -y unattended-upgrades curl htop
dpkg-reconfigure -plow unattended-upgrades   # choose "Yes"
```

### Optional: add swap as an out-of-memory safety net
16 GB is enough for models up to 8B, but a small swap file stops the kernel from killing Ollama during a memory spike.
```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h
```

### Optional: disable SSH password login
Only do this **after** confirming key login works (you're already logged in with your key):
```bash
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```
Keep your current session open and test a **new** `ssh` login in another terminal before closing it.

---

## 3. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This installs the `ollama` binary, creates an `ollama` system user and registers a `systemd` service that starts on boot.

Verify:
```bash
ollama -v
systemctl status ollama          # should be "active (running)"
curl http://127.0.0.1:11434      # "Ollama is running"
```

---

## 4. Choose and pull models

```bash
ollama pull qwen2.5:1.5b    # fast, small (~1 GB)
ollama pull qwen2.5:7b      # better quality, slower (~4.7 GB)
ollama list
```

Quick test in the terminal:
```bash
ollama run qwen2.5:1.5b "Explain what a REST API is in 2 sentences"
```
Interactive chat: `ollama run qwen2.5:1.5b`, then `/bye` to exit.

**Choosing a model**
- Start with the **smallest model that gives acceptable answers** for your task. On CPU, smaller means faster.
- `1.5b`–`3b`: tagging, classification, JSON extraction, short replies.
- `7b`–`8b`: summaries, general Q&A, chat.
- Test with your real prompts before you commit to one.

> Pull models **before** step 5. After step 5 the CLI needs the `OLLAMA_HOST` variable (see below).

---

## 5. Configure Ollama to listen on the private network

By default Ollama listens on `127.0.0.1` only, so nothing outside the droplet can reach it. We'll move it to the **private VPC IP** so the App droplet can connect while the public internet still can't.

### 5.1 Find the private IPs
Run on **both** droplets:
```bash
ip -4 addr show eth1 | grep inet
```
Or check the DO panel: **Droplet → Networking → Private IP**.

Write them down:
- `APP_PRIVATE_IP`, e.g. `10.114.0.2`
- `OLLAMA_PRIVATE_IP`, e.g. `10.114.0.3`

If `eth1` doesn't exist, or the IPs are in different `10.x` ranges, the droplets aren't in the same VPC. Go back to [step 1](#1-create-the-droplet).

### 5.2 Set the service configuration
```bash
sudo systemctl edit ollama
```
Paste this between the comment lines, replacing the IP:
```ini
[Service]
Environment="OLLAMA_HOST=10.114.0.3:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

| Variable | Value | Meaning |
|---|---|---|
| `OLLAMA_HOST` | private IP + port | Listen **only** on the private network, never on the public IP |
| `OLLAMA_KEEP_ALIVE` | `-1` | Keep the model in RAM forever. No 5–20s cold start on the first request. Fine on a dedicated box |
| `OLLAMA_NUM_PARALLEL` | `1` | One request at a time. On 4 vCPUs, parallel requests just slow each other down |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Avoids running out of RAM by loading two big models at once |

Apply:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
sudo ss -ltnp | grep 11434
```
✅ Expected: `10.114.0.3:11434`
❌ Wrong: `0.0.0.0:11434` or `*:11434`, which means Ollama is listening on the public interface too.

### 5.3 Make the CLI on this droplet work again
The `ollama` CLI connects to `127.0.0.1` by default, and the server isn't listening there anymore. Point the CLI at the private IP permanently:
```bash
echo 'export OLLAMA_HOST=10.114.0.3:11434' >> ~/.bashrc
source ~/.bashrc
ollama list        # should work again
```

---

## 6. Firewall

Allow SSH **first** so you don't lock yourself out, then allow Ollama **only** from the App droplet:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow from APP_PRIVATE_IP to any port 11434 proto tcp
sudo ufw enable
sudo ufw status verbose
```

Expected:
```
22/tcp (OpenSSH)    ALLOW IN    Anywhere
11434/tcp           ALLOW IN    10.114.0.2
```

### Optional extra layer: DigitalOcean Cloud Firewall
In **Networking → Firewalls → Create Firewall**, attach it to the Ollama droplet:
- Inbound SSH (22) from **your home/office IP only**
- Inbound TCP 11434 from the **App droplet** (select it by name or tag)
- Everything else denied

UFW protects the machine and the Cloud Firewall blocks traffic before it reaches the machine. Using both is defence in depth.

---

## 7. Connect from the App droplet

SSH into the **App** droplet and test:
```bash
# Health check
curl http://10.114.0.3:11434
# -> Ollama is running

# List models
curl http://10.114.0.3:11434/api/tags

# Native chat API
curl http://10.114.0.3:11434/api/chat -d '{
  "model": "qwen2.5:1.5b",
  "messages": [{ "role": "user", "content": "Say hello in 5 words" }],
  "stream": false
}'

# OpenAI-compatible API
curl http://10.114.0.3:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:1.5b",
    "messages": [{ "role": "user", "content": "Hello!" }]
  }'
```

From your **laptop**, `curl http://OLLAMA_PUBLIC_IP:11434` should **time out**. That confirms Ollama isn't exposed publicly.

---

## 8. Use it in your app

Keep the URL and model in configuration, never hard-coded.

### `.env` (App droplet)
```env
OLLAMA_BASE_URL=http://10.114.0.3:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT=120
```

### Laravel

`config/services.php`
```php
'ollama' => [
    'url'     => env('OLLAMA_BASE_URL'),
    'model'   => env('OLLAMA_MODEL', 'qwen2.5:1.5b'),
    'timeout' => (int) env('OLLAMA_TIMEOUT', 120),
],
```

`app/Services/OllamaClient.php`
```php
<?php

declare(strict_types=1);

namespace App\Services;

use Illuminate\Support\Facades\Http;

final class OllamaClient
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly string $model,
        private readonly int $timeout,
    ) {
    }

    public static function fromConfig(): self
    {
        return new self(
            config('services.ollama.url'),
            config('services.ollama.model'),
            config('services.ollama.timeout'),
        );
    }

    public function chat(string $prompt): string
    {
        return (string) Http::timeout($this->timeout)
            ->post("{$this->baseUrl}/v1/chat/completions", [
                'model'    => $this->model,
                'messages' => [['role' => 'user', 'content' => $prompt]],
            ])
            ->throw()
            ->json('choices.0.message.content');
    }
}
```

Bind it once in `AppServiceProvider::register()`:
```php
$this->app->singleton(OllamaClient::class, fn () => OllamaClient::fromConfig());
```

**Run LLM calls in a queue job, not inside the web request.** CPU inference takes seconds, and Ollama handles one request at a time.
```php
<?php

declare(strict_types=1);

namespace App\Jobs;

use App\Models\Document;
use App\Services\OllamaClient;
use Illuminate\Contracts\Queue\ShouldQueue;
use Illuminate\Foundation\Queue\Queueable;

final class SummariseDocument implements ShouldQueue
{
    use Queueable;

    public int $timeout = 180;
    public int $tries = 3;
    public array $backoff = [10, 30, 60];

    public function __construct(public readonly Document $document)
    {
    }

    public function handle(OllamaClient $ollama): void
    {
        $summary = $ollama->chat("Summarise in 3 bullet points:\n\n{$this->document->body}");

        $this->document->update(['summary' => $summary]);
    }
}
```
Run a **single** worker for the queue that talks to Ollama, so requests don't pile up:
```bash
php artisan queue:work --queue=ollama
```
and dispatch with `SummariseDocument::dispatch($doc)->onQueue('ollama');`.

### Node / TypeScript (OpenAI SDK)
```ts
import OpenAI from "openai";

const ollama = new OpenAI({
  baseURL: `${process.env.OLLAMA_BASE_URL}/v1`,
  apiKey: "ollama", // required by the SDK, ignored by Ollama
  timeout: 120_000,
});

export async function chat(prompt: string): Promise<string> {
  const res = await ollama.chat.completions.create({
    model: process.env.OLLAMA_MODEL ?? "qwen2.5:1.5b",
    messages: [{ role: "user", content: prompt }],
  });
  return res.choices[0]?.message.content ?? "";
}
```
For background work in Node, use a queue such as BullMQ with `concurrency: 1`.

### Streaming (chat UIs)
To show tokens as they're generated, set `"stream": true` (native API) or `stream: true` (OpenAI SDK) and forward the chunks to the browser over SSE. Long CPU responses feel much faster this way.

### Getting JSON back reliably
```bash
curl http://10.114.0.3:11434/api/chat -d '{
  "model": "qwen2.5:7b",
  "messages": [{"role":"user","content":"Extract name and city: John lives in Durban"}],
  "format": "json",
  "stream": false
}'
```
Small models still make mistakes, so **validate the JSON on your side** before saving it.

---

## 9. Connect from your laptop (SSH tunnel)

Ollama now listens on the private IP, so tunnel to **that** address through the droplet's public IP:
```bash
ssh -N -L 11434:10.114.0.3:11434 root@OLLAMA_PUBLIC_IP
```
Leave it running. On your Mac, `http://localhost:11434` now reaches the Ollama droplet:
```bash
curl http://localhost:11434/api/tags
```

> **Port clash:** if Ollama is also installed on your Mac, it already uses port 11434. Use a different local port:
> `ssh -N -L 11435:10.114.0.3:11434 root@OLLAMA_PUBLIC_IP`, then `http://localhost:11435`.

Any OpenAI-compatible desktop client (e.g. Open WebUI or Msty) can point at `http://localhost:11434/v1`.

---

## 10. Embeddings (optional, for search / RAG)

Pull an embedding model on the Ollama droplet:
```bash
ollama pull nomic-embed-text      # 768 dimensions
```
Call it from the App droplet:
```bash
curl http://10.114.0.3:11434/api/embed -d '{
  "model": "nomic-embed-text",
  "input": ["first text to embed", "second text"]
}'
```
Store the vectors in Postgres with **pgvector** on the App/DB droplet:
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id        BIGSERIAL PRIMARY KEY,
    body      TEXT NOT NULL,
    embedding VECTOR(768) NOT NULL
);

CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);

-- Top 5 most similar
SELECT id, body
FROM documents
ORDER BY embedding <=> $1
LIMIT 5;
```
> With `OLLAMA_MAX_LOADED_MODELS=1`, switching between the chat model and the embedding model forces a reload each time. If you use both a lot, set it to `2` (the embedding model is small, ~300 MB).

---

## 11. Performance tuning

| Problem | Fix |
|---|---|
| First request is slow (5–20s) | `OLLAMA_KEEP_ALIVE=-1` (step 5) keeps the model loaded |
| Long time before the first token | Prompt processing is slow on CPU. Shorten system prompts and send less context |
| Answers are slow | Use a smaller model (`1.5b`/`3b`) or set a lower `max_tokens` / `num_predict` |
| Requests time out in the app | Raise the client timeout to 120s or more and move calls into queue jobs |
| Many users at once | CPU can't scale this. Add a queue, or move to serverless inference or a GPU droplet |

Limit response length (native API):
```json
{ "model": "qwen2.5:1.5b", "messages": [...], "options": { "num_predict": 200 }, "stream": false }
```

**When to move off this box:** if the queue is constantly backed up or users wait more than ~10s, either upgrade to a CPU-optimised droplet with more vCPUs, or compare per-token serverless pricing with a GPU droplet's hourly price. Serverless usually wins unless traffic is steady and high.

---

## 12. Monitoring and logs

```bash
ollama ps                       # which model is loaded, RAM used
journalctl -u ollama -f         # live server logs
journalctl -u ollama --since "1 hour ago"
htop                            # CPU and RAM per process
free -h                         # memory and swap
df -h                           # disk (models live in /usr/share/ollama/.ollama/models)
```

In the DO panel, go to **Droplet → Graphs** and set **alert policies** (Monitoring → Create alert), for example CPU > 90% for 10 minutes or memory > 90%.

**Health check from the App droplet** (for uptime monitoring or a Laravel health check):
```bash
curl -fsS --max-time 5 http://10.114.0.3:11434/api/version
```

---

## 13. Updating Ollama and models

**Update Ollama.** Re-run the installer. Your `systemctl edit` overrides survive the update.
```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl restart ollama
ollama -v
```

**Update a model** (pulls the latest version of the same tag):
```bash
ollama pull qwen2.5:1.5b
```

**Remove unused models** to free disk:
```bash
ollama rm qwen2.5:7b
```

**Back up first:** take a DO **Snapshot** (Droplet → Snapshots) before big changes. It captures the OS, configuration and downloaded models.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl: (7) Failed to connect` from App droplet | Ollama not listening on the private IP | `ss -ltnp \| grep 11434`, then check `OLLAMA_HOST` in `systemctl cat ollama` |
| Connection **times out** from App droplet | UFW or Cloud Firewall blocking | `ufw status`, then check the allow rule uses the App droplet's **private** IP |
| `could not connect to ollama app` from the CLI | CLI still pointing at 127.0.0.1 | `export OLLAMA_HOST=10.114.0.3:11434` (step 5.3) |
| `model "x" not found` | Model not pulled, or name typo | `ollama list`, then `ollama pull <name>` |
| Ollama restarts or gets killed | Out of memory | Use a smaller model, add swap, set `OLLAMA_MAX_LOADED_MODELS=1` |
| Service won't start after edit | Typo in the override | `systemctl cat ollama`, then `journalctl -u ollama -n 50` |
| Can't SSH in after `ufw enable` | SSH wasn't allowed first | Use the DO **Recovery Console** (Droplet → Access), then `ufw allow OpenSSH` |
| DBeaver: `private key file does not exist: ~/.ssh/...` | GUI doesn't expand `~` | Use the full path `/Users/<you>/.ssh/id_ed25519` |
| No `eth1` / no private IP | Droplets in different regions or VPCs | Recreate the Ollama droplet in the App's region and VPC |

See the effective service configuration:
```bash
systemctl cat ollama
```

---

## 15. Security checklist

- [ ] `ss -ltnp | grep 11434` shows the **private** IP, not `0.0.0.0`
- [ ] UFW is enabled: SSH allowed, 11434 only from `APP_PRIVATE_IP`
- [ ] `curl http://OLLAMA_PUBLIC_IP:11434` from your laptop **times out**
- [ ] SSH key login only (password login disabled)
- [ ] Unattended security upgrades enabled
- [ ] End users never reach Ollama directly; the app is the only client
- [ ] The app validates and rate-limits user input before sending it to the model
- [ ] Model output is **escaped** before rendering in HTML (treat it as untrusted user input, since LLM output can contain injected HTML or JS)
- [ ] Model output that drives actions (SQL, shell, tool calls) is validated and never executed blindly
- [ ] Snapshot taken after setup is complete

---

## 16. Command cheat sheet

```bash
# Service
systemctl status ollama
sudo systemctl restart ollama
sudo systemctl edit ollama          # change env vars
systemctl cat ollama                # view effective config
journalctl -u ollama -f             # live logs

# Models
ollama list
ollama pull <model>
ollama rm <model>
ollama ps
ollama run <model> "prompt"

# Network
ip -4 addr show eth1                # private IP
sudo ss -ltnp | grep 11434          # what Ollama listens on
sudo ufw status verbose

# Tests (from App droplet)
curl http://OLLAMA_PRIVATE_IP:11434/api/tags
curl http://OLLAMA_PRIVATE_IP:11434/api/version

# Laptop tunnel
ssh -N -L 11434:OLLAMA_PRIVATE_IP:11434 root@OLLAMA_PUBLIC_IP
```
