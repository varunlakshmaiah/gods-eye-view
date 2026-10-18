"""
Gods-Eye-View MCP STDIO Server for Claude Desktop.
Communicates with Claude Desktop over stdin/stdout.
"""
import sys
import builtins
import functools

# CRITICAL: In STDIO mode, all application logs must go to stderr.
# Stdout is strictly reserved for JSON-RPC messages.
_orig_print = builtins.print
builtins.print = functools.partial(_orig_print, file=sys.stderr)

import asyncio
from src.main import mcp_server
from src.router import router

async def main():
    # Initialize DB pools and in-memory caches
    await router.initialize()
    try:
        await mcp_server.run_stdio_async()
    finally:
        await router.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
