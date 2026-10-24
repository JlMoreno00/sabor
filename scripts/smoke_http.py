"""Check initialize and core tools over Streamable HTTP, without writing data."""
import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def smoke(url):
    async with streamablehttp_client(url) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {tool.name for tool in result.tools}
            if not {'search_recipes', 'create_recipe'} <= names:
                raise RuntimeError('Core tools missing from HTTP server')
            print(f'initialize OK; tools/list: {len(names)} tools; '
                  'search_recipes, create_recipe present')


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(smoke(sys.argv[1]), timeout=60))
