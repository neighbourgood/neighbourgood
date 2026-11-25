"""WhatsApp bridge integration: webhook, linking, light accounts, commands, fan-out."""

import datetime
import hashlib
import hmac
import json
import string

import pytest

from app.config import settings
from app.models.booking import Booking
from app.models.community import Community, CommunityMember
from app.models.crisis import EmergencyTicket
from app.models.resource import Resource
from app.models.user import User
from app.routers.whatsapp import parse_incoming
from app.services import chat_commands
from app.services.chat_i18n import LOCALES, alias_table, catalog, normalize, parse

SECRET = "test-hmac-key"
GROUP = "120363000000000001@g.us"


@pytest.fixture()
def sent(monkeypatch):
    """Configure the bridge and record outgoing messages instead of sending them."""
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://waha:3000")
    monkeypatch.setattr(settings, "whatsapp_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "whatsapp_bot_number", "4915100000000")
    outbox: list[tuple[str, str]] = []
    monkeypatch.setattr("app.services.whatsapp.send_text", lambda chat, text: outbox.append((chat, text)))
    return outbox


def _post(client, body: dict, secret: str | None = SECRET):
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Webhook-Hmac"] = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        headers["X-Webhook-Hmac-Algorithm"] = "sha512"
    return client.post("/whatsapp/webhook", content=raw, headers=headers)


def _event(chat: str, text: str, participant: str | None = None, push_name: str | None = None, from_me=False):
    payload = {"id": "false_x", "from": chat, "fromMe": from_me, "body": text, "_data": {}}
    if participant:
        payload["participant"] = participant
    if push_name:
        payload["_data"]["notifyName"] = push_name
    return {"event": "message", "session": "default", "payload": payload}


def group(client, sender: str, text: str, push_name: str | None = None):
    res = _post(client, _event(GROUP, text, participant=sender, push_name=push_name))
    assert res.status_code == 200, res.text


def dm(client, sender: str, text: str):
    res = _post(client, _event(sender, text))
    assert res.status_code == 200, res.text


def last(outbox, chat: str | None = None) -> str:
    msgs = [t for c, t in outbox if chat is None or c == chat]
    assert msgs, f"no message sent to {chat}: {outbox}"
    return msgs[-1]


def _english(db, community_id):
    db.get(Community, community_id).primary_language = "en"
    db.commit()


@pytest.fixture()
def linked(client, auth_headers, community_id, sent, db):
    """English-speaking community linked to GROUP through the app + the !link command."""
    _english(db, community_id)
    res = client.post(f"/communities/{community_id}/whatsapp/start-link", headers=auth_headers)
    assert res.status_code == 200, res.text
    code = res.json()["code"]
    group(client, "491700000001@c.us", f"!link {code}")
    db.expire_all()
    assert db.get(Community, community_id).whatsapp_group_id == GROUP
    sent.clear()
    return community_id


def _light(db, wa_id: str) -> User:
    db.expire_all()
    return db.query(User).filter(User.whatsapp_id == wa_id).first()


# ── Webhook auth and filtering ────────────────────────────────────


def test_webhook_404_when_not_configured(client):
    assert _post(client, _event("1@c.us", "help")).status_code == 404


def test_webhook_rejects_bad_or_missing_signature(client, sent):
    assert _post(client, _event("1@c.us", "help"), secret=None).status_code == 403
    assert _post(client, _event("1@c.us", "help"), secret="wrong").status_code == 403
    assert sent == []


def test_own_messages_and_plain_group_chat_are_ignored(client, linked, sent):
    _post(client, _event(GROUP, "!help", participant="1@c.us", from_me=True))
    group(client, "491700000002@c.us", "anyone got a ladder? help")
    assert sent == []


def test_parse_incoming_group_sender_and_ignored_chats():
    msg = parse_incoming(_event(GROUP, "!help", participant="49170@s.whatsapp.net", push_name="Ana"))
    assert msg.sender_id == "49170@c.us" and msg.chat_id == GROUP and msg.is_group and msg.push_name == "Ana"
    assert parse_incoming(_event("status@broadcast", "hi")) is None
    assert parse_incoming({"event": "session.status", "payload": {}}) is None
    assert parse_incoming(_event(GROUP, "!help")) is None  # group message without a sender


# ── Group linking ─────────────────────────────────────────────────


def test_group_link_code_needs_admin_or_leader(client, community_id, register_user, sent):
    other = register_user(2)
    client.post(f"/communities/{community_id}/join", headers=other)
    res = client.post(f"/communities/{community_id}/whatsapp/start-link", headers=other)
    assert res.status_code == 403


def test_group_link_code_works_once(client, auth_headers, community_id, sent, db):
    _english(db, community_id)
    code = client.post(f"/communities/{community_id}/whatsapp/start-link", headers=auth_headers).json()["code"]
    group(client, "1@c.us", f"!link {code}")
    assert "Test Community" in last(sent, GROUP)
    group(client, "1@c.us", f"!link {code}")
    assert "invalid" in last(sent, GROUP)


def test_unlinked_group_gets_hint(client, sent):
    group(client, "1@c.us", "!search drill")
    assert "not linked" in last(sent, GROUP)


def test_unknown_sender_gets_the_communitys_language(client, auth_headers, community_id, sent):
    code = client.post(f"/communities/{community_id}/whatsapp/start-link", headers=auth_headers).json()["code"]
    group(client, "1@c.us", f"!link {code}")  # Teststadt, country DE
    assert "verknüpft" in last(sent, GROUP)


def test_community_whatsapp_settings(client, auth_headers, linked):
    res = client.get(f"/communities/{linked}/whatsapp", headers=auth_headers)
    assert res.json() == {"configured": True, "linked": True, "digest": "weekly"}
    res = client.patch(f"/communities/{linked}/whatsapp", headers=auth_headers, json={"digest": "daily"})
    assert res.json()["digest"] == "daily"
    assert client.patch(f"/communities/{linked}/whatsapp", headers=auth_headers, json={"digest": "hourly"}).status_code == 422
    assert client.delete(f"/communities/{linked}/whatsapp", headers=auth_headers).status_code == 204
    assert client.get(f"/communities/{linked}/whatsapp", headers=auth_headers).json()["linked"] is False


# ── Light accounts ────────────────────────────────────────────────


def test_join_creates_light_account_in_senders_language(client, linked, sent, db):
    group(client, "491700000002@c.us", "!beitreten Anna")
    reply = last(sent, GROUP)
    assert "Willkommen Anna" in reply and "wa.me/4915100000000" in reply
    user = _light(db, "491700000002@c.us")
    assert user.account_type == "whatsapp" and user.language_code == "de"
    assert user.email.endswith("@whatsapp.invalid")
    assert db.query(CommunityMember).filter_by(user_id=user.id, community_id=linked).count() == 1
    # No login with the placeholder email
    res = client.post("/auth/login", json={"email": user.email, "password": "Testpass123"})
    assert res.status_code in (401, 422)  # .invalid is not even accepted as an address
    # Phone numbers never show up in member lists
    members = client.get(f"/communities/{linked}/members").json()
    assert "491700000002" not in json.dumps(members)


def test_join_uses_push_name_and_needs_some_name(client, linked, sent, db):
    group(client, "491700000003@c.us", "!join", push_name="Ben")
    assert "Welcome Ben" in last(sent, GROUP)
    group(client, "491700000004@c.us", "!join")
    assert "Please add the name" in last(sent, GROUP)
    assert _light(db, "491700000004@c.us") is None


def test_join_twice_says_already_member(client, linked, sent):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000002@c.us", "!join Anna")
    assert "already a member" in last(sent, GROUP)


def test_non_member_commands_point_to_join(client, linked, sent):
    group(client, "491700000009@c.us", "!lend drill")
    assert "!join" in last(sent, GROUP)


def test_unknown_private_sender_gets_intro(client, sent):
    dm(client, "491700000009@c.us", "hallo")
    assert "NeighbourGood bot" in last(sent, "491700000009@c.us")


# ── Sharing and borrowing ─────────────────────────────────────────


def test_lend_search_borrow_and_answer(client, linked, sent, db):
    lender, borrower = "491700000002@c.us", "491700000003@c.us"
    group(client, lender, "!join Anna")
    group(client, borrower, "!join Ben")
    dm(client, lender, "help")  # Anna opens a private chat, so she can get requests there
    dm(client, borrower, "help")
    sent.clear()

    group(client, lender, "!lend Bohrmaschine Bosch")
    resource = db.query(Resource).filter(Resource.title == "Bohrmaschine Bosch").one()
    assert f"#{resource.id}" in last(sent, GROUP)

    group(client, borrower, "!search bohr")
    assert f"#{resource.id} Bohrmaschine Bosch (Anna)" in last(sent, GROUP)

    group(client, borrower, f"!borrow {resource.id}")
    booking = db.query(Booking).one()
    assert booking.status == "pending"
    assert f"#{booking.id}" in last(sent, GROUP)
    # Anna gets the request privately, in her language, with the answer to send
    assert f"yes {booking.id}" in last(sent, lender)

    # Ben cannot approve his own request
    dm(client, borrower, f"yes {booking.id}")
    assert "not allowed" in last(sent, borrower) or "not possible" in last(sent, borrower)

    dm(client, lender, f"yes {booking.id}")
    db.expire_all()
    assert db.get(Booking, booking.id).status == "approved"
    assert "accepted" in last(sent, borrower)

    dm(client, lender, f"returned {booking.id}")
    db.expire_all()
    assert db.get(Booking, booking.id).status == "completed"


def test_borrow_with_dates_and_bad_dates(client, linked, sent, db):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000003@c.us", "!join Ben")
    group(client, "491700000002@c.us", "!lend ladder")
    rid = db.query(Resource).one().id
    group(client, "491700000003@c.us", f"!borrow {rid} 2099-01-02 2099-01-05")
    booking = db.query(Booking).one()
    assert (booking.start_date, booking.end_date) == (datetime.date(2099, 1, 2), datetime.date(2099, 1, 5))
    # Anna never wrote privately: Ben is told she'll see it in the app
    assert "app" in last(sent, GROUP)
    group(client, "491700000003@c.us", f"!borrow {rid} someday")
    assert "couldn't read the dates" in last(sent, GROUP)


def test_parse_date_formats():
    today = datetime.date(2026, 12, 20)
    assert chat_commands.parse_date("2027-01-03", today) == datetime.date(2027, 1, 3)
    assert chat_commands.parse_date("24.12.", today) == datetime.date(2026, 12, 24)
    assert chat_commands.parse_date("03.01", today) == datetime.date(2027, 1, 3)
    assert chat_commands.parse_date("3/1/27", today) == datetime.date(2027, 1, 3)
    assert chat_commands.parse_date("31.02.", today) is None
    assert chat_commands.parse_date("tomorrow", today) is None


def test_remove_takes_item_off_the_list(client, linked, sent, db):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000003@c.us", "!join Ben")
    group(client, "491700000002@c.us", "!lend ladder")
    rid = db.query(Resource).one().id
    group(client, "491700000003@c.us", f"!remove {rid}")
    assert "not allowed" in last(sent, GROUP)
    group(client, "491700000002@c.us", f"!remove {rid}")
    db.expire_all()
    assert db.get(Resource, rid).is_available is False


def test_offer_need_and_profile(client, linked, sent):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000002@c.us", "!offer bike repair")
    group(client, "491700000002@c.us", "!need babysitter")
    group(client, "491700000003@c.us", "!join Ben")
    group(client, "491700000003@c.us", "!profile anna")
    reply = last(sent, GROUP)
    assert "*Anna*" in reply and "bike repair" in reply and "babysitter" not in reply


def test_private_commands_are_refused_in_groups(client, linked, sent):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000002@c.us", "!mine")
    assert "privately" in last(sent, GROUP)


def test_app_only_and_unknown_commands(client, linked, sent):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000002@c.us", "!photo")
    assert "in the app" in last(sent, GROUP)
    dm(client, "491700000002@c.us", "blah")
    assert "help" in last(sent, "491700000002@c.us")


def test_help_lists_commands_in_language(client, linked, sent):
    group(client, "491700000002@c.us", "!hilfe")
    reply = last(sent, GROUP)
    assert "!suche <Wort>" in reply and "!leihen" in reply


# ── Linking an app account, claiming a light account ──────────────


def test_link_app_account_merges_light_account(client, linked, auth_headers, sent, db):
    wa_id = "491700000005@c.us"
    group(client, wa_id, "!join Carla")
    group(client, wa_id, "!lend tent")
    light = _light(db, wa_id)
    # The app user leaves first, so Carla's membership can move over
    app_user = db.query(User).filter(User.email == "test@example.com").one()
    res = client.post("/users/me/whatsapp/start-link", headers=auth_headers)
    body = res.json()
    assert body["message"].startswith("link ") and body["bot_url"].startswith("https://wa.me/4915100000000?text=")
    dm(client, wa_id, body["message"])
    assert "linked" in last(sent, wa_id)
    db.expire_all()
    assert db.query(Resource).filter(Resource.title == "tent").one().owner_id == app_user.id
    assert db.get(User, light.id).is_active is False
    assert db.get(User, app_user.id).whatsapp_id == wa_id
    me = client.get("/users/me", headers=auth_headers).json()
    assert me["whatsapp_linked"] is True and "whatsapp_id" not in me
    # Unlink
    assert client.delete("/users/me/whatsapp", headers=auth_headers).status_code == 204
    assert client.get("/users/me", headers=auth_headers).json()["whatsapp_linked"] is False


def test_claim_light_account(client, linked, sent, db):
    wa_id = "491700000006@c.us"
    group(client, wa_id, "!join Dana")
    dm(client, wa_id, "account")
    url = last(sent, wa_id).split()[-1]
    token = url.rsplit("/", 1)[-1]
    assert client.get(f"/auth/claim/{token}").json() == {"display_name": "Dana"}
    res = client.post(f"/auth/claim/{token}", json={"email": "dana@example.com", "password": "weak"})
    assert res.status_code == 422
    res = client.post(f"/auth/claim/{token}", json={"email": "dana@example.com", "password": "Strongpass1"})
    assert res.status_code == 200
    assert client.post(f"/auth/claim/{token}", json={"email": "x@example.com", "password": "Strongpass1"}).status_code == 404
    login = client.post("/auth/login", json={"email": "dana@example.com", "password": "Strongpass1"})
    assert login.status_code == 200
    me = client.get("/users/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}).json()
    assert me["account_type"] == "full" and me["whatsapp_linked"] is True


def test_claim_rejects_taken_email(client, linked, auth_headers, sent):
    group(client, "491700000006@c.us", "!join Dana")
    dm(client, "491700000006@c.us", "account")
    token = last(sent, "491700000006@c.us").split()[-1].rsplit("/", 1)[-1]
    res = client.post(f"/auth/claim/{token}", json={"email": "TEST@example.com", "password": "Strongpass1"})
    assert res.status_code == 409


def test_link_code_for_other_full_account_is_refused(client, linked, auth_headers, register_user, sent, db):
    other = register_user(2)
    wa_id = "491700000007@c.us"
    code = client.post("/users/me/whatsapp/start-link", headers=other).json()["message"]
    dm(client, wa_id, code)
    code = client.post("/users/me/whatsapp/start-link", headers=auth_headers).json()["message"]
    dm(client, wa_id, code)
    assert "already belongs" in last(sent, wa_id)


# ── Red Sky ───────────────────────────────────────────────────────


def test_sos_posts_to_group_and_routine_tickets_do_not(client, linked, auth_headers, sent, db):
    group(client, "491700000002@c.us", "!join Anna")
    sent.clear()
    group(client, "491700000002@c.us", "!sos no water on 3rd floor")
    ticket = db.query(EmergencyTicket).one()
    assert ticket.urgency == "high" and ticket.ticket_type == "request"
    assert any("HIGH" in text and "no water" in text for chat, text in sent if chat == GROUP)

    sent.clear()
    client.post(
        f"/communities/{linked}/tickets",
        headers=auth_headers,
        json={"ticket_type": "request", "title": "Spare blankets?", "urgency": "medium"},
    )
    assert sent == []


def test_ping_needs_red_sky(client, linked, sent):
    group(client, "491700000002@c.us", "!join Anna")
    group(client, "491700000002@c.us", "!ping trapped")
    assert "Red Sky" in last(sent, GROUP)


def test_vote_switches_red_sky_and_tells_the_group(client, linked, auth_headers, sent, db):
    group(client, "491700000002@c.us", "!join Anna")  # 2 members: 2 votes needed
    client.post(f"/communities/{linked}/crisis/vote", headers=auth_headers, json={"vote_type": "activate"})
    sent.clear()
    group(client, "491700000002@c.us", "!vote")
    db.expire_all()
    assert db.get(Community, linked).mode == "red"
    texts = [text for chat, text in sent if chat == GROUP]
    assert any("RED SKY" in text for text in texts)
    group(client, "491700000002@c.us", "!status")
    assert "Red Sky" in last(sent, GROUP)


# ── Personal settings ─────────────────────────────────────────────


def test_stop_turns_off_private_notifications(client, linked, sent, db):
    lender, borrower = "491700000002@c.us", "491700000003@c.us"
    group(client, lender, "!join Anna")
    group(client, borrower, "!join Ben")
    dm(client, lender, "stop")
    assert "off" in last(sent, lender)
    group(client, lender, "!lend ladder")
    sent.clear()
    group(client, borrower, f"!borrow {db.query(Resource).one().id}")
    assert not [t for c, t in sent if c == lender]


def test_delete_light_account_needs_code(client, linked, sent, db):
    wa_id = "491700000008@c.us"
    group(client, wa_id, "!join Eve")
    group(client, wa_id, "!lend kettle")
    user = _light(db, wa_id)
    dm(client, wa_id, "delete")
    assert "To confirm" in last(sent, wa_id)
    dm(client, wa_id, "delete 000000" if chat_commands.delete_code(user) != "000000" else "delete 111111")
    db.expire_all()
    assert db.get(User, user.id).is_active is True
    dm(client, wa_id, f"delete {chat_commands.delete_code(user)}")
    db.expire_all()
    gone = db.get(User, user.id)
    assert gone.is_active is False and gone.whatsapp_id is None
    assert db.query(Resource).filter(Resource.title == "kettle").count() == 0


# ── Command words and translations ────────────────────────────────


def test_every_locale_has_the_same_keys_and_placeholders():
    en = catalog("en")
    fields = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
    for locale in LOCALES:
        cat = catalog(locale)
        assert set(cat["commands"]) == set(en["commands"]), locale
        for section in ("args", "desc", "text", "status", "urgency"):
            assert set(cat[section]) == set(en[section]), (locale, section)
        for key, value in en["text"].items():
            assert fields(cat["text"][key]) == fields(value), (locale, key)
        for command, aliases in cat["commands"].items():
            assert aliases and all(" " not in a for a in aliases), (locale, command)


def test_no_command_word_means_two_things():
    seen: dict[str, str] = {}
    for locale in LOCALES:
        for command, aliases in catalog(locale)["commands"].items():
            for alias in aliases:
                key = normalize(alias)
                assert seen.setdefault(key, command) == command, (locale, alias, seen[key], command)
    assert alias_table()


def test_parse_prefix_rules():
    assert parse("suche Leiter", require_prefix=True) is None
    assert parse("!SUCHE Leiter", require_prefix=True)[:2] == ("search", ["Leiter"])
    assert parse("/help@Bot", require_prefix=True)[0] == "help"
    assert parse("zurueck 4", require_prefix=False)[0] == "returned"
