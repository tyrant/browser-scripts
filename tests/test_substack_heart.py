"""Tests for substack_heart.py."""
import base64
from unittest.mock import MagicMock, mock_open, patch

import pytest

from substack_heart import (
    REACTION_BACKOFFS,
    REACTION_BODY,
    TOKEN_FILE,
    _main,
    check_substack_login,
    find_heart_link,
    find_post_url,
    get_email_body_html,
    get_gmail_service,
    heart_all,
    heart_via_api,
    is_subscriber_notification,
    iter_messages,
    like_post,
    main,
    mark_as_read,
    reauth,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _encode(html: str) -> str:
    return base64.urlsafe_b64encode(html.encode()).decode()


def _html_payload(html: str) -> dict:
    return {"mimeType": "text/html", "body": {"data": _encode(html)}}


def _make_msg(msg_id, subject, sender, html):
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            **_html_payload(html),
        },
    }


def _subscriber_html(pub="author", slug="my-post"):
    return (
        f'<a href="https://open.substack.com/pub/{pub}/p/{slug}">Read</a>'
        f'<a href="https://substack.com/post?submitLike=true">Like</a>'
    )


def _resp_ctx(status=200):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.value.status = status
    return ctx


def _make_page(url="https://author.substack.com/p/post", first_aria_pressed="false"):
    page = MagicMock()
    page.url = url
    like_btn = MagicMock()
    like_btn.get_attribute.return_value = first_aria_pressed
    page.locator.return_value.first = like_btn
    page.expect_response.return_value = _resp_ctx(200)
    return page


# ── Pure functions ─────────────────────────────────────────────────────────────


class TestIsSubscriberNotification:
    def test_substack_sender_is_notification(self):
        assert is_subscriber_notification("Writer <writer@substack.com>") is True

    def test_no_reply_is_not_notification(self):
        assert is_subscriber_notification("no-reply@substack.com") is False

    def test_non_substack_sender_is_not_notification(self):
        assert is_subscriber_notification("someone@gmail.com") is False


class TestGetEmailBodyHtml:
    def test_flat_html_payload(self):
        html = "<p>hello</p>"
        assert get_email_body_html(_html_payload(html)) == html

    def test_nested_multipart_finds_html(self):
        html = "<p>hello</p>"
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _encode("plain")}},
                _html_payload(html),
            ],
        }
        assert get_email_body_html(payload) == html

    def test_no_html_returns_none(self):
        payload = {"mimeType": "text/plain", "body": {"data": _encode("plain")}}
        assert get_email_body_html(payload) is None


class TestFindHeartLink:
    def test_submit_like_link(self):
        html = '<a href="https://substack.com/post?submitLike=true">x</a>'
        assert find_heart_link(html) == "https://substack.com/post?submitLike=true"

    def test_app_link_with_like_text(self):
        html = '<a href="https://substack.com/app-link/post/123">Like</a>'
        assert find_heart_link(html) == "https://substack.com/app-link/post/123"

    def test_no_heart_link_returns_none(self):
        html = '<a href="https://substack.com/post/123">Read more</a>'
        assert find_heart_link(html) is None


class TestFindPostUrl:
    def test_constructs_native_substack_url(self):
        html = '<a href="https://open.substack.com/pub/author/p/my-post">Read</a>'
        assert find_post_url(html) == "https://author.substack.com/p/my-post"

    def test_no_open_substack_link_returns_none(self):
        html = '<a href="https://substack.com/inbox">Inbox</a>'
        assert find_post_url(html) is None


# ── Gmail API ─────────────────────────────────────────────────────────────────


