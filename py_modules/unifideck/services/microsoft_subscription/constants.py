"""Microsoft subscription constants.

OP-22g | py_modules/unifideck/services/microsoft_subscription/constants.py

Constants used by the sub-package : the subscription cache filename,
tier label strings, default check interval.
"""

from __future__ import annotations

_CACHE_STORE_NAME = "microsoft_subscription"
_CACHE_KEY_PREFIX = "ms_sub_"
_DEFAULT_PROBE_URL = "https://xgpuweb.gssv-play-prod.xboxlive.com/v2/login/user"
