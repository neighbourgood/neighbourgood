# WhatsApp bot

Many neighbourhoods and apartment buildings already organise themselves in a WhatsApp group, and plenty of neighbours will rarely or never open the app. The WhatsApp bot brings NeighbourGood into that group: people can search, share, borrow, ask for help and follow Red Sky without leaving WhatsApp.

## Read this first

WhatsApp has no official way for a bot to sit in an existing group. NeighbourGood therefore uses **[WAHA](https://waha.devlike.pro)**, a self-hosted bridge that logs in to WhatsApp Web like a linked device.

- **It is unofficial and against WhatsApp's terms of service.** WhatsApp can block the number at any time, especially if it looks like spam.
- **Use a spare number** (a cheap prepaid SIM) for the bot, never your personal one. The phone only has to be set up once; after pairing it can stay in a drawer, but it must stay switched on and online at least every few days.
- NeighbourGood keeps the risk low: the bot only answers when addressed, never writes privately to someone who hasn't written to it first, paces its messages (1.5–3 s apart, with a typing indicator, at most 20 per chat and hour) and backs off when WhatsApp throttles it.

If that trade-off is not acceptable for your community, keep using the app and [Telegram](telegram-setup.md).

## Set it up

### With the installer

The installer asks about the bot as a separate step at the end. To add it later on an existing install:

```bash
curl -fsSL https://raw.githubusercontent.com/neighbourgood/neighbourgood/main/install.sh | bash -s -- --whatsapp
```

`NG_WHATSAPP=0` skips the question, `NG_WHATSAPP=1` answers yes without asking (for scripted installs).

It writes these lines to `.env` and starts the bridge container:

```
NG_WHATSAPP_API_KEY=<random>
NG_WHATSAPP_WEBHOOK_SECRET=<random>
NG_WHATSAPP_BRIDGE_URL=http://waha:3000
COMPOSE_PROFILES=whatsapp
```

### By hand

Add the same four lines to `.env` (generate both secrets with `openssl rand -hex 32`) and run `docker compose up -d`. The `waha` service in `docker-compose.yml` only starts with the `whatsapp` profile; it has no published port, so only the backend can reach it. On ARM servers, pick a matching tag from Docker Hub and set it as `NG_WAHA_IMAGE`.

Running WAHA somewhere else: point `NG_WHATSAPP_BRIDGE_URL` at it, and configure its webhook to `https://<your-instance>/whatsapp/webhook` with the events `message` and `NG_WHATSAPP_WEBHOOK_SECRET` as the HMAC key (`WHATSAPP_HOOK_URL`, `WHATSAPP_HOOK_EVENTS`, `WHATSAPP_HOOK_HMAC_KEY`). Calls without a valid signature are rejected.

### Pair the phone

1. Log in to NeighbourGood with a platform admin account (an email listed in `NG_ADMIN_EMAILS`).
2. Go to **Settings → WhatsApp bridge → Start pairing**.
3. On the bot's phone, open WhatsApp → **Linked devices → Link a device**, and scan the QR code. It refreshes every 20 seconds.
4. The panel shows **Connected as +…**. The number is read from the phone; set `NG_WHATSAPP_BOT_NUMBER` only if it is not shown.

### Link a neighbourhood group

1. Add the bot's number to the WhatsApp group.
2. In NeighbourGood, open the community → **Admin** tab → **WhatsApp group → Create link code**.
3. Post the message shown there (for example `!link K7Q2M9XD`) in the group within 30 minutes. The bot confirms.
4. Choose how often the group gets a summary: weekly (default), daily or off.

Each neighbour then writes `!join Their Name` in the group once. That creates a WhatsApp-only member of the community (see below).

## What neighbours can do in WhatsApp

In groups, commands start with `!` (or `/`); everything else in the group is ignored and never stored. In a private chat with the bot the `!` is optional. Command words work in all 12 app languages; English always works, and replies come in the member's language. `!help` (`!hilfe`, `!aide`, `!ayuda` …) lists them.

| Command (English / German) | What it does |
|---|---|
| `!help` / `!hilfe` | The command list |
| `!join Anna` / `!beitreten Anna` | Become a member of the group's community |
| `!search ladder` / `!suche Leiter` | Items to borrow and neighbours offering a skill, with numbers |
| `!lend ladder` / `!verleihe Leiter` | List an item (add a photo later in the app) |
| `!offer bike repair` / `!biete …`, `!need babysitter` / `!brauche …` | Skill offer or request |
| `!borrow 12` / `!leihen 12` | Ask to borrow item #12 for the next 3 days; `!borrow 12 24.12. 27.12.` for set dates |
| `yes 5` / `ja 5`, `no 5` / `nein 5`, `returned 5` / `zurück 5` | The lender answers request #5 (sent privately with the request) |
| `!remove 12` / `!weg 12` | Take your item off the list (`!remove S3` for skill S3) |
| `mine` / `meine` | Your items, skills and open requests (private chat only) |
| `!events` / `!termine`, `!rsvp 3` / `!dabei 3` | Upcoming events and saying you'll come |
| `!profile Anna` / `!profil Anna` | What a neighbour lends and can do |
| `!sos no water on 3rd floor` / `!notfall …` | Urgent help request (posted to the group right away) |
| `!ping …` / `!alarm …` | Emergency ping, Red Sky only |
| `!status` / `!lage` | Red Sky state and open emergency requests |
| `!vote` / `!abstimmen` | Vote to switch Red Sky on (or back off) |
| `account` / `konto` | A one-hour link to set an email and password (private chat only) |
| `stop` / `stopp`, `start` / `weiter` | Private messages from the bot off / on |
| `delete` / `löschen` | Delete a WhatsApp-only account, with a confirmation code |

When the instance has AI enabled (`NG_AI_PROVIDER`), linked members can also write in plain language in the private chat: *"does anyone have a drill?"*, *"what's happening?"*.

### What stays in the app, and why

The bot replies with a link to the app for these:

| In the app only | Why |
|---|---|
| Registering with email and password, login, password and email changes | Needs a secure form, not a chat message |
| Creating a community, invites, member roles and leaders, merging communities | Admin decisions that need the full picture |
| Linking or unlinking a WhatsApp group, the summary setting, pairing the bridge | Admin or leader only, protected by one-time codes |
| Photos, item condition and detailed edits, creating events | Forms work much better than chat for several fields |
| Reviews, trust badges, reputation, the booking calendar | Browsing, not quick actions |
| Direct messages between neighbours | The bot doesn't relay private conversations, so phone numbers stay hidden |
| Switching Red Sky on as an admin, triage and assigning emergency requests | Coordination needs the Emergency page; voting works in WhatsApp |
| Map, federation, mesh, webhooks, data export, settings | Not chat-shaped |

## Accounts and privacy

- **WhatsApp-only members.** `!join` creates an account with only the WhatsApp ID and the given name. It has no email or password and can't log in to the app. Other members see the name, never the phone number.
- **Moving to the app.** `account` in a private chat sends a one-hour link where the member sets an email and password; everything they shared stays theirs. Someone who already has an app account links WhatsApp under **Settings → WhatsApp** instead. If they had joined in WhatsApp before, the two accounts are merged.
- **Deleting.** `delete` removes a WhatsApp-only account after a confirmation code (the same deletion as in the app). Accounts with an app login are deleted in the app.
- **What is stored.** WhatsApp IDs, names and what members explicitly share through commands. Group conversations are not read beyond checking whether a message starts with `!`, and no message text is saved.

## What the group sees

| When | What |
|---|---|
| Red Sky switches on | Immediately, with how to ask for help |
| An emergency request with high or critical urgency | Immediately |
| Everything else (new items, skills, events, new neighbours) | In the daily or weekly summary, only if something happened |

Private messages go to members who have written to the bot at least once and haven't sent `stop`: borrow requests (with the `yes`/`no` answer), answers to their own requests, assigned emergency requests, and Red Sky for communities without a linked group.

## Troubleshooting

| Problem | What to do |
|---|---|
| Settings shows no WhatsApp bridge panel | You need the platform admin role, and `NG_WHATSAPP_BRIDGE_URL` must be set (restart the backend after changing `.env`) |
| "WhatsApp bridge not reachable" | `docker compose ps` should list `ng-waha`; check `docker compose logs waha` |
| The bot doesn't answer in the group | Is the group linked (Admin tab)? Did the message start with `!`? `docker compose logs backend` shows rejected webhook calls (wrong `NG_WHATSAPP_WEBHOOK_SECRET`) |
| Lenders don't get requests privately | They have to write to the bot once (any message), and not have sent `stop` |
| Status `FAILED` after pairing | Press **Start pairing** again. If WhatsApp blocked the number, there is no way back; pair a new one |

To turn the bot off, remove `COMPOSE_PROFILES` and `NG_WHATSAPP_BRIDGE_URL` from `.env`, then run `docker compose --profile whatsapp rm -sf waha && docker compose up -d`.