class TestGetGmailService:
    @patch("substack_heart.build")
    @patch("substack_heart.Credentials")
    @patch("os.path.exists", return_value=True)
    def test_valid_cached_token(self, _exists, mock_creds_cls, mock_build):
        mock_creds = MagicMock(valid=True)
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds
        get_gmail_service()
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)

    @patch("substack_heart.build")
    @patch("substack_heart.Credentials")
    @patch("os.path.exists", return_value=True)
    def test_expired_token_refreshes(self, _exists, mock_creds_cls, mock_build):
        mock_creds = MagicMock(valid=False, expired=True, refresh_token="tok")
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds
        with patch("builtins.open", mock_open()):
            get_gmail_service()
        mock_creds.refresh.assert_called_once()
        mock_build.assert_called_once()

    @patch("substack_heart.Credentials")
    @patch("os.path.exists", return_value=True)
    def test_revoked_token_raises_runtime_error(self, _exists, mock_creds_cls):
        mock_creds = MagicMock(valid=False, expired=True, refresh_token="tok")
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds
        mock_creds.refresh.side_effect = Exception("invalid_grant: Token has been expired or revoked.")
        with pytest.raises(RuntimeError, match="Gmail token revoked"):
            get_gmail_service()

    @patch("substack_heart.InstalledAppFlow")
    @patch("substack_heart.build")
    @patch("os.path.exists", return_value=False)
    def test_no_token_file_runs_oauth_flow(self, _exists, mock_build, mock_flow_cls):
        mock_creds = MagicMock()
        mock_flow_cls.from_client_secrets_file.return_value.run_local_server.return_value = mock_creds
        with patch("builtins.open", mock_open()):
            get_gmail_service()
        mock_flow_cls.from_client_secrets_file.assert_called_once()
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)


class TestIterMessages:
    def test_single_page(self):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "1"}, {"id": "2"}]
        }
        assert list(iter_messages(svc, "query")) == [{"id": "1"}, {"id": "2"}]

    def test_multiple_pages(self):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "1"}], "nextPageToken": "tok"},
            {"messages": [{"id": "2"}]},
        ]
        assert list(iter_messages(svc, "query")) == [{"id": "1"}, {"id": "2"}]

    def test_empty_result(self):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        assert list(iter_messages(svc, "query")) == []


class TestMarkAsRead:
    def test_marks_message_as_read(self):
        svc = MagicMock()
        mark_as_read(svc, "msg123")
        svc.users.return_value.messages.return_value.modify.assert_called_with(
            userId="me", id="msg123", body={"removeLabelIds": ["UNREAD"]}
        )

    @patch("substack_heart.get_gmail_service")
    def test_retries_with_fresh_service_on_os_error(self, mock_get_svc):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.modify.return_value.execute.side_effect = OSError("dropped")
        fresh_svc = MagicMock()
        mock_get_svc.return_value = fresh_svc
        mark_as_read(svc, "msg123")
        fresh_svc.users.return_value.messages.return_value.modify.return_value.execute.assert_called_once()


# ── Playwright ────────────────────────────────────────────────────────────────


