"""Run one stateful PPTAgent MCP session in a configured isolated environment.

Targets MCP Python SDK 1.x (PPTAgent's FastMCP 2.x dependency generation).
No Studio dependencies are imported here. Output paths are assigned by Studio.
"""
import asyncio
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


async def run() -> None:
    sdk_version = version("mcp")
    if not sdk_version.startswith("1.") or int(sdk_version.split(".")[1]) < 28:
        raise RuntimeError("PPTAgent session requires mcp>=1.28,<2 in its isolated runtime")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    source, request_path, output = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", WORKSPACE=str(output),
               DEEPPRESENTER_WORKSPACE_BASE=str(output / "runtime-workspace"),
               XDG_CACHE_HOME=str(output / "cache"), HF_HOME=str(output / "cache/huggingface"),
               TORCH_HOME=str(output / "cache/torch"), MPLCONFIGDIR=str(output / "cache/matplotlib"))
    bootstrap = "import sys; sys.path.insert(0,sys.argv[1]); from pptagent.mcp_server import main; main()"
    params = StdioServerParameters(command=sys.executable,
                                   args=["-B", "-c", bootstrap, str(source)], env=env, cwd=str(output))
    results = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=120)
            listing = await session.list_tools()
            (output / "mcp-tools.json").write_text(listing.model_dump_json(indent=2), encoding="utf-8")
            available = {tool.name for tool in listing.tools}
            for step in request["steps"]:
                name = step["name"]
                if name not in available:
                    raise ValueError(f"configured server has no tool: {name}")
                arguments = dict(step.get("arguments", {}))
                if name == "save_generated_slides":
                    arguments = {"pptx_path": str(output / "candidate.pptx")}
                result = await asyncio.wait_for(session.call_tool(name, arguments=arguments), timeout=300)
                payload = result.model_dump(mode="json")
                results.append({"name": name, "result": payload})
                (output / "mcp-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                if result.isError:
                    raise ValueError(f"PPTAgent tool failed: {name}; inspect mcp-results.json")
                # Upstream write_slide may return warnings as a successful tool
                # response. Preserve and stop rather than generating anyway.
                for content in result.content:
                    if getattr(content, "type", "") == "text":
                        try:
                            structured = json.loads(content.text)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(structured, dict) and structured.get("warnings"):
                            raise ValueError(f"PPTAgent requested content repair: {name}; inspect mcp-results.json")


if __name__ == "__main__":
    asyncio.run(run())
