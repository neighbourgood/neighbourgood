# API Endpoints

Full reference for the NeighbourGood REST API. Interactive docs available at `/docs` when the backend is running.

## Status

| Endpoint  | Method | Auth | Description                         |
| --------- | ------ | ---- | ----------------------------------- |
| `/status` | GET    | No   | Health check, version, current mode |

## Authentication

| Endpoint         | Method | Auth | Description               |
| ---------------- | ------ | ---- | ------------------------- |
| `/auth/register` | POST   | No   | Create account, returns JWT |
| `/auth/login`    | POST   | No   | Authenticate, returns JWT |
| `/auth/password-reset/request` | POST | No | Email a single-use reset link (valid 1 hour). Always 202 with the same body, whether or not the email exists |
| `/auth/password-reset/confirm` | POST | No | Set a new password with `{token, new_password}`; token works once; clears lockout |

## Users

| Endpoint                 | Method | Auth | Description                          |
| ------------------------ | ------ | ---- | ------------------------------------ |
| `/users/me`              | GET    | Yes  | Get current user profile             |
| `/users/me`              | PATCH  | Yes  | Update profile (name, neighbourhood) |
| `/users/me`              | DELETE | Yes  | Delete (anonymise) your account; body `{password}`, 400 on wrong password |
| `/users/me/reputation`   | GET    | Yes  | Get your reputation score            |
| `/users/{id}/reputation` | GET    | No   | Get a user's reputation score        |

## Resources

| Endpoint                         | Method | Auth | Description                        |
| -------------------------------- | ------ | ---- | ---------------------------------- |
| `/resources`                     | GET    | No   | List resources (search, filter)    |
| `/resources`                     | POST   | Yes  | Create a new resource listing      |
| `/resources/{id}`                | GET    | No   | Get resource details               |
| `/resources/{id}`                | PATCH  | Yes  | Update resource (owner only)       |
| `/resources/{id}`                | DELETE | Yes  | Delete resource (owner only)       |
| `/resources/categories`          | GET    | No   | List categories with labels/icons  |
| `/resources/{id}/image`          | POST   | Yes  | Upload resource image (owner only) |
| `/resources/{id}/image`          | GET    | No   | Serve resource image               |

## Bookings

| Endpoint                              | Method | Auth | Description                        |
| ------------------------------------- | ------ | ---- | ---------------------------------- |
| `/bookings`                           | POST   | Yes  | Request to borrow a resource       |
| `/bookings`                           | GET    | Yes  | List your bookings (role/status)   |
| `/bookings/{id}`                      | GET    | Yes  | Get booking details                |
| `/bookings/{id}`                      | PATCH  | Yes  | Update booking status              |
| `/bookings/resource/{id}/calendar`    | GET    | No   | Calendar view of resource bookings |

## Messages

| Endpoint                         | Method | Auth | Description                           |
| -------------------------------- | ------ | ---- | ------------------------------------- |
| `/messages`                      | POST   | Yes  | Send a message to another user        |
| `/messages`                      | GET    | Yes  | List messages (partner/booking filter) |
| `/messages/conversations`        | GET    | Yes  | List conversation summaries           |
| `/messages/unread`               | GET    | Yes  | Get unread message count              |
| `/messages/{id}/read`            | PATCH  | Yes  | Mark a message as read                |
| `/messages/conversation/{id}/read` | POST | Yes  | Mark conversation as read             |

## Communities

| Endpoint                                 | Method | Auth | Description                        |
| ---------------------------------------- | ------ | ---- | ---------------------------------- |
| `/communities/search`                    | GET    | No   | Search communities (name/PLZ/city) |
| `/communities`                           | POST   | Yes  | Create a community (409 if already in one) |
| `/communities/{id}`                      | GET    | No   | Get community details              |
| `/communities/{id}`                      | PATCH  | Yes  | Update community (admin only)      |
| `/communities/{id}/join`                 | POST   | Yes  | Join a community                   |
| `/communities/{id}/leave`                | POST   | Yes  | Leave a community (409 for the last admin) |
| `/communities/{id}/members/{user_id}/promote` | POST | Yes | Make a member an admin (community admin only) |
| `/communities/{id}/members`              | GET    | No   | List community members             |
| `/communities/my`                        | GET    | Yes  | List your communities              |
| `/communities/{id}/merge`                | POST   | Yes  | Merge into another (admin of both, or platform admin) |
| `/communities/{id}/merge-suggestions`    | GET    | Yes  | Auto-suggest merge candidates      |

## Skills

| Endpoint              | Method | Auth | Description                  |
| --------------------- | ------ | ---- | ---------------------------- |
| `/skills`             | GET    | No   | List skills (search, filter) |
| `/skills`             | POST   | Yes  | Create a skill listing       |
| `/skills/{id}`        | GET    | No   | Get skill details            |
| `/skills/{id}`        | PATCH  | Yes  | Update skill (owner only)    |
| `/skills/{id}`        | DELETE | Yes  | Delete skill (owner only)    |
| `/skills/categories`  | GET    | No   | List skill categories        |

