# 🏠 RealState Lead AI

<p align="center">
  <b>Telegram bot that turns raw real-estate channel text into structured, scored, deliverable leads.</b><br>
  Monitor channels → AI extracts the lead → score it → push it to your sales team.
</p>

---

## ✨ What it does

```
┌──────────────┐    ┌───────────────────┐    ┌──────────────┐    ┌───────────────┐
│  Telegram    │───▶│  RealState Bot     │───▶│  AI Extract  │───▶│  Score + Save │
│  channels &  │    │  (aiogram 3,       │    │  Mistral /   │    │  SQLite +     │
│  groups      │    │  admin)            │    │  Ollama /    │    │  forward 🔥   │
│              │    │                    │    │  regex       │    │               │
└──────────────┘    └───────────────────┘    └──────────────┘    └───────────────┘
```

- 📡 **Channel monitoring** — add the bot as admin to any real-estate channel/group; every post is ingested automatically (`channel_post` + group messages)
- 🧠 **AI lead extraction** — LLM parses free text into a strict JSON schema: deal type, property type, city, district, price, area, rooms, floor, contact, urgency
- 🔗 **Provider chain** — `Mistral API → local Ollama → regex fallback`, so the bot *never* goes blind even without any API key
- 🔢 **Rule-based scoring (0–100)** — phone contact, price, location, urgency and more; only leads above your threshold get forwarded
- 🗄 **SQLite persistence** — dedupe by message id **and** content hash (catches cross-channel reposts)
- 📤 **Lead delivery** — formatted HTML cards with deep-link to the original message
- 🛠 **CRM-lite commands** — stats, top leads, status pipeline (`new → contacted → won/lost`), CSV export

## 📬 Example output

> 🔥 **NEW LEAD** — score **85/100**
> 🏷 Sell · Apartment
> 📍 Dubai, Marina
> 💰 250,000 USD
> 📐 120 m² · 🛏 3 rooms · 🏢 5
> 📞 `+971 50 123 4567`
> ⚡ urgency: high
> 🔗 Source: Dubai Property Deals → https://t.me/dubaiproperty/4821

## 🚀 Quick start

### Local

```bash
git clone https://github.com/alihosseinabadi/realstate.git
cd realstate
cp .env.example .env          # fill in TELEGRAM token (+ optional keys)
pip install -r requirements.txt
python main.py
```

### Docker

```bash
cp .env.example .env          # fill in tokens
docker compose up -d --build
docker compose logs -f
```

### Wiring it up (2 minutes)

1. Create a bot with [@BotFather](https://t.me/BotFather) → copy token into `.env`
2. Add the bot as **admin** to your source channels (it needs to read posts)
3. Add the bot to your team channel/group → put its chat id in `DESTINATION_CHAT_ID`
4. Put your own user id in `ADMIN_IDS` to unlock admin commands
5. Done — leads start flowing on the next channel post 🎉

## ⚙️ Configuration

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM` | — | Bot token from @BotFather |
| `DESTINATION_CHAT_ID` | — | Where qualified leads are delivered |
| `SOURCE_CHANNELS` | — | Comma-separated chat ids to monitor (or use `/addchannel`) |
| `ADMIN_IDS` | — | User ids allowed to run admin commands |
| `AI_PROVIDER` | `auto` | `auto` \| `mistral` \| `ollama` \| `regex` |
| `MISTRAL_API_KEY` | — | [console.mistral.ai](https://console.mistral.ai/api-keys/) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1` | Model for the Ollama provider |
| `MIN_LEAD_SCORE` | `50` | Forward threshold (0–100) |
| `DB_PATH` | `data/realstate.db` | SQLite location |

## 🤖 Commands

| Command | Description |
|---|---|
| `/start` | Setup guide + command list |
| `/stats` | Total / hot leads, breakdown by status & deal type |
| `/leads [n]` | Top-n leads with scores |
| `/mark <chat_id>.<msg_id> <status>` | Move a lead through `new/contacted/won/lost/junk` |
| `/export` | Full CSV export of all leads |
| `/channels` | List monitored sources |
| `/addchannel <id>` / `/rmchannel <id>` | Manage sources at runtime |

## 🔢 How scoring works

| Signal | Points |
|---|:---:|
| Real estate detected | +15 |
| Deal type identified | +10 |
| Property type identified | +10 |
| **Phone contact** | **+30** |
| Price specified | +15 |
| Location specified | +10 |
| Area / rooms | +5 each |
| Urgency keywords ("urgent", "asap"…) | +10 |
| AI flags high urgency | +10 |

≥80 = 🔥 hot · ≥60 = ✅ good · below threshold = silently stored for later mining.

## 📁 Project structure

```
realstate/
├── main.py              # entrypoint — wiring + polling
├── config.py            # env configuration
├── ai/
│   ├── extractor.py     # Mistral → Ollama → regex chain
│   └── prompts.py       # strict JSON extraction prompt
├── bot/
│   └── handlers.py      # ingestion pipeline + commands
├── core/
│   ├── models.py        # Lead dataclass
│   ├── db.py            # SQLite store (dedupe, stats, CSV)
│   └── scoring.py       # rule-based scoring engine
├── Dockerfile / docker-compose.yml
└── .env.example
```

## 🛣 Roadmap

- [ ] Web dashboard for the lead pipeline
- [ ] Auto-reply to sellers with qualifying questions
- [ ] Multi-language listing support tuning
- [ ] Telegram Mini App CRM view
- [ ] Vector dedupe for near-duplicate listings

## 🙏 Credits

Evolved from my earlier [@alihosseinabadi/telegram_bot](https://github.com/alihosseinabadi/telegram_bot) experiment (aiogram + Mistral).

## 📬 Contact

Built by [@alihosseinabadi](https://github.com/alihosseinabadi) · hosseinabadiia@gmail.com
