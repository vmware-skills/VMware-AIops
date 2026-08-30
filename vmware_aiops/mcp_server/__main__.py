"""Entry point for running the MCP server: python -m vmware_aiops.mcp_server."""

from vmware_aiops.mcp_server.server import main

# Guarded, though this file exists to be run: `python -m <pkg>.mcp_server` still
# executes it with __name__ == "__main__". Without the guard, merely *importing*
# it starts the server — which is what anything walking the package tree does,
# including this family's own smoke gate, where it blocked on a stdin that never
# reached EOF and left processes running for a day (2026-08-30).
if __name__ == "__main__":
    main()