## Activity Feed

| Endpoint       | Method | Auth | Description               |
| -------------- | ------ | ---- | ------------------------- |
| `/activity`    | GET    | No   | Community activity feed   |
| `/activity/my` | GET    | Yes  | Your own activity history |

## Invites

| Endpoint                | Method | Auth | Description                  |
| ----------------------- | ------ | ---- | ---------------------------- |
| `/invites`              | POST   | Yes  | Create community invite code |
| `/invites`              | GET    | Yes  | List invites for a community |
| `/invites/{code}/redeem` | POST  | Yes  | Redeem an invite code        |
| `/invites/{id}`         | DELETE | Yes  | Revoke an invite code        |

## Reviews

| Endpoint                      | Method | Auth | Description                  |
| ----------------------------- | ------ | ---- | ---------------------------- |
| `/reviews`                    | POST   | Yes  | Leave a review on a booking  |
| `/reviews/booking/{id}`       | GET    | No   | Get reviews for a booking    |
| `/reviews/user/{id}`          | GET    | No   | Get reviews received by user |
| `/reviews/user/{id}/summary`  | GET    | No   | Get user's average rating    |

## Instance (Federation)

| Endpoint         | Method | Auth | Description                    |
| ---------------- | ------ | ---- | ------------------------------ |
| `/instance/info` | GET    | No   | Instance metadata (federation) |

## Federation – Instance Directory

| Endpoint                   | Method | Auth  | Description                              |
| -------------------------- | ------ | ----- | ---------------------------------------- |
| `/directory`               | GET    | No    | List known NeighbourGood instances       |
| `/directory`               | POST   | Admin | Add instance by URL (auto-fetches info)  |
| `/directory/{id}`          | DELETE | Admin | Remove instance from directory           |
| `/directory/refresh`       | POST   | Admin | Re-crawl all instances for updates       |

## Federation – Red Sky Alerts

| Endpoint                      | Method | Auth  | Description                              |
| ----------------------------- | ------ | ----- | ---------------------------------------- |
| `/alerts`                     | GET    | No    | List Red Sky alerts (active = not dismissed and not expired, by default) |
| `/alerts/{id}`                | GET    | Yes   | One received alert, incl. `expires_at` (any logged-in user) |
| `/alerts/send`                | POST   | Admin | Broadcast alert to all known instances; `duration_hours` 1–336, default 48 |
| `/alerts/receive`             | POST   | No    | Receive alert notification from a known instance; the alert is fetched back from its source to verify it |
| `/alerts/outgoing/{alert_uid}` | GET   | No    | An alert this instance broadcast, with `expires_at` (used by receivers to verify) |
| `/alerts/{id}/dismiss`        | PATCH  | Admin | Dismiss a Red Sky alert                  |

## WhatsApp

See [whatsapp-setup.md](whatsapp-setup.md). All endpoints answer 503 while `NG_WHATSAPP_BRIDGE_URL` is unset (the webhook answers 404).

| Endpoint | Method | Auth | Description |
| -------- | ------ | ---- | ----------- |
| `/whatsapp/webhook` | POST | HMAC | Message events from the WAHA bridge; `X-Webhook-Hmac` must be the HMAC-SHA512 of the raw body with `NG_WHATSAPP_WEBHOOK_SECRET` |
| `/users/me/whatsapp/start-link` | POST | Yes | Code (30 min) to send to the bot privately; returns `{code, message, bot_url, expires_at}` |
| `/users/me/whatsapp` | PATCH | Yes | `{whatsapp_notify}`: private messages from the bot on/off |
| `/users/me/whatsapp` | DELETE | Yes | Unlink WhatsApp (409 for WhatsApp-only accounts) |
| `/communities/{id}/whatsapp` | GET | Admin/leader | `{configured, linked, digest}` |
| `/communities/{id}/whatsapp/start-link` | POST | Admin/leader | Code to post in the group as `!link CODE` |
| `/communities/{id}/whatsapp` | PATCH | Admin/leader | `{digest: "off" \| "daily" \| "weekly"}` |
| `/communities/{id}/whatsapp` | DELETE | Admin/leader | Unlink the group |
| `/auth/claim/{token}` | GET | No | Name of the WhatsApp-only account the link belongs to |
| `/auth/claim/{token}` | POST | No | `{email, password}`: turn it into an app account; returns a JWT |
| `/whatsapp/bridge/status` | GET | Platform admin | Bridge session status and bot number |
| `/whatsapp/bridge/start` | POST | Platform admin | Create/start the bridge session |
| `/whatsapp/bridge/qr` | GET | Platform admin | Pairing QR code `{mimetype, data}` (base64) |

## Data Export & Migration

| Endpoint              | Method | Auth | Description                              |
| --------------------- | ------ | ---- | ---------------------------------------- |
| `/export/my-data`     | GET    | Yes  | Export all user data as portable JSON (`bookings` = as borrower, `lending_bookings` = as lender) |
| `/migrate/import`     | POST   | Yes  | Import resources/skills (max 200 items; imported items earn no reputation) |
