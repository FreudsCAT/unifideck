"""
Microsoft Store download pipeline for Unifideck.

Handles the FE3 (Windows Update) delivery endpoint: SOAP request for
download URLs, package download, extraction (.appx/.msix bundles),
and executable discovery.

All functions are pure — no class state.  They receive explicit parameters
and can be tested independently.
"""

import logging
import os
import re
import urllib.parse
import urllib.request
from typing import List, Optional

from .microsoft_auth import ssl_ctx_strict, ssl_ctx_permissive

logger = logging.getLogger(__name__)

__all__ = [
    "get_fe3_download_urls", "download_file",
    "extract_package", "find_executable",
]

# No module-level constants — all values are passed as parameters
# from the connector, which reads them from settings.json.


# ───────────────────────── FE3 download URLs ─────────────────────────────

def get_fe3_download_urls(
    wu_bundle_id: str,
    xsts_token: str,
    user_hash: str,
    xuid: str,
    device_attrs: str,
    fe3_url: str,
) -> List[str]:
    """
    Call the FE3 GetExtendedUpdateInfo2 SOAP endpoint and return package
    download URLs.

    Args:
        wu_bundle_id: Windows Update bundle ID for the game.
        xsts_token: XSTS authentication token.
        user_hash: XBL user hash (uhs).
        xuid: Xbox User ID.
        device_attrs: FE3 device attribute string.
        fe3_url: FE3 secured endpoint URL (from settings.json).

    Authentication: XBL3.0 x=<user_hash>;<xsts_token> in a WS-Security header.
    """
    if not xsts_token or not user_hash:
        raise RuntimeError("[MS] XSTS token not available for FE3")

    logger.info(f"[MS] Requesting FE3 download URLs for bundle {wu_bundle_id}")

    import uuid as _uuid
    from datetime import datetime, timezone, timedelta
    from xml.sax.saxutils import escape as _xml_escape

    now     = datetime.now(timezone.utc)
    created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg_id  = str(_uuid.uuid4())

    xuid_safe       = _xml_escape(str(xuid or "0"))
    user_hash_safe  = _xml_escape(str(user_hash or ""))
    xsts_token_safe = _xml_escape(str(xsts_token or ""))
    wu_bundle_safe  = _xml_escape(str(wu_bundle_id))

    soap = f"""<s:Envelope
    xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:a="http://www.w3.org/2005/08/addressing"
    xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <s:Header>
    <a:Action s:mustUnderstand="1">http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetExtendedUpdateInfo2</a:Action>
    <a:MessageID>urn:uuid:{msg_id}</a:MessageID>
    <a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>
    <a:To s:mustUnderstand="1">{fe3_url}</a:To>
    <o:Security s:mustUnderstand="1"
        xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <u:Timestamp><u:Created>{created}</u:Created><u:Expires>{expires}</u:Expires></u:Timestamp>
      <o:UsernameToken>
        <o:Username>{xuid_safe}</o:Username>
        <o:Password Type="http://schemas.xmlsoap.org/ws/2005/05/identity/NoProofKey">XBL3.0 x={user_hash_safe};{xsts_token_safe}</o:Password>
      </o:UsernameToken>
    </o:Security>
  </s:Header>
  <s:Body>
    <GetExtendedUpdateInfo2 xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">
      <updateIDs>
        <UpdateIdentity>
          <UpdateID>{wu_bundle_safe}</UpdateID>
          <RevisionNumber>1</RevisionNumber>
        </UpdateIdentity>
      </updateIDs>
      <infoTypes>
        <XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>
        <XmlUpdateFragmentType>FileDecryption</XmlUpdateFragmentType>
        <XmlUpdateFragmentType>Extended</XmlUpdateFragmentType>
      </infoTypes>
      <deviceAttributes>{device_attrs}</deviceAttributes>
    </GetExtendedUpdateInfo2>
  </s:Body>
</s:Envelope>"""

    req = urllib.request.Request(
        fe3_url,
        data=soap.encode("utf-8"),
        headers={"Content-Type": "application/soap+xml; charset=UTF-8", "SOAPAction": ""},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx_strict()) as r:
        response = r.read().decode("utf-8")

    raw_urls = re.findall(r"<Url>([^<]+)</Url>", response)
    urls = [u.strip() for u in raw_urls if u.strip().startswith("http")]
    if not urls:
        logger.warning(f"[MS] FE3 response contained no URLs for {wu_bundle_id}")
        logger.debug(f"[MS] FE3 raw response (first 2000 chars): {response[:2000]}")
    return urls


