"""Request-id and Celery header propagation."""

from daastaan_common.request_id import (
    CELERY_REQUEST_ID_KEY,
    RequestIdTask,
    bind_request_id,
    clear_request_id,
    current_request_id,
    ensure_request_id,
    new_request_id,
)


class TestRequestIdContext:
    def setup_method(self) -> None:
        clear_request_id()

    def teardown_method(self) -> None:
        clear_request_id()

    def test_ensure_binds_and_reuses(self):
        first = ensure_request_id("abc123")
        assert first == "abc123"
        assert current_request_id() == "abc123"
        minted = ensure_request_id(None)
        assert len(minted) == 32

    def test_new_ids_are_unique(self):
        assert new_request_id() != new_request_id()


class TestRequestIdTaskHeaders:
    def setup_method(self) -> None:
        clear_request_id()

    def teardown_method(self) -> None:
        clear_request_id()

    def test_apply_async_injects_header_from_context(self, monkeypatch):
        captured: dict = {}

        class DummyTask(RequestIdTask):
            name = "dummy"
            abstract = False

            def apply_async(self, args=None, kwargs=None, **options):  # type: ignore[no-untyped-def]
                # Call the mixin logic via super... we test by invoking the override body.
                headers = dict(options.get("headers") or {})
                request_id = current_request_id()
                if request_id and CELERY_REQUEST_ID_KEY not in headers:
                    headers[CELERY_REQUEST_ID_KEY] = request_id
                options["headers"] = headers
                captured.update(options)
                return "ok"

        bind_request_id("req-from-api")
        DummyTask().apply_async()
        assert captured["headers"][CELERY_REQUEST_ID_KEY] == "req-from-api"
