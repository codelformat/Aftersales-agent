import argparse

from mcp_servers.logistics.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(description="启动物流 MCP 服务")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    build_server(port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
