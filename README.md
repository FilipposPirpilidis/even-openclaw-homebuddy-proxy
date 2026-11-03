# Even OpenClaw HomeBuddy Proxy

This project connects the AI feature in the Even Realities app to an OpenClaw
assistant that is already available through Telegram.

It is a small HTTP service with an OpenAI-compatible endpoint. A question from
the glasses is sent to a Telegram bot using a real Telegram user session. The
service waits for the bot's final reply and returns that text to the glasses.

An optional Home Assistant route can handle a small set of voice commands
locally. It supports both exact phrases and an optional strict multilingual
intent grammar. Those commands do not go through Telegram or an AI model,
which makes common actions such as turning off a light noticeably faster.

## How it fits together

```text
Even glasses
    |
    v
Even Realities app
    |
    v
This proxy ---------------> Home Assistant
    |                       (optional exact commands)
    v
Telegram bot
    |
    v
OpenClaw
```

The proxy is deliberately locked to one Telegram target ID. It does not act as
a general Telegram client or accept arbitrary chat destinations.

## What it handles

- An OpenAI-compatible `POST /v1/chat/completions` endpoint
- A `POST /` compatibility endpoint for clients that only accept a base URL
- Model discovery through `GET /v1/models`
- Bearer-token authentication
- A persistent Telegram user session through Telethon
- Correlation of each request with the Telegram response that follows it
- Filtering of temporary messages such as `Working`, `Thinking`, and
  `Web Fetch: running`
- Waiting briefly for message edits before returning the final response
- Optional exact-match and strict intent-based Home Assistant commands
- Greek command normalization, including accents, case, final sigma, and
  speech-recognition punctuation
- Docker health checks and an unprivileged runtime user

The service is intentionally non-streaming. The glasses receive one final
answer after Telegram has settled.

## Requirements

- A Linux machine with Docker and Docker Compose. A Raspberry Pi 4 or newer is
  a good fit.
- An OpenClaw installation with its Telegram channel working.
- A Telegram bot connected to that OpenClaw channel.
- Telegram API credentials from [my.telegram.org](https://my.telegram.org).
- The phone running the Even Realities app must be able to reach the proxy.

Home Assistant is optional. It is not needed for the Telegram bridge.

## 1. Prepare the configuration

Clone the repository and create the local environment file:

```bash
git clone https://github.com/FilipposPirpilidis/even-openclaw-homebuddy-proxy.git
cd even-openclaw-homebuddy-proxy

cp .env.example .env
chmod 600 .env
```

Open `.env` and set at least:

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_TARGET=YourOpenClawBotUsername
PROXY_BEARER_TOKEN=replace_with_a_long_random_value
PROXY_BIND_ADDRESS=192.168.1.50
```

Generate a bearer token with:

```bash
openssl rand -hex 32
```

`PROXY_BIND_ADDRESS` should normally be the LAN address of the machine running
the service. Keeping it explicit avoids exposing the port on every interface.

## 2. Authorize the Telegram user session

Build the image and run the interactive login helper:

```bash
docker compose build
docker compose --profile login run --rm telegram-login
```

Enter your Telegram phone number, login code, and two-step password if Telegram
asks for it. The helper prints two IDs:

```text
Authorized Telegram user id: 123456789
Resolved Telegram target id: 987654321
```

Put the resolved target ID in `.env`:

```dotenv
TELEGRAM_TARGET_ID=987654321
```

The login session is stored in the Docker volume named `telegram-session`.
Treat that volume as a credential. Anyone who obtains the session file may be
able to use the Telegram account.

## 3. Check the OpenClaw Telegram channel

The Telegram bot must already be able to receive a message and return an
OpenClaw answer. Test that in the Telegram app before starting the proxy.

OpenClaw should reply directly to incoming Telegram messages. In current
OpenClaw releases, the relevant setting is:

```bash
openclaw config set channels.telegram.replyToMode all
```

If OpenClaw runs in Docker, execute the equivalent command inside its gateway
container. Configuration commands can change between releases, so use the
command documented by the OpenClaw version you have installed if this setting
has moved.

## 4. Start the proxy

```bash
docker compose up -d telegram-proxy
docker compose ps
docker compose logs --tail 100 telegram-proxy
```

The first start normally takes a few seconds while Telethon connects. Check the
health endpoint from another device on the same network:

```bash
curl http://192.168.1.50:18989/healthz
```

A healthy response looks like this:

```json
{
  "ok": true,
  "status": "live",
  "telegram_target_id": 987654321,
  "home_assistant_offline_commands": false,
  "offline_command_count": 0
}
```

## 5. Test the OpenAI-compatible endpoint

Run this from the proxy host, after loading the token from `.env`:

```bash
set -a
source .env
set +a

curl --max-time 240 -sS \
  http://${PROXY_BIND_ADDRESS}:${PROXY_PORT:-18989}/v1/chat/completions \
  -H "Authorization: Bearer ${PROXY_BEARER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "messages": [
      {
        "role": "user",
        "content": "Reply with exactly: proxy works"
      }
    ],
    "stream": false
  }'

