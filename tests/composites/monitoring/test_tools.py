"""Tests for monitoring skill deployment tools."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from rhoai_mcp.composites.monitoring.tools import deploy_monitoring_skill, register_tools


@pytest.fixture
def mock_mcp() -> MagicMock:
    """Create a mock FastMCP server that captures tool registrations."""
    mock = MagicMock()
    registered_tools: dict = {}

    def capture_tool():
        def decorator(f):
            registered_tools[f.__name__] = f
            return f

        return decorator

    mock.tool = capture_tool
    mock._registered_tools = registered_tools
    return mock


@pytest.fixture
def mock_server() -> MagicMock:
    """Create a mock RHOAIServer."""
    server = MagicMock()
    server.config.is_operation_allowed.return_value = (True, None)
    return server


@pytest.fixture
def mock_config() -> MagicMock:
    """Create a mock RHOAIConfig."""
    config = MagicMock()
    config.is_operation_allowed.return_value = (True, None)
    return config


class TestAddMonitoringWithSkillTool:
    """Tests for the add_monitoring_with_skill MCP tool."""

    def test_tool_registration(self, mock_mcp: MagicMock, mock_server: MagicMock) -> None:
        """add_monitoring_with_skill tool is registered."""
        register_tools(mock_mcp, mock_server)
        assert "add_monitoring_with_skill" in mock_mcp._registered_tools

    def test_read_only_mode_blocked(self, mock_mcp: MagicMock, mock_server: MagicMock) -> None:
        """Tool returns error when read_only_mode is enabled."""
        mock_server.config.is_operation_allowed.return_value = (False, "Read-only mode is enabled")
        register_tools(mock_mcp, mock_server)
        tool = mock_mcp._registered_tools["add_monitoring_with_skill"]

        result = tool(
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert "error" in result
        assert "Read-only" in result["error"]


class TestDeployMonitoringSkill:
    """Tests for the deploy_monitoring_skill internal function."""

    def test_permission_denied(self, mock_config: MagicMock) -> None:
        """Returns error when create operations are not allowed."""
        mock_config.is_operation_allowed.return_value = (False, "Read-only mode is enabled")

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert result["success"] is False
        assert "Read-only" in result["error"]

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value=None)
    def test_helm_not_found(self, _mock_which: MagicMock, mock_config: MagicMock) -> None:
        """Returns error when helm binary is not on PATH."""
        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert result["success"] is False
        assert "helm binary not found" in result["error"]

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    def test_successful_deployment(
        self, mock_run: MagicMock, _mock_which: MagicMock, mock_config: MagicMock
    ) -> None:
        """Successful helm deployment returns success dict."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Release installed", stderr="")

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert result["success"] is True
        assert result["release_name"] == "test-release"
        assert result["namespace"] == "test-ns"
        assert result["chart_ref"] == "oci://example.com/chart:1.0"

        argv = mock_run.call_args[0][0]
        assert argv[:7] == [
            "/usr/bin/helm",
            "upgrade",
            "--install",
            "test-release",
            "oci://example.com/chart:1.0",
            "--namespace",
            "test-ns",
        ]

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    def test_deployment_with_context_files(
        self, mock_run: MagicMock, _mock_which: MagicMock, mock_config: MagicMock
    ) -> None:
        """Context files are written to temp dir and passed via --set-file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        context_files = {
            r"references___SKILL-CONTEXT\.md": "# Training Context\n- Model: test/model",
        }

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
            context_files=context_files,
        )

        assert result["success"] is True
        assert result["context_files_count"] == 1

        argv = mock_run.call_args[0][0]
        assert "--set-file" in argv
        set_file_idx = argv.index("--set-file")
        set_file_value = argv[set_file_idx + 1]
        assert set_file_value.startswith(r"extraFiles.references___SKILL-CONTEXT\.md=")

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    def test_helm_failure(
        self, mock_run: MagicMock, _mock_which: MagicMock, mock_config: MagicMock
    ) -> None:
        """Helm failure returns error with stderr."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: chart not found"
        )

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert result["success"] is False
        assert "chart not found" in result["error"]

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    def test_helm_timeout(
        self, mock_run: MagicMock, _mock_which: MagicMock, mock_config: MagicMock
    ) -> None:
        """Timeout returns descriptive error."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="helm", timeout=300)

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
        )

        assert result["success"] is False
        assert "timed out" in result["error"]

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    @patch("rhoai_mcp.composites.monitoring.tools.shutil.rmtree")
    def test_temp_files_cleaned_up_on_failure(
        self,
        mock_rmtree: MagicMock,
        mock_run: MagicMock,
        _mock_which: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        """Temp directory is cleaned up even on failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")

        deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
            context_files={"test___file\\.md": "content"},
        )

        mock_rmtree.assert_called_once()

    @patch("rhoai_mcp.composites.monitoring.tools.shutil.which", return_value="/usr/bin/helm")
    @patch("rhoai_mcp.composites.monitoring.tools.subprocess.run")
    def test_no_context_files(
        self, mock_run: MagicMock, _mock_which: MagicMock, mock_config: MagicMock
    ) -> None:
        """Deployment without context files omits --set-file args."""
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        result = deploy_monitoring_skill(
            config=mock_config,
            namespace="test-ns",
            chart_ref="oci://example.com/chart:1.0",
            release_name="test-release",
            context_files=None,
        )

        assert result["success"] is True
        assert result["context_files_count"] == 0

        argv = mock_run.call_args[0][0]
        assert "--set-file" not in argv


