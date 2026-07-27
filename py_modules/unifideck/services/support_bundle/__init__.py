"""support_bundle — One-tap diagnostic bundle for bug reports.

Collects every Unifideck log and state file from wherever it actually
lives on the device, audits every path the plugin can touch, probes the
machine, runs a set of derived sanity checks, scrubs credentials, and
writes a single zip into the user's Downloads folder.

Reachable from the frontend as the ``capture_logs`` RPC.

Module map:

``spec``            dataclasses and every size cap
``sources``         registry rows that contribute bytes
``sources_audit``   rows audited but never read (credentials, bulk dirs)
``deny``            the never-read list, checked before any open()
``resolve``         root and destination resolution with fallbacks
``scrub``           content redaction
``path_audit``      where everything should be, and whether it is
``checks``          derived PASS/FAIL verdicts
``probe_device``    hardware and OS identity
``probe_storage``   block devices, filesystems, mount visibility
``probe_stack``     Steam, Decky, our install, Proton/umu, caches
``probe_conflicts`` other plugins, stray processes, lock state
``procscan``        read-only process inspection
``env_report``      assembles the probes into one report
``collect``         builds the archive
``service``         async facade used by the RPC layer

Two properties worth preserving when changing any of this:

1. The registry is an **allowlist**. No row globs the data directory
   wholesale, so a new secret cannot be swept in without a visible
   diff. A test enforces this.
2. Everything is **read-only**. The bundle describes the device; it must
   never repair, delete, or create anything it is reporting on.
"""
from __future__ import annotations

from .service import SupportBundleService

__all__ = ["SupportBundleService"]
