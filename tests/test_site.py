"""Static checks for the site.

`index.html` is a single hand-written file with no build step and no tests. These
checks read it as text and as markup and assert the things that silently rot:
an anchor pointing at an id that no longer exists, an icon renamed on disk but
not in the markup, a link that leaks `window.opener`.

They are deliberately offline — no network, no browser, standard library only —
so they run anywhere, including in CI on a fork.

Run: python3 -m unittest discover -s tests -v
"""

import json
import os
import re
import unittest
from html.parser import HTMLParser
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")


def read_index():
    with open(INDEX, encoding="utf-8") as fh:
        return fh.read()


class Collector(HTMLParser):
    """Gathers the tags and attributes the checks care about."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.ids = []
        self.metas = {}
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            if key:
                self.metas[key] = attributes.get("content", "")
        if tag == "title":
            self._in_title = True
            self.title = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse():
    collector = Collector()
    collector.feed(read_index())
    collector.close()
    return collector


HTML = parse()
RAW = read_index()


def of_tag(name):
    return [attrs for tag, attrs in HTML.tags if tag == name]


def is_local(url):
    if not url or url.startswith("#"):
        return False
    return not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) and not url.startswith("//")


# ---------------------------------------------------------------------------
# the document itself
# ---------------------------------------------------------------------------

class DocumentTest(unittest.TestCase):
    def test_it_declares_a_doctype(self):
        self.assertTrue(RAW.lstrip().lower().startswith("<!doctype html>"))

    def test_the_language_is_declared(self):
        html_tags = of_tag("html")
        self.assertEqual(len(html_tags), 1)
        self.assertTrue(html_tags[0].get("lang"), "html needs a lang attribute")

    def test_the_character_set_is_declared_first(self):
        head = RAW[RAW.index("<head>"):RAW.index("</head>")]
        charset = head.find("charset")
        self.assertGreaterEqual(charset, 0)
        self.assertLess(charset, head.find("<title>"))

    def test_the_viewport_is_declared(self):
        self.assertIn("width=device-width", HTML.metas.get("viewport", ""))

    def test_the_title_is_present_and_not_empty(self):
        self.assertTrue(HTML.title and HTML.title.strip(), HTML.title)

    def test_there_is_a_description(self):
        description = HTML.metas.get("description") or HTML.metas.get("og:description")
        self.assertTrue(description and len(description) > 40, description)

    def test_no_id_is_used_twice(self):
        seen = set()
        duplicates = []
        for value in HTML.ids:
            if value in seen:
                duplicates.append(value)
            seen.add(value)
        self.assertEqual(duplicates, [])


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

class LinkTest(unittest.TestCase):
    def test_every_in_page_anchor_resolves_to_an_id(self):
        ids = set(HTML.ids)
        missing = sorted({
            attrs["href"][1:]
            for attrs in of_tag("a")
            if attrs.get("href", "").startswith("#") and len(attrs["href"]) > 1
            and attrs["href"][1:] not in ids
        })
        self.assertEqual(missing, [], "anchors with no target: %s" % missing)

    def test_every_section_has_an_id_so_the_nav_can_target_it(self):
        sections = [attrs for attrs in of_tag("section") if "sec" in attrs.get("class", "").split()]
        self.assertTrue(sections)
        without = [attrs for attrs in sections if not attrs.get("id")]
        self.assertEqual(without, [], "a .sec section without an id cannot be linked to")

    def test_every_new_tab_link_is_rel_safe(self):
        unsafe = [
            attrs.get("href", "")
            for attrs in of_tag("a")
            if attrs.get("target") == "_blank"
            and "noopener" not in attrs.get("rel", "")
        ]
        self.assertEqual(unsafe, [], "target=_blank without rel=noopener: %s" % unsafe)

    def test_every_new_tab_link_also_drops_the_referrer(self):
        unsafe = [
            attrs.get("href", "")
            for attrs in of_tag("a")
            if attrs.get("target") == "_blank"
            and "noreferrer" not in attrs.get("rel", "")
        ]
        self.assertEqual(unsafe, [])

    def test_every_external_link_is_https(self):
        insecure = [
            attrs.get("href", "")
            for attrs in of_tag("a")
            if attrs.get("href", "").startswith("http://")
        ]
        self.assertEqual(insecure, [])

    def test_every_link_has_an_href(self):
        self.assertEqual([a for a in of_tag("a") if not a.get("href")], [])

    def test_no_link_is_empty(self):
        self.assertEqual([a for a in of_tag("a") if a.get("href") == ""], [])


# ---------------------------------------------------------------------------
# local assets
# ---------------------------------------------------------------------------

class AssetTest(unittest.TestCase):
    def test_every_local_reference_exists_on_disk(self):
        referenced = set()
        for attrs in of_tag("a") + of_tag("link") + of_tag("script") + of_tag("img"):
            for key in ("href", "src"):
                url = attrs.get(key)
                if url and is_local(url):
                    referenced.add(url.split("?")[0].split("#")[0])
        missing = sorted(url for url in referenced if not os.path.isfile(os.path.join(ROOT, url)))
        self.assertEqual(missing, [], "referenced but not in the repo: %s" % missing)

    def test_every_image_has_alt_text(self):
        without = [attrs.get("src", "?") for attrs in of_tag("img") if "alt" not in attrs]
        self.assertEqual(without, [])

    def test_the_preloaded_photo_is_the_one_on_disk(self):
        fronts = [attrs["data-front"] for _, attrs in HTML.tags if attrs.get("data-front")]
        self.assertTrue(fronts)
        for name in fronts:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)), name)

    def test_the_manifest_parses_and_its_icons_exist(self):
        path = os.path.join(ROOT, "site.webmanifest")
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertTrue(manifest.get("name"))
        self.assertTrue(manifest.get("short_name"))
        self.assertIn(manifest.get("display"), {"standalone", "fullscreen", "minimal-ui", "browser"})
        self.assertTrue(manifest.get("icons"))
        for icon in manifest["icons"]:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, icon["src"])), icon["src"])
            self.assertRegex(icon["sizes"], r"^\d+x\d+$")

    def test_the_manifest_is_linked_from_the_head(self):
        self.assertIn("site.webmanifest", [attrs.get("href") for attrs in of_tag("link")])

    def test_the_favicons_exist(self):
        icons = [attrs["href"] for attrs in of_tag("link") if "icon" in attrs.get("rel", "")]
        self.assertTrue(icons)
        for href in icons:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, href)), href)


# ---------------------------------------------------------------------------
# sharing metadata
# ---------------------------------------------------------------------------

class SharingTest(unittest.TestCase):
    def test_the_required_open_graph_tags_are_present(self):
        for key in ("og:title", "og:description", "og:image", "og:url", "og:type"):
            self.assertTrue(HTML.metas.get(key), key)

    def test_the_social_image_is_absolute_and_https(self):
        for key in ("og:image", "twitter:image"):
            url = HTML.metas.get(key, "")
            parsed = urlparse(url)
            self.assertEqual(parsed.scheme, "https", url)
            self.assertTrue(parsed.netloc, url)

    def test_the_social_image_exists_in_the_repo(self):
        url = HTML.metas["og:image"]
        name = os.path.basename(urlparse(url).path)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, name)), name)

    def test_the_declared_image_size_matches_the_file(self):
        # A wrong og:image:width makes link previews letterbox or crop.
        path = os.path.join(ROOT, os.path.basename(urlparse(HTML.metas["og:image"]).path))
        with open(path, "rb") as fh:
            head = fh.read(24)
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", "expected a PNG")
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        self.assertEqual(str(width), HTML.metas.get("og:image:width"))
        self.assertEqual(str(height), HTML.metas.get("og:image:height"))

    def test_the_canonical_host_matches_the_cname(self):
        with open(os.path.join(ROOT, "CNAME"), encoding="utf-8") as fh:
            cname = fh.read().strip()
        self.assertEqual(urlparse(HTML.metas["og:url"]).netloc, cname)

    def test_the_twitter_card_is_a_large_image(self):
        self.assertEqual(HTML.metas.get("twitter:card"), "summary_large_image")


# ---------------------------------------------------------------------------
# motion
# ---------------------------------------------------------------------------

class MotionTest(unittest.TestCase):
    def test_the_page_animates(self):
        # If this ever stops being true the reduced-motion test below is vacuous.
        self.assertGreaterEqual(RAW.count("animation:"), 1)

    def test_infinite_animation_is_disabled_when_motion_is_reduced(self):
        # WCAG 2.3.3: motion that cannot be turned off must honour the
        # operating system preference.
        self.assertTrue("prefers-reduced-motion" in RAW,
                        "the page animates forever but never asks about reduced motion")
        block = RAW[RAW.index("prefers-reduced-motion"):]
        block = block[: block.find("</style>")] if "</style>" in block else block
        self.assertIn("animation", block)
        self.assertRegex(block, r"animation:\s*none|animation:\s*none\s*!important|animation-duration:\s*0")


if __name__ == "__main__":
    unittest.main()
