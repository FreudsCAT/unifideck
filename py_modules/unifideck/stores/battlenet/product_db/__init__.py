"""Battle.net ``product.db`` parsing — public surface.

py_modules/unifideck/stores/battlenet/product_db/__init__.py

Secondary installed-state source, behind ``aggregate.json``. Supplies the
things plain JSON cannot: whether an install genuinely finished, the
version, and the total install size.
"""

from .reader import parse_product_db, read_product_db
from .schema import NON_GAME_CODES, ProductInstall

__all__ = [
    "NON_GAME_CODES",
    "ProductInstall",
    "parse_product_db",
    "read_product_db",
]