class TestLikePost:
    def test_sign_in_redirect_returns_false(self):
        page = _make_page(url="https://substack.com/sign-in?redirect=...")
        assert like_post(page, "https://author.substack.com/p/post") is False

    def test_age_verification_redirect_returns_none(self):
        page = _make_page(url="https://substack.com/age-verification-required?redirect_url=...")
        assert like_post(page, "https://author.substack.com/p/post") is None

    def test_custom_domain_hearts_via_api(self):
        page = _make_page(url="https://customdomain.com/p/post")
        page.request.get.return_value = MagicMock(status=200, json=lambda: {"id": 123})
        page.request.post.return_value = MagicMock(status=200)
        assert like_post(page, "https://author.substack.com/p/post") is True
        page.request.post.assert_called_once()

    def test_like_button_not_found_returns_false(self):
        page = _make_page()
        page.wait_for_selector.side_effect = Exception("timeout")
        assert like_post(page, "https://author.substack.com/p/post") is False

    def test_already_liked_returns_true(self):
        page = _make_page(first_aria_pressed="true")
        assert like_post(page, "https://author.substack.com/p/post") is True

    def test_successful_like_returns_true(self):
        page = _make_page()
        assert like_post(page, "https://author.substack.com/p/post") is True

    def test_scroll_detached_is_swallowed_and_click_still_succeeds(self):
        page = _make_page()
        page.locator.return_value.first.scroll_into_view_if_needed.side_effect = Exception(
            "Element is not attached to the DOM"
        )
        assert like_post(page, "https://author.substack.com/p/post") is True

    @patch("substack_heart.time.sleep")
    def test_rate_limited_every_attempt_returns_false(self, mock_sleep):
        page = _make_page()
        page.expect_response.return_value.value.status = 429
        assert like_post(page, "https://author.substack.com/p/post") is False
        # one click per attempt: initial + len(REACTION_BACKOFFS) retries
        assert page.locator.return_value.first.click.call_count == 1 + len(REACTION_BACKOFFS)

    @patch("substack_heart.time.sleep")
    def test_rate_limited_then_success_recovers(self, mock_sleep):
        page = _make_page()
        ctx_429 = _resp_ctx(429)
        ctx_200 = _resp_ctx(200)
        page.expect_response.side_effect = [ctx_429, ctx_200]
        assert like_post(page, "https://author.substack.com/p/post") is True

    def test_no_reaction_api_but_aria_pressed_true_returns_true(self):
        page = _make_page()
        page.expect_response.side_effect = Exception("no response captured")
        page.locator.return_value.first.get_attribute.side_effect = ["false", "true"]
        assert like_post(page, "https://author.substack.com/p/post") is True

    def test_no_reaction_api_and_aria_pressed_false_returns_false(self):
        page = _make_page()
        page.expect_response.side_effect = Exception("no response captured")
        page.locator.return_value.first.get_attribute.side_effect = ["false", "false"]
        assert like_post(page, "https://author.substack.com/p/post") is False


class TestHeartViaApi:
    def _page(self, get_status=200, get_json=None, post_status=200):
        page = MagicMock()
        page.request.get.return_value = MagicMock(
            status=get_status, json=lambda: (get_json if get_json is not None else {"id": 999})
        )
        page.request.post.return_value = MagicMock(status=post_status)
        return page

    def test_success_returns_true(self):
        assert heart_via_api(self._page(), "https://pub.substack.com/p/slug") is True

    def test_unparseable_url_returns_false(self):
        assert heart_via_api(MagicMock(), "https://example.com/weird") is False

    def test_lookup_non_200_returns_false(self):
        assert heart_via_api(self._page(get_status=429), "https://pub.substack.com/p/slug") is False

    def test_no_post_id_returns_false(self):
        assert heart_via_api(self._page(get_json={}), "https://pub.substack.com/p/slug") is False

    def test_reaction_non_2xx_returns_false(self):
        assert heart_via_api(self._page(post_status=429), "https://pub.substack.com/p/slug") is False

    def test_posts_reaction_body_to_apex_host(self):
        page = self._page()
        heart_via_api(page, "https://pub.substack.com/p/slug")
        args, kwargs = page.request.post.call_args
        assert args[0] == "https://substack.com/api/v1/post/999/reaction"
        assert kwargs["data"] == REACTION_BODY


