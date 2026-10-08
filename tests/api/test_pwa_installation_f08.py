"""Install resources must work behind Access and survive framework packaging."""

from __future__ import annotations

import os
import struct
import tomllib
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin, urlsplit

from fastapi.testclient import TestClient

from personal_assistant.app import create_app
from personal_assistant.bootstrap import build_container
from personal_assistant.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


class _ManifestLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and attributes.get("rel") == "manifest":
            self.links.append(attributes)


class PwaInstallationF08Tests(unittest.TestCase):
    def setUp(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.client = TestClient(
            create_app(settings=settings, container=build_container(settings))
        )

    def test_protected_manifest_link_requests_credentials(self) -> None:
        page = self.client.get("/ui/")
        self.assertEqual(200, page.status_code)
        parser = _ManifestLinks()
        parser.feed(page.text)
        self.assertEqual(1, len(parser.links))
        link = parser.links[0]
        self.assertEqual("use-credentials", link.get("crossorigin"))
        self.assertEqual("./manifest.webmanifest", link.get("href"))

    def test_manifest_declares_real_same_origin_android_png_icons(self) -> None:
        response = self.client.get("/ui/manifest.webmanifest")
        self.assertEqual(200, response.status_code)
        manifest = response.json()
        self.assertTrue(manifest.get("name") or manifest.get("short_name"))
        self.assertEqual("/ui/", manifest["start_url"])
        self.assertEqual("/ui/", manifest["scope"])
        self.assertEqual("standalone", manifest["display"])
        self.assertFalse(manifest.get("prefer_related_applications", False))
        self.assertIn("icons", manifest, "Android installation needs real icon resources")
        dimensions = set()
        for icon in manifest["icons"]:
            target = urlsplit(urljoin("http://testserver/ui/manifest.webmanifest", icon["src"]))
            self.assertEqual("testserver", target.hostname)
            self.assertTrue(target.path.startswith("/ui/"))
            self.assertFalse(target.query or target.fragment)
            self.assertEqual("image/png", icon["type"])
            image = self.client.get(target.path)
            self.assertEqual(200, image.status_code)
            self.assertEqual("image/png", image.headers["content-type"])
            self.assertEqual(b"\x89PNG\r\n\x1a\n", image.content[:8])
            self.assertEqual(b"IHDR", image.content[12:16])
            width, height = struct.unpack(">II", image.content[16:24])
            self.assertEqual(icon["sizes"], f"{width}x{height}")
            dimensions.add((width, height))
        self.assertTrue({(192, 192), (512, 512)}.issubset(dimensions))

    def test_framework_package_includes_both_android_icons(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        files = project["tool"]["setuptools"]["data-files"]["share/personal-assistant/pwa"]
        for name in ("icon-192.png", "icon-512.png"):
            self.assertIn(f"web/pwa/{name}", files)
            self.assertTrue((ROOT / "web/pwa" / name).is_file())

    def test_fixed_ui_files_disable_http_cache_including_conditional_responses(self) -> None:
        for path in (
            "/ui/",
            "/ui/app.js",
            "/ui/styles.css",
            "/ui/manifest.webmanifest",
            "/ui/service-worker.js",
            "/ui/service-worker.js?f086=cache-check",
            "/ui/icon-192.png",
            "/ui/icon-512.png",
        ):
            with self.subTest(path=path):
                for method in ("GET", "HEAD"):
                    response = self.client.request(method, path)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("no-store", response.headers.get("cache-control"))
                unchanged = self.client.get(
                    path, headers={"If-None-Match": response.headers["etag"]}
                )
                self.assertEqual(304, unchanged.status_code)
                self.assertEqual("no-store", unchanged.headers.get("cache-control"))

    def test_install_resources_do_not_bypass_access(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PA_TRUST_CLOUDFLARE_ACCESS": "true",
                "PA_CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                "PA_CF_ACCESS_AUD": "test-application-audience",
                "PA_PUBLIC_ORIGIN": "https://assistant.example.test",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        with TestClient(
            create_app(settings=settings, container=build_container(settings))
        ) as client:
            for path in ("/ui/manifest.webmanifest", "/ui/icon-192.png", "/ui/icon-512.png"):
                for headers in ({}, {"Cf-Access-Jwt-Assertion": "not-a-jwt"}):
                    with self.subTest(path=path, forged=bool(headers)):
                        self.assertEqual(401, client.get(path, headers=headers).status_code)


if __name__ == "__main__":
    unittest.main()
