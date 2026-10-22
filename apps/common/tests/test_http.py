import pytest
import requests
import responses
from apps.common import http
from responses import matchers

URL = "https://example.test/search"


@pytest.fixture
def mocked():
    with responses.RequestsMock() as mock:
        yield mock


class TestGet:
    def test_returns_body(self, mocked):
        mocked.get(URL, body=b"\x00body")
        assert http.get(URL) == b"\x00body"

    def test_identifies_itself(self, mocked, settings):
        settings.HTTP_USER_AGENT = "qsology-test"
        mocked.get(
            URL,
            match=[matchers.header_matcher({"User-Agent": "qsology-test"})],
        )
        http.get(URL)

    def test_params(self, mocked):
        mocked.get(URL)
        mocked.get(URL)
        http.get(URL, params={"q": "a b", "n": "1"})
        http.get(f"{URL}?x=1", params={"q": "c"})
        assert mocked.calls[0].request.url == f"{URL}?q=a+b&n=1"
        assert mocked.calls[1].request.url == f"{URL}?x=1&q=c"

    def test_timeout(self, mocked):
        mocked.get(URL, match=[matchers.request_kwargs_matcher({"timeout": 5})])
        http.get(URL, timeout=5)

    @pytest.mark.parametrize("status", [403, 404, 500, 503])
    def test_error_responses(self, mocked, status):
        mocked.get(URL, status=status)
        with pytest.raises(http.HTTPError, match=str(status)):
            http.get(URL)

    @pytest.mark.parametrize(
        "error",
        [
            requests.ConnectionError("refused"),
            requests.Timeout("too slow"),
            requests.TooManyRedirects("loop"),
        ],
    )
    def test_errors_wrapped(self, mocked, error):
        mocked.get(URL, body=error)
        with pytest.raises(http.HTTPError, match=r"example\.test"):
            http.get(URL)

    def test_follows_redirects(self, mocked):
        mocked.get(URL, status=302, headers={"Location": f"{URL}/moved"})
        mocked.get(f"{URL}/moved", body=b"moved")
        assert http.get(URL) == b"moved"
