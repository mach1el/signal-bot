# Deployment Guide

The stack runs as Docker Compose services. It makes only **outbound**
connections, so there is **no DNS, no TLS certificate, no reverse proxy, and no
inbound firewall port** for the bot. Any small Linux host with Docker works.

Compose startup order: postgres + redis → **config-compiler** →
ctrader-engine → bot. The compiler writes the secret-safe
`ResolvedRuntimeManifest` to `/runtime/resolved-runtime.json`.

Production cTrader authority:

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

ENV retains secrets and bootstrap only. See
`docs/configuration/manifest-authority-cutover.md`.

Multi-symbol: production **live** on the demo account is XAU, EURUSD, and
GBPJPY. See `docs/runtime/multi-symbol-routing.md`.

## Prerequisites

- A host with Docker Engine + Compose v2.
- A Telegram account.
- (Optional) An Anthropic API key for chart analysis.
- About 15 minutes.

## 1. Host & Docker

Any Linux host will do. Install Docker's official packages (Compose v2 ships as
a plugin):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER" && newgrp docker
```

> The only inbound port the host needs is SSH (22). No 80/443. If you run a
> host firewall, you do **not** need to open anything for the bot.

## 2. Telegram credentials

1. **Bot token** — chat with `@BotFather`, `/newbot`, record the token.
2. **Channel** — create a private channel, add the bot as an administrator with
   **Post Messages** permission, and post any message in it.
3. **Chat ID** — visit `https://api.telegram.org/bot<TOKEN>/getUpdates`, find
   `chat.id` (a negative integer starting with `-100`).
4. **Owner ID** — DM `@userinfobot` (or check `getUpdates`) to get your numeric
   Telegram user ID. This locks DM commands to you.

## 3. Configure

```bash
git clone <repo-url> apexvoid-trading-bot
cd apexvoid-trading-bot
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in at minimum (see `.env.example` for the full generated contract):

```text
TELEGRAM_BOT_TOKEN=<from @BotFather>
SIGNAL_VIP_CHANNEL_ID=-100xxxxxxxxxx
TELEGRAM_OWNER_ID=<your numeric user id>
POSTGRES_PASSWORD=<secret>
DATABASE_URL=postgresql://apexvoid:<secret>@postgres:5432/signals
REDIS_URL=redis://redis:6379/0
CTRADER_CLIENT_ID=
CTRADER_CLIENT_SECRET=
CTRADER_ACCESS_TOKEN=
CTRADER_REFRESH_TOKEN=
CTRADER_ACCOUNT_ID=
ANTHROPIC_API_KEY=sk-ant-...                 # chart analysis (optional)
```

Non-secret strategy / actionability / execution tuning belongs in
`config/trading-bot.yml`, not duplicated as ad-hoc ENV.

## 4. Launch

```bash
docker compose up -d --build
docker compose ps                 # postgres, redis, engine, bot Up; compiler Exited 0
docker compose logs -f bot
docker compose logs -f ctrader-engine
```

Expected shape:

```text
config-compiler: wrote /runtime/resolved-runtime.json (exit 0)
ctrader-engine: feed heartbeat / bars writing
bot: Starting Telegram polling + scanner / ZoneWatch loops
```

For autonomous execution on the broker-confirmed demo account, use the
[demo evaluation runbook](demo-eval-autotrade.md). It includes the complete
profile contract, live-account fail-closed behavior, and Redis evidence
commands.

## Technique pack + ZoneWatch publish

After shipping images that include technique publishers and
`execution.technique`, keep host `config/trading-bot.yml` aligned with:

```yaml
actionability:
  entry_location:
    mode: enforce
execution:
  technique:
    enforce: true          # emergency off without code revert
    include_late_ny: true
    reaction_require_killzone: true
    hfs_require_killzone: true
    require_sweep_body: true
    strict_premium_discount: true
  activation:
    mode: enforce
```

Technique publisher flags (`AUTO_TRADE_TECHNIQUE_*`,
`AUTO_TRADE_CONFLUENCE_ZONE_ENABLED`) default on in schema. Operator guide:
[technique-zonewatch-publish.md](technique-zonewatch-publish.md).

Smoke after deploy:

- Technique ZoneWatches show non-zero chase and survive spot wicks (closed-bar
  invalidate only).
- HFS discovery outside killzone stays quiet when technique.enforce is on.
- Key Level inside London/NY/late-NY with accepted confirmation still publishes.
- Ladder stops past furthest envelope log `stop_exceeds_envelope_furthest_leg`.

## 5. Smoke test

DM your bot:

- `active` → `📋 No open signals.`
- `gold buy entry zone (4100-4105)` / `sl 4095` / `tp 10/20/30` → posts a
  formatted signal to the channel and replies `✅ Sent to channel (signal #1)`.
- (If configured) DM a chart screenshot → analysis is posted to the channel.

## Deployment Checklist

- [ ] Docker + Compose v2 installed; `docker run hello-world` works.
- [ ] Bot created, added to the channel as admin with Post Messages.
- [ ] `.env` populated (`600` perms) with bot token, chat id, owner id.
- [ ] (Charts) `ANTHROPIC_API_KEY` set.
- [ ] `docker compose up -d` brings `bot` Up; logs show polling started.
- [ ] DM `active` returns the empty-state reply.
