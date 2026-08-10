# Deployment Guide

The bot runs as a single long-polling container. It makes only **outbound**
connections, so there is **no DNS, no TLS certificate, no reverse proxy, and no
inbound firewall port** to configure. Any small Linux host with Docker works —
a $5 VPS, a home server, or a Raspberry Pi.

Compose startup order is: postgres + redis → **config-compiler** →
ctrader-engine → bot. The compiler writes the secret-safe
`ResolvedRuntimeManifest` for shadow ENV/manifest parity. Live cTrader
authority remains ENV in this generation of the stack.

PR3 introduces symbol-routed multi-instrument runtime support while keeping
**XAU as the only production live instrument**.

**PR4** flips cTrader authority to the resolved runtime manifest:

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

ENV retains secrets and bootstrap only. See
`docs/configuration/manifest-authority-cutover.md`.

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

Fill in:

```
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
TELEGRAM_OWNER_ID=<your numeric user id>
ANTHROPIC_API_KEY=sk-ant-...                 # chart analysis (optional)
DB_PATH=/data/signals.db
LOG_LEVEL=INFO
```

## 4. Launch

```bash
docker compose up -d --build
docker compose ps                 # 'bot' is Up
docker compose logs -f bot
```

Expected startup lines:

```
bot: DB ready at /data/signals.db
bot: Starting Telegram polling
```

For autonomous execution on the broker-confirmed demo account, use the
[demo evaluation runbook](demo-eval-autotrade.md). It includes the complete
profile contract, live-account fail-closed behavior, and Redis evidence
commands.

## Technique pack (killzone + sweep/body + HTF PD + SL hard-cap)

After shipping an image that includes `execution.technique`, host
`config/trading-bot.yml` (Ansible `apexvoid_trading_bot_config`) should keep:

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
    hard_cap_group_stop: true
  activation:
    mode: enforce
```

Smoke after deploy:

- HFS discovery outside killzone (UTC 01/03/05/…) logs empty permits / no Impulse spam.
- Key Level inside London/NY/late-NY killzone with `sweep_reclaim`/`body_close` still publishes.
- Ladder stops that would soft-max past 60 furthest log
  `stop_exceeds_envelope_furthest_leg`.

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
