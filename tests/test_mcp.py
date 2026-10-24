import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_discovers_tools_without_credentials(tmp_path):
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", "from arca_codex.server import main; main()"],
            env={**os.environ, "ARCA_CODEX_HOME": str(tmp_path)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == {
                    "configuration_status",
                    "arca_health",
                    "billing_parameters",
                    "preview_invoice",
                    "preview_prepared_invoice",
                    "prepare_invoice",
                    "issue_invoice",
                    "invoice_status",
                    "consult_invoice",
                    "reconcile_invoice",
                    "export_invoice_pdf",
                    "invoice_output_options",
                    "save_invoice_pdf",
                    "prepare_invoice_email",
                    "invoice_email_handoff",
                }
                issue = next(t for t in tools.tools if t.name == "issue_invoice")
                assert issue.annotations.destructiveHint is True
                consult = next(t for t in tools.tools if t.name == "consult_invoice")
                assert consult.annotations.readOnlyHint is True
                assert consult.annotations.destructiveHint is False
                assert consult.inputSchema["properties"]["number"]["minimum"] == 1
                invalid = await session.call_tool(
                    "consult_invoice",
                    {
                        "environment": "homologacion",
                        "issuer_cuit": "20123456786",
                        "point_of_sale": 2,
                        "voucher_type": 211,
                        "number": 0,
                    },
                )
                assert invalid.isError
                result = await session.call_tool("configuration_status", {})
                assert not result.isError
                assert result.structuredContent["configured"] is False

    asyncio.run(run())
