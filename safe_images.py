"""Bezpieczne pobieranie publicznych obrazów przez DNS -> stały publiczny IP.

Ogranicza SSRF w aplikacji przyjmującej dowolne linki od użytkownika.
Dla środowiska firmowego dodatkowo skonfiguruj reguły egress w infrastrukturze.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3

from engine import Cancelled

MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_HOPS = 4


def resolve_public_image(url: str):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Nieprawidłowy lub zbyt długi adres zdjęcia.")
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Wymagany adres HTTP lub HTTPS.")
    if parsed.scheme == "http" and os.getenv("QS_ALLOW_HTTP_IMAGES", "false").lower() != "true":
        raise ValueError("Wersja online przyjmuje tylko adresy HTTPS.")
    if parsed.username or parsed.password:
        raise ValueError("Adres ze składnią login:hasło jest niedozwolony.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Nieprawidłowy port.") from exc
    if port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("Dozwolone są tylko standardowe porty HTTPS/HTTP.")
    hostname = parsed.hostname.rstrip('.').lower()
    if not hostname or hostname.endswith((".local", ".internal", ".localhost", ".test")):
        raise ValueError("Niedozwolona domena lokalna.")
    try:
        hostname_ascii = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Nieprawidłowa nazwa hosta.") from exc
    allowlist = [x.strip().lower() for x in os.getenv("QS_IMAGE_HOSTS", "").split(",") if x.strip()]
    if allowlist and not any(hostname_ascii == h or (h.startswith("*.") and hostname_ascii.endswith(h[1:]) and hostname_ascii != h[2:]) for h in allowlist):
        raise ValueError("Domena zdjęcia nie należy do listy dozwolonych.")
    try:
        records = socket.getaddrinfo(hostname_ascii, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Nie znaleziono adresu serwera zdjęcia.") from exc
    ips = list(dict.fromkeys(item[4][0] for item in records))
    if not ips or not all(ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Zablokowano połączenie z adresem prywatnym lub lokalnym.")
    return parsed, hostname_ascii, port, ips[0]


def download_public_image(url, settings, stop):
    """Pobiera URL z osobną walidacją i przypięciem IP na KAŻDYM przekierowaniu."""
    attempts = settings.retries + 1
    for attempt in range(attempts):
        if stop.is_set():
            raise Cancelled()
        try:
            current = url
            for hop in range(MAX_HOPS + 1):
                if stop.is_set():
                    raise Cancelled()
                parsed, host, port, ip = resolve_public_image(current)
                if parsed.scheme == "https":
                    pool = urllib3.HTTPSConnectionPool(
                        ip, port=port, server_hostname=host, assert_hostname=host,
                        cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), maxsize=1,
                        timeout=urllib3.Timeout(connect=min(settings.timeout, 15), read=settings.timeout))
                else:
                    pool = urllib3.HTTPConnectionPool(ip, port=port, maxsize=1,
                        timeout=urllib3.Timeout(connect=min(settings.timeout, 15), read=settings.timeout))
                response = None
                try:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    host_header = host if port in (80, 443) else f"{host}:{port}"
                    response = pool.request("GET", path, headers={"Host": host_header,
                            "User-Agent": "Quicksilver-Online/2.0", "Accept": "image/*"},
                            preload_content=False, redirect=False, retries=False)
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location or hop == MAX_HOPS:
                            raise ValueError("Zbyt wiele przekierowań zdjęcia.")
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        raise ValueError(f"Serwer zdjęcia zwrócił HTTP {response.status}.")
                    if int(response.headers.get("Content-Length", "0") or "0") > MAX_IMAGE_BYTES:
                        raise ValueError("Zdjęcie jest większe niż 40 MB.")
                    started = time.monotonic()
                    data = bytearray()
                    while True:
                        if stop.is_set():
                            raise Cancelled()
                        if time.monotonic() - started > settings.timeout:
                            raise TimeoutError("Upłynął limit czasu pobierania obrazu.")
                        chunk = response.read(64 * 1024, decode_content=False)
                        if not chunk:
                            return bytes(data)
                        data.extend(chunk)
                        if len(data) > MAX_IMAGE_BYTES:
                            raise ValueError("Zdjęcie jest większe niż 40 MB.")
                finally:
                    if response is not None:
                        response.release_conn()
                    pool.close()
            raise ValueError("Zbyt wiele przekierowań zdjęcia.")
        except Cancelled:
            raise
        except (OSError, urllib3.exceptions.HTTPError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise ValueError(f"Błąd połączenia: {exc}") from exc
            if stop.wait(min(2 ** attempt, 4)):
                raise Cancelled()
