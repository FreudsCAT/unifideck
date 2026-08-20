"""Field-number map for Battle.net's ``product.db``, as data.

py_modules/unifideck/stores/battlenet/product_db/schema.py

Every number here was decoded from a real ``product.db`` on-device
(2026-08-09) by walking the wire format and correlating against the
Battle.net client's own UI. Keeping them in one small module means an
Agent update that renumbers something is a one-file change.

Nothing in this module raises. Unknown field numbers are ignored and
missing ones default — that tolerance *is* the schema-drift defence, and
it is why ``ProductInstall`` uses ``None``/``False`` defaults rather than
requiring fields to be present.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- ProductDb (top level) -------------------------------------------------
# Field 1 is repeated, one per product. Fields 5 and 6 also exist (a struct
# and a varint) and are deliberately unmapped: we saw them on-device, they
# are not needed, and iterating past them must not fail.
F_PRODUCT_INSTALL = 1

# ---- ProductInstall --------------------------------------------------------
# CAUTION: field 1 is a *variant* string, not a stable key. A real install
# of Hearthstone reported uid='hs_beta' while the product code was 'hsb'.
# Lutris keys its table on field 2 and that choice is correct — keying on
# field 1 would have made Hearthstone unmatchable. Always join on CODE.
F_UID = 1
F_PRODUCT_CODE = 2
F_SETTINGS = 3
F_CACHED_STATE = 4

# ---- ProductInstall.settings ----------------------------------------------
F_INSTALL_PATH = 1
F_PLAY_REGION = 2
F_LANGUAGE = 6

# ---- ProductInstall.cached_product_state ----------------------------------
F_BASE_STATE = 1
# Field 4 holds the completion/size struct (see below).
F_SIZE_STATE = 4

# ---- ...base_product_state -------------------------------------------------
F_INSTALLED = 1
F_PLAYABLE = 2
F_UPDATE_COMPLETE = 3
F_VERSION = 7

# ---- ...size/completion struct --------------------------------------------
# F_COMPLETION reads exactly 0.0 for the whole of a download and exactly 1.0
# on completion (measured across a 12.43 GB install). It is a completion
# value expressed as an IEEE-754 double, NOT a progress fraction — do not
# use it to drive a percentage.
#
# F_TOTAL_BYTES is 0 throughout the download and is populated at completion
# with the true total: 12,428,894,444 bytes, matching the client's own
# "12.43 GB" readout exactly. Usable for updates and as a completion
# cross-check; useless for first-install progress.
F_COMPLETION = 2
F_TOTAL_BYTES = 4

# Products that are Battle.net infrastructure rather than games. Matched on
# the CODE (field 2), never the uid.
NON_GAME_CODES = frozenset({"agent", "bna"})


@dataclass(frozen=True, slots=True)
class ProductInstall:
    """One product's state, as far as we choose to understand it."""

    code: str
    uid: str | None = None
    install_path: str | None = None
    play_region: str | None = None
    language: str | None = None
    version: str | None = None
    installed: bool = False
    playable: bool = False
    update_complete: bool = False
    total_bytes: int | None = None

    @property
    def is_game(self) -> bool:
        """False for the Agent and the client itself."""
        return self.code not in NON_GAME_CODES

    @property
    def is_ready(self) -> bool:
        """True only when the install is genuinely finished.

        All three flags flip in a single write — measured at 20 s sampling
        resolution, they went 0/0/0 to 1/1/1 between consecutive samples —
        so requiring the conjunction is safe and there is no partial state
        to race against.
        """
        return self.installed and self.playable and self.update_complete
