"""OCI entrypoint: bootstrap managed site config, then run the gateway."""
from __future__ import annotations

import logging
import os
import sys


def main(argv=None):
    logging.basicConfig(level=logging.INFO)
    from .bootstrap import resolve_config_path

    argv = list(sys.argv[1:] if argv is None else argv)
    # Honour an explicit --config from the process args; otherwise inject the
    # Softwares-managed path from bootstrap.
    if "--config" not in argv:
        config = resolve_config_path()
        os.environ["EQUIPMENT_CONFIG"] = str(config)
        argv = ["--config", str(config), *argv]
    from .runtime import main as runtime_main

    # runtime.main reads sys.argv
    sys.argv = ["equipment-gateway", *argv]
    return runtime_main()


if __name__ == "__main__":
    raise SystemExit(main())
