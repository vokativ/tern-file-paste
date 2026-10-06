#!/usr/bin/env python3
"""Read a clipboard file list and upload it; configuration is JSON on stdin."""

import json
import sys

from clipboard import ClipboardError, read_files
from transfer import TransferError, upload_files


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON configuration object on stdin")
        target = request.get("target")
        if not isinstance(target, str) or not target:
            raise ValueError("Configure an SSH target for this Tern host")
        timeout = request.get("timeout", 300)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer in seconds")
        port = request.get("port")
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
            raise ValueError("port must be an SSH port between 1 and 65535")
        identity_file = request.get("identity_file")
        if identity_file is not None and (not isinstance(identity_file, str) or not identity_file):
            raise ValueError("identity_file must be a nonempty local private-key path")
        paths = read_files()
        if not paths:
            raise ClipboardError("No copied files in the clipboard. Copy files in your file manager first; text and image-only clipboards are not uploaded.")
        result = upload_files(paths, target, port=port, identity_file=identity_file, timeout=timeout)
        print(json.dumps(result))
        return 0
    except (ClipboardError, TransferError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
