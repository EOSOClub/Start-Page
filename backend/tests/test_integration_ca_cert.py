"""The per-integration "Trusted CA certificate" field: validation, and a real TLS handshake on
127.0.0.1 against a certificate made in the test (needs `cryptography`; skipped without it)."""
import asyncio
import datetime
import http.server
import ipaddress
import re
import ssl
import threading

import certifi
import httpx
import pytest

from app.integrations import base, errors
from app.integrations.base import Context, ca_cert_error
from app.integrations.registry import SPECS

from conftest import HEADERS


def _system_ca() -> str:
    """One real CA certificate in PEM form (the first in certifi's bundle)."""
    with open(certifi.where(), encoding="ascii") as fh:
        return re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", fh.read(), re.S).group(0)


def _post(client, config):
    return client.post("/api/integrations", headers=HEADERS, json={"name": "J", "type": "json", "config": config})


def test_every_http_type_has_the_field_and_host_does_not():
    for type_, spec in SPECS.items():
        keys = [f["key"] for f in spec.fields]
        assert ("ca_cert" in keys) == spec.uses_http, type_
    field = next(f for f in SPECS["pihole"].fields if f["key"] == "ca_cert")
    assert field["type"] == "textarea" and not field.get("secret") and not field.get("required")


def test_ca_cert_error():
    assert ca_cert_error(_system_ca()) is None
    assert ca_cert_error(_system_ca() + "\n" + _system_ca()) is None  # a chain / several CAs
    assert "PEM" in ca_cert_error("not a certificate")
    assert "PEM" in ca_cert_error(None)
    assert "private key" in ca_cert_error(_system_ca() + "\n-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----")
    broken = "-----BEGIN CERTIFICATE-----\nAAAAnotbase64!!\n-----END CERTIFICATE-----"
    assert ca_cert_error(broken) == "is not a valid PEM certificate"


def test_tls_verify_value():
    spec = SPECS["json"]
    assert Context(spec, {"url": "https://x"}).tls_verify is True
    assert Context(spec, {"url": "https://x", "verify_tls": False, "ca_cert": _system_ca()}).tls_verify is False
    context = Context(spec, {"url": "https://x", "ca_cert": _system_ca()}).tls_verify
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    with pytest.raises(errors.IntegrationError) as exc:  # a row stored by hand with a bad value
        Context(spec, {"url": "https://x", "ca_cert": "garbage"}).tls_verify
    assert "garbage" not in str(exc.value)


def test_api_validates_and_stores(client):
    bad = _post(client, {"url": "https://x.lan/api", "ca_cert": "nope"})
    assert bad.status_code == 422
    assert "ca_cert" in str(bad.json()) and "nope" not in str(bad.json())
    key = _post(client, {"url": "https://x.lan/api",
                         "ca_cert": _system_ca() + "\n-----BEGIN RSA PRIVATE KEY-----\nAA\n-----END RSA PRIVATE KEY-----"})
    assert key.status_code == 422 and "private key" in str(key.json())


def test_api_round_trip(client, monkeypatch):
    async def no_fetch(*a, **k):  # creating an integration fetches once; keep the network out of it
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_fetch)
    pem = _system_ca()
    res = _post(client, {"url": "https://x.lan/api", "ca_cert": pem})
    assert res.status_code in (200, 201), res.text
    assert res.json()["config"]["ca_cert"] == pem  # a certificate is public: shown, not masked
    iid = res.json()["id"]
    cleared = client.patch(f"/api/integrations/{iid}", headers=HEADERS, json={"config": {"url": "https://x.lan/api", "ca_cert": ""}})
    assert cleared.status_code == 200 and "ca_cert" not in cleared.json()["config"]


# ---------------------------------------------------------------- a real handshake
def _self_signed():
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "startpage-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return cert_pem, key_pem


@pytest.fixture
def https_server(tmp_path):
    cert_pem, key_pem = _self_signed()
    (tmp_path / "c.pem").write_text(cert_pem)
    (tmp_path / "k.pem").write_text(key_pem)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/", cert_pem
    finally:
        server.shutdown()
        server.server_close()


def _get(cfg):
    async def go():
        ctx = Context(SPECS["json"], cfg)
        try:
            return (await ctx.client.get(cfg["url"])).json()
        finally:
            await ctx.aclose()
    return asyncio.run(go())


def test_handshake_with_and_without_the_ca(https_server):
    url, pem = https_server
    assert _get({"url": url, "ca_cert": pem}) == {"ok": True}  # trusted through the pasted certificate
    assert _get({"url": url, "verify_tls": False}) == {"ok": True}  # verification off still works
    for cfg in ({"url": url}, {"url": url, "ca_cert": _system_ca()}):  # no CA, and the wrong CA
        with pytest.raises(httpx.ConnectError) as exc:
            _get(cfg)
        assert "Trusted CA certificate" in errors.safe_error(exc.value, cfg)


def test_hostname_must_still_match(https_server):
    url, pem = https_server  # the certificate names 127.0.0.1 only
    with pytest.raises(httpx.ConnectError):
        _get({"url": url.replace("127.0.0.1", "localhost"), "ca_cert": pem})