class TestBuildTrainingContextMarkdown:
    """Tests for the training context markdown builder."""

    def test_builds_context_with_all_fields(self) -> None:
        from rhoai_mcp.composites.training.planning import _build_training_context_markdown

        result = _build_training_context_markdown(
            model_id="meta-llama/Llama-2-7b-hf",
            dataset_id="tatsu-lab/alpaca",
            method="lora",
            runtime_name="test-runtime",
            resource_estimate={
                "estimated_params_billion": 7.0,
                "total_required_gb": 25.2,
                "recommended_gpus": 1,
                "storage_gb": 78,
            },
        )

        assert "meta-llama/Llama-2-7b-hf" in result
        assert "tatsu-lab/alpaca" in result
        assert "lora" in result
        assert "test-runtime" in result
        assert "7.0B" in result
        assert "25.2 GB" in result

    def test_handles_none_runtime(self) -> None:
        from rhoai_mcp.composites.training.planning import _build_training_context_markdown

        result = _build_training_context_markdown(
            model_id="test/model",
            dataset_id="test/dataset",
            method="qlora",
            runtime_name=None,
            resource_estimate={},
        )

        assert "auto-selected" in result


class TestPrepareTrainingMonitoringIntegration:
    """Tests for monitoring integration in prepare_training."""

    @patch("rhoai_mcp.composites.training.planning.TrainingClient")
    @patch("rhoai_mcp.composites.training.planning._deploy_training_monitor")
    def test_monitor_deployed_when_ready(
        self,
        mock_deploy_monitor: MagicMock,
        mock_client_class: MagicMock,
        mock_mcp: MagicMock,
        mock_server: MagicMock,
    ) -> None:
        """Monitoring is deployed when prepare_training returns ready=True."""
        # Setup: cluster has GPUs and runtimes
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_resources = MagicMock()
        mock_resources.has_gpus = True
        mock_resources.gpu_info.available = 4
        mock_client.get_cluster_resources.return_value = mock_resources

        mock_runtime = MagicMock()
        mock_runtime.name = "test-runtime"
        mock_client.list_cluster_training_runtimes.return_value = [mock_runtime]

        # Storage exists
        mock_pvc = MagicMock()
        mock_pvc.status.phase = "Bound"
        mock_server.k8s.get_pvc.return_value = mock_pvc

        mock_deploy_monitor.return_value = {"success": True}

        from rhoai_mcp.composites.training.planning import register_tools

        register_tools(mock_mcp, mock_server)
        prepare_training = mock_mcp._registered_tools["prepare_training"]

        result = prepare_training(
            namespace="test-ns",
            model_id="meta-llama/Llama-2-7b-hf",
            dataset_id="tatsu-lab/alpaca",
            create_storage=False,
        )

        assert result["ready"] is True
        mock_deploy_monitor.assert_called_once()
        assert result["monitoring_skill"] == {"success": True}

    @patch("rhoai_mcp.composites.training.planning.TrainingClient")
    @patch("rhoai_mcp.composites.training.planning._deploy_training_monitor")
    def test_monitor_skipped_when_not_ready(
        self,
        mock_deploy_monitor: MagicMock,
        mock_client_class: MagicMock,
        mock_mcp: MagicMock,
        mock_server: MagicMock,
    ) -> None:
        """Monitoring is not attempted when prepare_training is not ready."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.get_cluster_resources.side_effect = Exception("Connection failed")

        from rhoai_mcp.composites.training.planning import register_tools

        register_tools(mock_mcp, mock_server)
        prepare_training = mock_mcp._registered_tools["prepare_training"]

        result = prepare_training(
            namespace="test-ns",
            model_id="meta-llama/Llama-2-7b-hf",
            dataset_id="tatsu-lab/alpaca",
            create_storage=False,
        )

        assert result["ready"] is False
        mock_deploy_monitor.assert_not_called()
        assert result["monitoring_skill"] is None

    @patch("rhoai_mcp.composites.training.planning.TrainingClient")
    @patch("rhoai_mcp.composites.training.planning._deploy_training_monitor")
    def test_monitor_failure_non_blocking(
        self,
        mock_deploy_monitor: MagicMock,
        mock_client_class: MagicMock,
        mock_mcp: MagicMock,
        mock_server: MagicMock,
    ) -> None:
        """Monitoring failure does not affect ready status."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_resources = MagicMock()
        mock_resources.has_gpus = True
        mock_resources.gpu_info.available = 4
        mock_client.get_cluster_resources.return_value = mock_resources

        mock_runtime = MagicMock()
        mock_runtime.name = "test-runtime"
        mock_client.list_cluster_training_runtimes.return_value = [mock_runtime]

        mock_pvc = MagicMock()
        mock_pvc.status.phase = "Bound"
        mock_server.k8s.get_pvc.return_value = mock_pvc

        mock_deploy_monitor.return_value = {"success": False, "error": "helm not found"}

        from rhoai_mcp.composites.training.planning import register_tools

        register_tools(mock_mcp, mock_server)
        prepare_training = mock_mcp._registered_tools["prepare_training"]

        result = prepare_training(
            namespace="test-ns",
            model_id="meta-llama/Llama-2-7b-hf",
            dataset_id="tatsu-lab/alpaca",
            create_storage=False,
        )

        assert result["ready"] is True
        assert result["monitoring_skill"]["success"] is False
        assert "helm not found" in result["monitoring_skill"]["error"]
