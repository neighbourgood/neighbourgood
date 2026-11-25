"""Daily/weekly WhatsApp group digest."""

import datetime

import pytest

from app.config import settings
from app.models.community import Community
from app.models.resource import Resource
from app.services.whatsapp_digest import run_due_digests

GROUP = "120363000000000002@g.us"


@pytest.fixture()
def sent(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "http://waha:3000")
    outbox: list[tuple[str, str]] = []
    monkeypatch.setattr("app.services.whatsapp.send_text", lambda chat, text: outbox.append((chat, text)))
    return outbox


@pytest.fixture()
def community(db, community_id):
    c = db.get(Community, community_id)
    c.whatsapp_group_id = GROUP
    c.primary_language = "en"
    db.commit()
    return c


def _share(client, auth_headers, community_id, title):
    res = client.post(
        "/resources",
        headers=auth_headers,
        json={"title": title, "category": "tool", "community_id": community_id},
    )
    assert res.status_code == 201, res.text


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def test_digest_lists_new_items_once(client, auth_headers, community, db, sent):
    _share(client, auth_headers, community.id, "Ladder")
    sent.clear()  # the share itself posts nothing to the group
    now = _now() + datetime.timedelta(seconds=1)
    assert run_due_digests(db, now) == 1
    chat, text = sent[0]
    assert chat == GROUP
    assert "This week in Test Community" in text and "Ladder" in text and "Test User" in text
    # Running again right away (another worker, or the next loop) posts nothing
    assert run_due_digests(db, now) == 0
    assert run_due_digests(db, now + datetime.timedelta(days=1)) == 0
    assert len(sent) == 1


def test_no_digest_without_activity(client, auth_headers, community, db, sent):
    community.whatsapp_last_digest_at = _now()
    db.commit()
    _share(client, auth_headers, community.id, "Old drill")
    # Make the item older than the last digest
    db.query(Resource).update({Resource.created_at: _now() - datetime.timedelta(days=2)})
    db.commit()
    assert run_due_digests(db, _now() + datetime.timedelta(days=8)) == 0
    assert sent == []


def test_daily_and_off(client, auth_headers, community, db, sent):
    community.whatsapp_digest = "daily"
    community.whatsapp_last_digest_at = _now() - datetime.timedelta(hours=1)
    db.commit()
    _share(client, auth_headers, community.id, "Tent")
    assert run_due_digests(db, _now()) == 0  # not a day yet
    assert run_due_digests(db, _now() + datetime.timedelta(days=1)) == 1
    assert "Today in" in sent[-1][1]

    community.whatsapp_digest = "off"
    db.commit()
    _share(client, auth_headers, community.id, "Kayak")
    assert run_due_digests(db, _now() + datetime.timedelta(days=10)) == 0


def test_digest_in_community_language(client, auth_headers, community, db, sent):
    community.primary_language = "de"
    db.commit()
    _share(client, auth_headers, community.id, "Leiter")
    run_due_digests(db, _now() + datetime.timedelta(seconds=1))
    assert "Diese Woche in Test Community" in sent[-1][1]


def test_nothing_when_bridge_not_configured(client, auth_headers, community, db, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_bridge_url", "")
    assert run_due_digests(db) == 0
