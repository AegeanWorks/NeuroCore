"""Pinned requests: connect to the vetted address, keep the hostname for Host and TLS (IDEA-063)."""

from core.pinned_http import pinned_target


def test_https_goes_to_the_address_and_keeps_the_hostname_for_host_and_sni():
    url, headers, extensions = pinned_target("https://en.wikipedia.org/w/api.php?q=a#x", "93.184.216.34")
    assert url == "https://93.184.216.34/w/api.php?q=a#x"
    assert headers == {"Host": "en.wikipedia.org"}
    assert extensions == {"sni_hostname": "en.wikipedia.org"}


def test_an_explicit_port_stays_on_the_url_and_the_host_header():
    url, headers, extensions = pinned_target("http://example.com:8080/skill.md", "93.184.216.34")
    assert url == "http://93.184.216.34:8080/skill.md"
    assert headers == {"Host": "example.com:8080"}
    assert extensions == {}, "plain http has no TLS name to carry"


def test_an_ipv6_address_is_bracketed_and_its_zone_dropped():
    url, _headers, _extensions = pinned_target("https://example.com/a", "2606:2800:220:1::1%eth0")
    assert url == "https://[2606:2800:220:1::1]/a"


def test_embedding_key_not_sent_on_unparseable_host():
    from core.llm import LLMBridge

    assert LLMBridge._same_host("nota-url", "http://x:1/v1") is False
    assert LLMBridge._same_host("http://a:1/v1", "http://a:1/v1") is True
    assert LLMBridge._same_host("http://a:1/v1", "http://b:1/v1") is False
