"""
Launcher for the Control Tower MCP server. Point your agent's MCP config at
this file by absolute path. Python puts this file's directory on sys.path,
so the `app` and `mcp_server` packages import correctly no matter which
directory the harness launches it from (normally the agent's own repo,
which is also the default CT_REPO_ROOT).
"""

from mcp_server.server import main

if __name__ == "__main__":
    main()