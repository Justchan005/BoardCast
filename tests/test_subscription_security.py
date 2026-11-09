"""Regression checks for subscriber-state preservation and bounded verification."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from backend import config, store, subscriptions, tokens, web
from backend.localdb import LocalClient

EMAIL = "student.subscription@mail.apu.edu.my"


@pytest.fixture
def subscription_db(monkeypatch, tmp_path):
    client = LocalClient(tmp_path / "subscriptions.json")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(store, "_db", client)
    monkeypatch.setattr(tokens, "_SECRET", b"subscription-test-only-secret")
    return client


def challenge_ref(client):
    return client.collection("subscription_challenges").document(EMAIL)


@pytest.mark.parametrize("active", [True, False])
def test_request_preserves_existing_subscription_and_metadata(subscription_db, monkeypatch, active):
    original = {"email": EMAIL, "active": active, "pilot_cohort": "club-a"}
    store.upsert_subscriber(original)
    messages = []
    monkeypatch.setattr(web, "_send_or_log", lambda *args: messages.append(args))
    result = web.subscribe_submit(EMAIL)
    assert result.status_code == 303
    assert len(messages) == 1
    assert store.get_subscriber(EMAIL) == original
    assert challenge_ref(subscription_db).get().exists


def test_failed_delivery_never_disables_an_existing_subscriber(subscription_db, monkeypatch):
    store.upsert_subscriber({"email": EMAIL, "active": True})

    def fail(*args):
        raise RuntimeError("Test delivery failure")

    monkeypatch.setattr(web, "_send_or_log", fail)
    with pytest.raises(RuntimeError, match="delivery failure"):
        web.subscribe_submit(EMAIL)
    assert store.get_subscriber(EMAIL)["active"] is True


def test_unverified_request_does_not_create_a_subscriber(subscription_db):
    subscriptions.request_code(EMAIL)
    assert store.get_subscriber(EMAIL) is None


def test_request_route_throttles_without_a_second_email(subscription_db, monkeypatch):
    messages = []
    monkeypatch.setattr(web, "_send_or_log", lambda *args: messages.append(args))
    assert web.subscribe_submit(EMAIL).status_code == 303
    response = web.subscribe_submit(EMAIL)
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= subscriptions.RESEND_SECONDS
    assert len(messages) == 1


def test_concurrent_requests_reserve_only_one_code(subscription_db):
    def request(_):
        try:
            return bool(subscriptions.request_code(EMAIL))
        except subscriptions.ChallengeThrottled:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(8)))
    assert sum(results) == 1


def test_maximum_guesses_cannot_be_reset_by_resending(subscription_db):
    correct = subscriptions.request_code(EMAIL)
    wrong = "000000" if correct != "000000" else "000001"
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert not any(pool.map(lambda _: subscriptions.verify_code(EMAIL, wrong), range(10)))
    challenge = challenge_ref(subscription_db).get().to_dict()
    assert challenge["attempts"] == subscriptions.MAX_ATTEMPTS
    assert not subscriptions.verify_code(EMAIL, correct)
    challenge_ref(subscription_db).update({"requested_at": datetime.now(timezone.utc) - timedelta(minutes=2)})
    with pytest.raises(subscriptions.ChallengeThrottled):
        subscriptions.request_code(EMAIL)
    assert store.get_subscriber(EMAIL) is None


def test_resend_preserves_guess_count_and_original_expiry(subscription_db, monkeypatch):
    codes = iter(["123456", "654321"])

    def next_code():
        value = next(codes)
        return value, tokens.hash_code(value), datetime.now(timezone.utc) + timedelta(minutes=15)

    monkeypatch.setattr(tokens, "new_code", next_code)
    old = subscriptions.request_code(EMAIL)
    assert not subscriptions.verify_code(EMAIL, "999999")
    before = challenge_ref(subscription_db).get().to_dict()
    challenge_ref(subscription_db).update({"requested_at": datetime.now(timezone.utc) - timedelta(minutes=2)})
    new = subscriptions.request_code(EMAIL)
    after = challenge_ref(subscription_db).get().to_dict()
    assert after["expires_at"] == before["expires_at"]
    assert after["attempts"] == 1
    assert not subscriptions.verify_code(EMAIL, old)
    assert subscriptions.verify_code(EMAIL, new)


def test_successful_code_can_be_consumed_only_once_concurrently(subscription_db):
    store.upsert_subscriber({"email": EMAIL, "active": False, "pilot_cohort": "club-a"})
    code = subscriptions.request_code(EMAIL)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: subscriptions.verify_code(EMAIL, code), range(8)))
    assert sum(results) == 1
    assert store.get_subscriber(EMAIL)["active"] is True
    assert store.get_subscriber(EMAIL)["pilot_cohort"] == "club-a"
    assert challenge_ref(subscription_db).get().to_dict()["code_hash"] is None


def test_consumed_code_cannot_resubscribe_after_unsubscribe(subscription_db):
    code = subscriptions.request_code(EMAIL)
    assert subscriptions.verify_code(EMAIL, code)
    store.upsert_subscriber({"email": EMAIL, "active": False})
    assert not subscriptions.verify_code(EMAIL, code)
    assert store.get_subscriber(EMAIL)["active"] is False


def test_expired_challenge_requires_a_new_request(subscription_db):
    old = subscriptions.request_code(EMAIL)
    challenge_ref(subscription_db).update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
                                            "requested_at": datetime.now(timezone.utc) - timedelta(minutes=20),
                                            "attempts": subscriptions.MAX_ATTEMPTS})
    assert not subscriptions.verify_code(EMAIL, old)
    new = subscriptions.request_code(EMAIL)
    assert challenge_ref(subscription_db).get().to_dict()["attempts"] == 0
    assert subscriptions.verify_code(EMAIL, new)


def test_verify_route_rejects_forged_token_without_creating_state(subscription_db):
    response = web.verify_submit("forged", "123456")
    assert response.status_code == 303
    assert not subscription_db.data


@pytest.mark.parametrize("email", ["stranger@example.com", "a@evil@apu.edu.my",
                                    "a/b@mail.apu.edu.my", "a\\b@mail.apu.edu.my",
                                    "a b@mail.apu.edu.my", "@apu.edu.my"])
def test_invalid_addresses_never_create_challenges(subscription_db, email):
    with pytest.raises(ValueError):
        subscriptions.request_code(email)
    assert not subscription_db.data


def test_cloud_path_uses_one_transaction_for_code_consumption(subscription_db, monkeypatch):
    """Check the Firestore transaction contract without calling any cloud service."""
    code = subscriptions.request_code(EMAIL)
    calls = []

    class CloudRef:
        def __init__(self, ref):
            self.ref = ref

        def get(self, *, transaction):
            assert transaction is transaction_marker
            calls.append("read")
            return self.ref.get()

    class CloudCollection:
        def __init__(self, collection):
            self.collection = collection

        def document(self, key):
            return CloudRef(self.collection.document(key))

    class Transaction:
        def set(self, ref, data, *, merge):
            calls.append("write")
            ref.ref.set(data, merge=merge)

    class CloudClient:
        def collection(self, name):
            return CloudCollection(subscription_db.collection(name))

        def transaction(self):
            return transaction_marker

    transaction_marker = Transaction()

    def transactional(operation):
        def run(transaction):
            calls.append("transaction")
            return operation(transaction)
        return run

    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", CloudClient())
    monkeypatch.setattr(subscriptions.firestore, "transactional", transactional)
    assert subscriptions.verify_code(EMAIL, code)
    assert calls == ["transaction", "read", "write", "write"]
    assert subscription_db.collection("subscribers").document(EMAIL).get().to_dict()["active"] is True