unset PROXY_BEARER_TOKEN
```

The Telegram bot should receive the request, and its final reply should be in
the JSON response.

## 6. Add it to the Even Realities app

Create a custom AI provider or agent in the Even Realities app using:

- **Server URL:** `http://192.168.1.50:18989/v1/chat/completions`
- **API key:** the value of `PROXY_BEARER_TOKEN`
- **Model:** `openclaw`

The exact field names depend on the app release. Enter the complete endpoint,
including `/v1/chat/completions`; the Even Realities app does not append that
path automatically.

Start with a simple question. Keep the Telegram chat open for the first test so
you can see the request and the final OpenClaw response.

## Optional Home Assistant commands

Home Assistant routing is useful for short, predictable commands that should
not wait for Telegram. It is an allowlist, not a natural-language parser.

When a user message matches a configured phrase, the proxy calls the specified
Home Assistant service and returns the configured response. Messages that do
not match continue to Telegram normally.

### Enable it

Create the local command file:

```bash
cp config/offline_commands.example.json config/offline_commands.json
chmod 644 config/offline_commands.json
```

Edit its Home Assistant URL and entity IDs. Then add a Home Assistant long-lived
access token to `.env`:

```dotenv
HOME_ASSISTANT_TOKEN=your_long_lived_access_token
```

Keep the token in `.env`; do not put it in the JSON file.

Restart the service after changing the command file:

```bash
jq -e . config/offline_commands.json >/dev/null
chmod 644 config/offline_commands.json
docker compose up -d --force-recreate telegram-proxy
```

The container runs as an unprivileged user, so the JSON file must be readable.
If the service reports `Permission denied: /config/offline_commands.json`, run
the `chmod 644` command again. Some editors replace a file and reset its
permissions when saving.

### Command format

```json
"turn off the kitchen lights": {
  "domain": "light",
  "service": "turn_off",
  "service_data": {
    "entity_id": "light.kitchen"
  },
  "response": "Kitchen lights turned off."
}
```

Useful Home Assistant service combinations include:

- `light.turn_on` and `light.turn_off`
- `switch.turn_on` and `switch.turn_off`
- `script.turn_on`

Scripts should be run with `script.turn_on`. Avoid retrying or treating scripts
as toggles, since a script may perform a non-idempotent action.

### Matching rules

Matching is exact after normalization:

- Leading, trailing, and repeated spaces are normalized.
- Uppercase and lowercase are equivalent.
- Greek accents are ignored.
- Greek `ς` and `σ` are equivalent.
- Trailing punctuation such as `.`, `,`, `!`, `?`, and `;` is ignored.

For example, these normalize to the same command:

```text
Άναψε όλα τα φώτα
άναψε όλα τα φώτα
ΑΝΑΨΕ ΟΛΑ ΤΑ ΦΩΤΑ
αναψε ολα τα φωτα
Άναψε όλα τα φώτα!
```