class TestCheckSubstackLogin:
    def _ctx(self, cookies, auth_status=200):
        ctx = MagicMock()
        ctx.cookies.return_value = cookies
        page = ctx.new_page.return_value
        page.evaluate.return_value = auth_status
        return ctx

    @patch("substack_heart._launch")
    def test_returns_true_when_session_authenticated(self, mock_launch):
        ctx = self._ctx([{"name": "substack.sid", "value": "abc"}], auth_status=200)
        mock_launch.return_value = ctx
        assert check_substack_login(MagicMock(), None) is True
        ctx.close.assert_called_once()

    @patch("substack_heart._launch")
    def test_returns_false_when_sid_cookie_present_but_session_stale(self, mock_launch):
        ctx = self._ctx([{"name": "substack.sid", "value": "abc"}], auth_status=401)
        mock_launch.return_value = ctx
        assert check_substack_login(MagicMock(), None) is False
        ctx.new_page.return_value.close.assert_called_once()
        ctx.close.assert_called_once()

    @patch("substack_heart._launch")
    def test_returns_false_when_sid_cookie_absent(self, mock_launch):
        ctx = self._ctx([{"name": "other", "value": "xyz"}])
        mock_launch.return_value = ctx
        assert check_substack_login(MagicMock(), None) is False
        ctx.new_page.assert_not_called()
        ctx.close.assert_called_once()


class TestHeartAll:
    def _run(self, like_result, items=None):
        items = items or [("msg1", "https://author.substack.com/p/post", "Post 1")]
        ctx = MagicMock()
        gmail = MagicMock()
        with patch("substack_heart._launch", return_value=ctx):
            with patch("substack_heart.like_post", return_value=like_result):
                result = heart_all(MagicMock(), None, items, gmail)
        return result, gmail

    def test_success_increments_hearted_and_marks_read(self):
        (hearted, failed, age_skipped, failed_items), gmail = self._run(True)
        assert hearted == 1 and failed == 0 and age_skipped == 0 and failed_items == []
        gmail.users.return_value.messages.return_value.modify.assert_called_once()

    def test_failure_increments_failed_and_leaves_unread(self):
        (hearted, failed, age_skipped, failed_items), gmail = self._run(False)
        assert hearted == 0 and failed == 1 and age_skipped == 0
        assert len(failed_items) == 1
        gmail.users.return_value.messages.return_value.modify.assert_not_called()

    def test_age_gated_increments_skipped_and_marks_read(self):
        (hearted, failed, age_skipped, failed_items), gmail = self._run(None)
        assert hearted == 0 and failed == 0 and age_skipped == 1 and failed_items == []
        gmail.users.return_value.messages.return_value.modify.assert_called_once()

    def test_like_post_exception_contained_as_failure(self):
        items = [("msg1", "https://author.substack.com/p/post", "Post 1")]
        ctx = MagicMock()
        gmail = MagicMock()
        with patch("substack_heart._launch", return_value=ctx):
            with patch("substack_heart.like_post", side_effect=Exception("detached")):
                hearted, failed, age_skipped, failed_items = heart_all(MagicMock(), None, items, gmail)
        assert hearted == 0 and failed == 1 and age_skipped == 0
        assert len(failed_items) == 1
        ctx.new_page.return_value.close.assert_called_once()
        gmail.users.return_value.messages.return_value.modify.assert_not_called()

    def test_mixed_outcomes_tallied_correctly(self):
        items = [
            ("m1", "https://a.substack.com/p/1", "L1"),
            ("m2", "https://a.substack.com/p/2", "L2"),
            ("m3", "https://a.substack.com/p/3", "L3"),
        ]
        ctx = MagicMock()
        gmail = MagicMock()
        with patch("substack_heart._launch", return_value=ctx):
            with patch("substack_heart.like_post", side_effect=[True, False, None]):
                hearted, failed, age_skipped, failed_items = heart_all(MagicMock(), None, items, gmail)
        assert hearted == 1 and failed == 1 and age_skipped == 1
        assert len(failed_items) == 1 and failed_items[0][0] == "m2"


# ── Orchestration ─────────────────────────────────────────────────────────────


class TestMain:
    @patch("substack_heart._main")
    def test_success_calls_inner_main(self, mock__main):
        main()
        mock__main.assert_called_once()

    @patch("substack_heart.report_run")
    @patch("substack_heart._main")
    def test_exception_reports_crashed_and_reraises(self, mock__main, mock_report):
        mock__main.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            main()
        args = mock_report.call_args[0]
        assert args[0] == "substack_heart" and args[1] == "crashed"


