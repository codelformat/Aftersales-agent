import importlib
import pkgutil

from mcp.server.fastmcp import FastMCP

from mcp_servers.aftersales import tools


def build_server(host: str = "127.0.0.1", port: int = 8102) -> FastMCP:
    mcp = FastMCP("aftersales", host=host, port=port, stateless_http=True, json_response=True)
    for module_info in pkgutil.iter_modules(tools.__path__, tools.__name__ + "."):
        module = importlib.import_module(module_info.name)
        module.register(mcp)
    return mcp
