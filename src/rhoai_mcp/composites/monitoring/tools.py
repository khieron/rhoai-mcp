"""MCP Tools for deploying monitoring Skills via helm charts."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from rhoai_mcp.config import RHOAIConfig
    from rhoai_mcp.server import RHOAIServer

logger = logging.getLogger(__name__)


def deploy_monitoring_skill(
    config: RHOAIConfig,
    namespace: str,
    chart_ref: str,
    release_name: str,
    context_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Deploy a monitoring Skill CRD via helm upgrade --install.

    This is the shared implementation used by both the add_monitoring_with_skill
    MCP tool and internal callers like prepare_training.

    Args:
        config: Server configuration (for permission checks).
        namespace: Target namespace for the helm release.
        chart_ref: OCI helm chart reference (e.g., oci://ghcr.io/.../chart:tag).
        release_name: Helm release name.
        context_files: Optional dict mapping path-encoded keys (with triple-underscore
            separators and backslash-escaped dots) to file content strings.
            These are passed via --set-file 'extraFiles.<key>=<tmpfile>'.

    Returns:
        Dict with success/error status and deployment details.
    """
    allowed, reason = config.is_operation_allowed("create")
    if not allowed:
        return {"error": reason, "success": False}

    helm_path = shutil.which("helm")
    if not helm_path:
        return {
            "error": "helm binary not found on PATH. Install helm to deploy monitoring skills.",
            "success": False,
        }

    tmp_dir: Path | None = None
    try:
        set_file_args: list[str] = []
        if context_files:
            tmp_dir = Path(tempfile.mkdtemp(prefix="rhoai-mcp-skill-"))
            for i, (key, content) in enumerate(context_files.items()):
                file_path = tmp_dir / f"context-{i}.txt"
                file_path.write_text(content, encoding="utf-8")
                set_file_args.extend(["--set-file", f"extraFiles.{key}={file_path}"])

        argv = [
            helm_path,
            "upgrade",
            "--install",
            release_name,
            chart_ref,
            "--namespace",
            namespace,
            *set_file_args,
        ]

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "release_name": release_name,
                "namespace": namespace,
                "chart_ref": chart_ref,
                "error": result.stderr or f"helm exited with code {result.returncode}",
            }

        return {
            "success": True,
            "release_name": release_name,
            "namespace": namespace,
            "chart_ref": chart_ref,
            "context_files_count": len(context_files) if context_files else 0,
            "stdout": result.stdout,
            "stderr": result.stderr if result.stderr else None,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "release_name": release_name,
            "namespace": namespace,
            "chart_ref": chart_ref,
            "error": "Helm deployment timed out after 300 seconds",
        }
    except Exception as e:
        return {
            "success": False,
            "release_name": release_name,
            "namespace": namespace,
            "chart_ref": chart_ref,
            "error": f"Unexpected error: {e}",
        }
    finally:
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def register_tools(mcp: FastMCP, server: RHOAIServer) -> None:
    """Register monitoring tools with the MCP server."""

    @mcp.tool()
    def add_monitoring_with_skill(
        namespace: str,
        chart_ref: str,
        release_name: str,
        context_files: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Deploy a monitoring Skill via a helm chart.

        Deploys a Skill CRD using ``helm upgrade --install`` with the provided
        OCI chart reference. Context files are injected into the Skill via
        the chart's ``extraFiles`` value using ``--set-file``.

        The Skill CRD is executed in a loop by the khieron-controller
        (must be pre-installed in the cluster).

        Args:
            namespace: Target namespace for the monitoring skill deployment.
            chart_ref: OCI helm chart reference
                (e.g., "oci://ghcr.io/khieron/charts/training-health-monitor-skill:0.1.2").
            release_name: Name for the helm release.
            context_files: Optional dict mapping path-encoded keys to file content strings.
                Keys use triple-underscore (___) for directory separators and
                backslash-escaped dots in filenames.
                Example: {"references___SKILL-CONTEXT\\\\.md": "# Context\\n..."}

        Returns:
            Deployment result with success status, helm output, and error details.
        """
        allowed, reason = server.config.is_operation_allowed("create")
        if not allowed:
            return {"error": reason}

        return deploy_monitoring_skill(
            config=server.config,
            namespace=namespace,
            chart_ref=chart_ref,
            release_name=release_name,
            context_files=context_files,
        )