# ───────────────────────── download ──────────────────────────────────────

def download_file(url: str, dest_path: str, cdn_user_agent: str) -> bool:
    """Download a single package file.

    Args:
        url: Direct download URL for the package.
        dest_path: Local filesystem path to write the file to.
        cdn_user_agent: User-Agent header for CDN requests.

    Returns True on success.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": cdn_user_agent},
        )
        with urllib.request.urlopen(req, timeout=600, context=ssl_ctx_permissive()) as resp:
            chunk_size = 1024 * 1024  # 1 MB
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        logger.info(f"[MS] Downloaded {os.path.basename(dest_path)} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        logger.error(f"[MS] Download failed for {url}: {e}")
        return False


# ───────────────────────── extract ───────────────────────────────────────

def extract_package(pkg_path: str, dest_dir: str, _depth: int = 0) -> bool:
    """
    Extract an .appx / .msix / bundle file into dest_dir.

    .appx/.msix files are ZIP archives.  Bundles contain inner .appx files.
    For Win32 games the outer layer typically contains a standard installer exe.

    _depth is an internal recursion counter — callers should not set it.
    """
    import zipfile
    import tempfile

    _MAX_DEPTH = 3
    if _depth > _MAX_DEPTH:
        logger.warning(f"[MS] extract_package: max recursion depth ({_MAX_DEPTH}) reached, skipping {pkg_path}")
        return False

    real_dest = os.path.realpath(dest_dir)

    try:
        with zipfile.ZipFile(pkg_path, "r") as z:
            members = z.namelist()

            inner_pkgs = [
                m for m in members
                if (m.endswith(".appx") or m.endswith(".msix"))
                and not m.startswith("_")
            ]
            if inner_pkgs:
                with tempfile.TemporaryDirectory() as tmp:
                    for inner in inner_pkgs:
                        z.extract(inner, tmp)
                        extract_package(
                            os.path.join(tmp, inner), dest_dir, _depth=_depth + 1
                        )
            else:
                extract = [
                    m for m in members
                    if not m.startswith("AppxMetadata/")
                    and m not in ("[Content_Types].xml", "AppxBlockMap.xml")
                    and not m.endswith(".appxsym")
                ]
                for member in extract:
                    target = os.path.realpath(os.path.join(dest_dir, member))
                    if not target.startswith(real_dest + os.sep) and target != real_dest:
                        logger.warning(f"[MS] Zip-slip blocked: {member!r} → {target}")
                        continue
                    z.extract(member, dest_dir)
        return True
    except Exception as e:
        logger.error(f"[MS] Extraction failed for {pkg_path}: {e}")
        return False


# ───────────────────────── find executable ───────────────────────────────

def find_executable(install_dir: str) -> Optional[str]:
    """
    Locate the main game executable after extraction.

    Priority:
      1. Common top-level launcher names (case-insensitive).
      2. Largest .exe in the tree (typically the game binary).
    """
    PRIORITY_NAMES = {
        "game.exe", "launcher.exe", "start.exe", "run.exe", "play.exe",
    }
    exe_files = []
    for root, dirs, files in os.walk(install_dir):
        dirs[:] = [d for d in dirs if d not in ("AppxMetadata", "__MACOSX")]
        for fname in files:
            if fname.lower().endswith(".exe"):
                full = os.path.join(root, fname)
                exe_files.append((os.path.getsize(full), fname.lower(), full))

    if not exe_files:
        return None

    for _, name, path in exe_files:
        if name in PRIORITY_NAMES:
            return path

    exe_files.sort(key=lambda x: x[0], reverse=True)
    return exe_files[0][2]