class TestReauth:
    @patch("substack_heart.get_gmail_service")
    @patch("os.remove")
    @patch("os.path.exists", return_value=True)
    def test_deletes_existing_token_then_reauths(self, _exists, mock_remove, mock_get_svc):
        reauth()
        mock_remove.assert_called_once_with(TOKEN_FILE)
        mock_get_svc.assert_called_once()

    @patch("substack_heart.get_gmail_service")
    @patch("os.remove")
    @patch("os.path.exists", return_value=False)
    def test_skips_delete_when_no_token(self, _exists, mock_remove, mock_get_svc):
        reauth()
        mock_remove.assert_not_called()
        mock_get_svc.assert_called_once()


class TestInnerMain:
    def _setup_gmail(self, msg):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.get.return_value.execute.return_value = msg
        return svc

    def _mock_pw(self):
        pw = MagicMock()
        pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        pw.return_value.__exit__ = MagicMock(return_value=False)
        return pw

    @patch("substack_heart.report_run")
    @patch("substack_heart.iter_messages", return_value=iter([]))
    @patch("substack_heart.get_gmail_service")
    def test_no_emails_reports_success_with_zero_counts(self, mock_get_svc, _iter, mock_report):
        mock_get_svc.return_value = MagicMock()
        run_log = MagicMock()
        run_log.messages = []
        _main(run_log)
        args = mock_report.call_args[0]
        assert args[0] == "substack_heart" and args[1] == "success"
        assert args[2] == 0 and args[3] == 0  # hearted, failed

    @patch("substack_heart.report_run")
    @patch("substack_heart.heart_all", return_value=(1, 0, 0, []))
    @patch("substack_heart.check_substack_login", return_value=True)
    @patch("substack_heart.sync_playwright")
    @patch("substack_heart.get_chromium_executable", return_value=None)
    @patch("substack_heart.get_gmail_service")
    def test_emails_present_calls_heart_all_and_reports_outcome(
        self, mock_get_svc, _exe, mock_pw, _login, mock_heart, mock_report
    ):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        html = _subscriber_html()
        msg = _make_msg("m1", "Post title", "writer@substack.com", html)
        svc = self._setup_gmail(msg)
        mock_get_svc.return_value = svc
        with patch("substack_heart.iter_messages", return_value=iter([{"id": "m1"}])):
            run_log = MagicMock()
            run_log.messages = []
            _main(run_log)
        mock_heart.assert_called_once()
        args = mock_report.call_args[0]
        assert args[1] == "success" and args[2] == 1

    @patch("substack_heart.report_run")
    @patch("substack_heart.check_substack_login", return_value=True)
    @patch("substack_heart.sync_playwright")
    @patch("substack_heart.get_chromium_executable", return_value=None)
    @patch("substack_heart.get_gmail_service")
    def test_failed_items_trigger_retry_and_counts_aggregate(
        self, mock_get_svc, _exe, mock_pw, _login, mock_report
    ):
        mock_pw.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pw.return_value.__exit__ = MagicMock(return_value=False)
        html = _subscriber_html()
        msg = _make_msg("m1", "Post title", "writer@substack.com", html)
        svc = self._setup_gmail(msg)
        mock_get_svc.return_value = svc
        failed_item = ("m1", "https://author.substack.com/p/my-post", "Post title")
        with patch("substack_heart.iter_messages", return_value=iter([{"id": "m1"}])):
            with patch("substack_heart.heart_all", side_effect=[
                (0, 1, 0, [failed_item]),
                (1, 0, 0, []),
            ]) as mock_heart:
                with patch("substack_heart.time"):
                    run_log = MagicMock()
                    run_log.messages = []
                    _main(run_log)
        assert mock_heart.call_count == 2
        args = mock_report.call_args[0]
        assert args[2] == 1 and args[3] == 0  # hearted=1, failed=0 after retry