Different words are still different commands. `σβήσε το φως` does not
automatically mean the same thing as `κλείσε το φως`; add both phrases if both
should work. Internal spaces also matter, so `μικρόφως` and `μικρό φως` are
separate phrases.

The service rejects two phrases that become identical after normalization.
This catches accidental duplicates during startup rather than selecting an
action unpredictably.

### Strict multilingual intent matching

For natural variations such as `άναψε το θερμοσίφωνα` and
`άναψε τον θερμοσίφωνα, σε παρακαλώ`, enable `intent_matching` in the JSON
file. Languages are data, not hard-coded behavior: add the action phrases,
articles, courtesy phrases, and entity aliases that Even's ASR may return.

The matcher requires the complete utterance to fit this grammar:

```text
[optional courtesy phrase] + action + [optional article] + entity
```

Courtesy phrases may also occur between the action and entity or at the end.
Punctuation, case, and accents are normalized. The matcher never searches for
action and entity words inside a longer sentence. For example:

- `Άναψε το θερμοσίφωνα, σε παρακαλώ` matches.
- `Could you turn on the water heater, please?` matches.
- `Πες μου γιατί πρέπει να ανάψω τον θερμοσίφωνα` does not match.
- `What happens if I turn on the water heater?` does not match.

Messages that do not fit the complete grammar continue to Telegram. This is
deliberately stricter than fuzzy or AI classification so a discussion about a
device cannot accidentally control it. See
`config/offline_commands.example.json` for a bilingual example.

`config/intent_matching.homebuddy.example.json` contains a larger HomeBuddy
example covering lights, a water heater, TV controls, cooking scripts, and a
garage command. It can be merged into an existing command file without
replacing the existing exact `commands` object.

For `light` and `switch` `turn_on`/`turn_off` calls, a transient Home Assistant
server failure is retried once. Scripts are never retried. If Home Assistant
still fails, the proxy returns the configured `error_response` to the glasses
instead of a generic HTTP error page.

## Operations

Follow the logs:

```bash
docker compose logs --follow --timestamps telegram-proxy
```

Restart after an environment change:

```bash
docker compose up -d --force-recreate telegram-proxy
```

Rebuild after a code update:

```bash
git pull
docker compose build telegram-proxy
docker compose up -d --force-recreate telegram-proxy
```

Back up the Telegram session volume before moving the installation to another
machine.

## Troubleshooting

### The container keeps restarting

Read the startup error:

```bash
docker compose ps -a
docker compose logs --tail 200 telegram-proxy
```

Common causes are a missing Telegram target ID, an unauthorized session, an
invalid JSON file, a duplicate normalized Home Assistant command, or unreadable
configuration permissions.

### The glasses show “AI server error”

Check whether the request reached the proxy and which status it returned:

```bash
docker compose logs --since 5m --timestamps telegram-proxy
```

A `401` means the bearer token in the app does not match `.env`. A `503` usually
means Telegram was not ready. A `504` means the Telegram bot did not produce a
stable response before the configured timeout.

### A temporary Telegram status appears as the answer

The proxy filters the common OpenClaw status formats. If a new status format is
introduced, add it to `is_transient_status` in `app/telegram_bridge.py` and add
a regression test in `tests/test_telegram_bridge.py`.

### A Home Assistant phrase goes to Telegram

The phrase did not match the allowlist after normalization. Look for the
`Offline Home Assistant command matched` log line. If it is absent, add the
spoken wording as another explicit command.

## Development

Create a virtual environment and run the tests:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The code intentionally keeps the Home Assistant router separate from the
Telegram bridge. Changes to local command matching should not affect Telegram
correlation, and vice versa.

## Security notes

- Run the service on a trusted network or behind a properly authenticated HTTPS
  reverse proxy.
- Do not expose port `18989` directly to the public internet.
- Keep `.env`, the Home Assistant token, and the Telegram session out of Git.
- Use a long random bearer token.
- The Home Assistant JSON is an explicit action allowlist. Review changes before
  restarting the service.

## License

MIT
