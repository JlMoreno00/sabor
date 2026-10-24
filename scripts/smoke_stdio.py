"""Exercise an installed wheel via uvx, without Mealie or checkout imports.

Usage: python scripts/smoke_stdio.py /absolute/path/to/sabor_mcp.whl
"""
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def smoke(wheel):
    with TemporaryDirectory(prefix='sabor-smoke-') as directory:
        server = StdioServerParameters(
            command='uvx', args=['--from', str(Path(wheel).resolve()),
                                 'sabor-mcp', '--transport', 'stdio'],
            cwd=directory,
            env={'SABOR_DATA_DIR': directory, 'MEALIE_BASE_URL': '',
                 'MEALIE_API_TOKEN': '', 'SABOR_TOOLSETS': 'core,import,shopping_sync'})
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.list_tools()
                names = {tool.name for tool in result.tools}
                required = {'search_recipes', 'create_recipe'}
                if not required <= names:
                    raise RuntimeError('Core tools missing from installed distribution')
                print(f'initialize OK; tools/list: {len(names)} tools; '
                      'search_recipes, create_recipe present; no Mealie configuration')


if __name__ == '__main__':
    asyncio.run(smoke(sys.argv[1]))
