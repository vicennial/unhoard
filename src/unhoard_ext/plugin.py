"""Lightweight manifest entry point (registered under the ``omnigent.extensions`` group).

Follows the documented V1 manifest (docs/extending/extension_manifest.md in the
Omnigent repository). It imports only ``omnigent.extensions`` and nothing else.
"""
from omnigent.extensions import (
    EXTENSION_API_VERSION,
    ExtensionEntrypoints,
    ExtensionManifest,
    PageContribution,
    PrimaryNavigationContribution,
)

# Publisher-qualified, lowercase, immutable once released. Replace the publisher
# segment with the real publisher name before publishing.
EXTENSION_ID = "vicennial.unhoard"
PAGE_ID = f"{EXTENSION_ID}.home"


def get_manifest() -> ExtensionManifest:
    return ExtensionManifest(
        id=EXTENSION_ID,
        display_name="Bookmarks",
        distribution="unhoard",  # must match the installed package metadata
        version="0.1.0",
        requires_omnigent=">=0.11,<1",
        extension_api=EXTENSION_API_VERSION,
        entrypoints=ExtensionEntrypoints(browser="dist/extension.js", browser_css="dist/extension.css"),
        # No SDK permissions: the page is static until the host offers a sessions.create
        # method and an approved local data source (see the README).
        permissions=frozenset(),
        pages=(PageContribution(id=PAGE_ID, title="Bookmarks", route="bookmarks", view="bookmarks"),),
        primary_navigation=(
            PrimaryNavigationContribution(id=f"{EXTENSION_ID}.primary-nav", label="Bookmarks", page=PAGE_ID,
                                          icon="puzzle", order=500),
        ),
    )
