#!/usr/bin/env python3
"""
CodeBadger Server - Main entry point using FastMCP

This is the main entry point for the CodeBadger Server that provides static code analysis
capabilities through the Model Context Protocol (MCP) using Joern's Code Property Graph.
"""

import asyncio
import logging
import os
import shutil
import socket
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import load_config
from src import defaults
from src.tools.core_tools import CPGGenerationQueue
from src.services import (
    CodebaseTracker,
    GitManager,
    CPGGenerator,
    JoernServerManager,
    PortManager,
    QueryExecutor,
    CodeBrowsingService
)
from src.utils import DBManager, setup_logging
from src.tools import register_tools

# Version information - bump this when releasing new versions
VERSION = "0.3.4-beta"

# Global service instances
services = {}

logger = logging.getLogger(__name__)


def _setup_telemetry(config) -> None:
    """Configure OpenTelemetry SDK if telemetry is enabled.

    Must be called before FastMCP tools are invoked so the tracer provider
    is in place when FastMCP's built-in instrumentation fires.
    """
    telemetry = config.telemetry
    if not telemetry.enabled:
        logger.debug("Telemetry disabled, skipping OpenTelemetry setup")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": telemetry.service_name})
        provider = TracerProvider(resource=resource)

        if telemetry.otlp_protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=telemetry.otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        logger.info(f"OpenTelemetry enabled: exporting to {telemetry.otlp_endpoint} via {telemetry.otlp_protocol}")
    except ImportError:
        logger.warning("OpenTelemetry packages not installed. Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp")
    except Exception as e:
        logger.warning(f"Failed to initialize OpenTelemetry: {e}")


async def _graceful_shutdown():
    """Gracefully shutdown all services"""
    logger.info("Performing graceful shutdown...")

    try:
        # Terminate all Joern servers
        if 'joern_server_manager' in services:
            logger.info("Terminating all Joern servers...")
            services['joern_server_manager'].terminate_all_servers()
            logger.info("All Joern servers terminated")

        # Release all ports
        if 'port_manager' in services:
            logger.info("Releasing allocated ports...")
            try:
                services['port_manager'].release_all_ports()
            except Exception as e:
                logger.warning(f"Error releasing ports: {e}")

        # Stop CPG generation queue
        if 'cpg_queue' in services:
            await services['cpg_queue'].stop()

        # Flush database and caches
        if 'db_manager' in services:
            logger.info("Flushing database...")
            try:
                services['db_manager'].close()
            except Exception as e:
                logger.warning(f"Error closing database: {e}")

        logger.info("Graceful shutdown completed")
    except Exception as e:
        logger.error(f"Error during graceful shutdown: {e}", exc_info=True)


@lifespan
async def app_lifespan(server: FastMCP):
    """Startup and shutdown logic for the FastMCP server"""
    # Load configuration
    config = load_config("config.yaml")
    setup_logging(config.server.log_level)
    logger.info("Starting CodeBadger Server")

    # Setup OpenTelemetry (must happen before tool invocations)
    _setup_telemetry(config)

    # Ensure required directories exist
    os.makedirs(config.storage.workspace_root, exist_ok=True)

    # Create playground directory relative to project root
    project_root = os.path.dirname(os.path.abspath(__file__))
    playground_dir = os.path.join(project_root, "playground")
    cpgs_dir = os.path.join(playground_dir, "cpgs")
    codebases_dir = os.path.join(playground_dir, "codebases")

    os.makedirs(cpgs_dir, exist_ok=True)
    os.makedirs(codebases_dir, exist_ok=True)
    logger.info("Created required directories")

    try:
        # Initialize DB Manager
        db_manager = DBManager(os.path.join(project_root, "codebadger.db"))

        logger.info("DB Manager initialized")

        # Initialize services
        services['config'] = config
        services['db_manager'] = db_manager
        services['codebase_tracker'] = CodebaseTracker(db_manager)
        services['git_manager'] = GitManager(config.storage.workspace_root)

        # Initialize port manager for Joern servers
        services['port_manager'] = PortManager(
            port_min=config.joern.port_min,
            port_max=config.joern.port_max
        )

        # Initialize Joern server manager (runs servers inside Docker container)
        container_name = os.getenv("JOERN_CONTAINER_NAME", "codebadger-joern-server")
        services['joern_server_manager'] = JoernServerManager(
            joern_binary_path=config.joern.binary_path,
            container_name=container_name,
            config=config,
            codebase_tracker=services['codebase_tracker'],
            max_active_servers=config.joern.max_active_servers,
        )

        # Verify the Docker container is running before proceeding
        try:
            import docker
            docker_client = docker.from_env()
            container = docker_client.containers.get(container_name)
            if container.status != "running":
                logger.error(
                    f"Docker container '{container_name}' exists but is not running "
                    f"(status: {container.status}). Please start it with: docker compose up -d"
                )
                raise RuntimeError(
                    f"Docker container '{container_name}' is not running. "
                    f"Run 'docker compose up -d' first."
                )
            logger.info(f"Docker container '{container_name}' is running")
        except docker.errors.NotFound:
            logger.error(
                f"Docker container '{container_name}' not found. "
                f"Please start it with: docker compose up -d"
            )
            raise RuntimeError(
                f"Docker container '{container_name}' not found. "
                f"Run 'docker compose up -d' first."
            )
        except docker.errors.DockerException as e:
            logger.error(f"Cannot connect to Docker daemon: {e}")
            raise RuntimeError(
                f"Cannot connect to Docker daemon. Is Docker running? Error: {e}"
            )

        # Initialize CPG generator (runs Joern CLI directly in container)
        services['cpg_generator'] = CPGGenerator(config=config, joern_server_manager=services['joern_server_manager'])
        # Skip initialize() - no Docker needed

        # Initialize query executor with Joern server manager
        services['query_executor'] = QueryExecutor(
            services['joern_server_manager'],
            config=config.query,
            codebase_tracker=services['codebase_tracker'],
        )

        # Initialize Code Browsing Service
        services['code_browsing_service'] = CodeBrowsingService(
            services['codebase_tracker'],
            services['query_executor'],
            services['db_manager']
        )

        # Start CPG generation queue (B3)
        cpg_queue = CPGGenerationQueue(workers=config.cpg.build_workers)
        await cpg_queue.start()
        services['cpg_queue'] = cpg_queue
        logger.info(f"CPG generation queue started with {config.cpg.build_workers} workers")

        # Register MCP tools now that services are initialized
        register_tools(server, services)

        # Start Joern watchdog (C1) — must run after tools are registered
        services['joern_server_manager'].start_watchdog()
        logger.info("Joern server watchdog started")

        logger.info("All services initialized")
        logger.info("CodeBadger Server is ready")

        yield services

    except Exception as e:
        logger.error(f"Error during server lifecycle: {e}", exc_info=True)
        raise
    finally:
        await _graceful_shutdown()
        logger.info("CodeBadger Server shutdown complete")


def _apply_transforms(server) -> None:
    """Apply CodeMode transform after all tools are registered.

    CodeMode replaces the full 34-tool catalog with three lightweight
    discovery tools + one execute tool, so the LLM only loads schemas
    for the tools it actually needs:

        ListTools   — enumerate every available tool by name (one-shot)
        Search      — natural-language search across tool descriptions
        GetSchemas  — fetch full parameter schemas for selected tools
        execute     — run a Python script that chains call_tool() calls
                      in a sandbox, eliminating sequential round-trips
    """
    from fastmcp.experimental.transforms.code_mode import (
        CodeMode, ListTools, Search, GetSchemas,
    )
    server.add_transform(CodeMode(
        discovery_tools=[ListTools(), Search(), GetSchemas()],
    ))
    logger.info("Transform: CodeMode enabled (ListTools + Search + GetSchemas)")


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    """Return 503 when too many MCP connections are active (B2)."""

    def __init__(self, app, max_concurrent: int = 8):
        super().__init__(app)
        self._sem = asyncio.Semaphore(max_concurrent)

    async def dispatch(self, request: Request, call_next):
        if self._sem._value == 0:
            return Response(
                "Server busy — too many concurrent requests",
                status_code=503,
                headers={"Retry-After": "5"},
            )
        async with self._sem:
            return await call_next(request)


# Initialize FastMCP server
_max_mcp = int(os.getenv("MAX_MCP_CONNECTIONS", str(defaults.MAX_MCP_CONNECTIONS)))
mcp = FastMCP(
    "CodeBadger Server",
    lifespan=app_lifespan,
)
# Note: Tools are registered inside the lifespan function
# register_tools(mcp, services)
# _apply_transforms is called only in __main__ so tests use direct tool access


def _get_disk_usage(path: str) -> dict:
    """Get disk usage information for a path"""
    try:
        stat = shutil.disk_usage(path)
        return {
            "total_gb": round(stat.total / (1024**3), 2),
            "used_gb": round(stat.used / (1024**3), 2),
            "free_gb": round(stat.free / (1024**3), 2),
            "percent_used": round((stat.used / stat.total) * 100, 2) if stat.total > 0 else 0
        }
    except Exception as e:
        logger.debug(f"Error getting disk usage for {path}: {e}")
        return {"error": str(e)}


def _get_cache_size() -> dict:
    """Get cache directory size"""
    try:
        project_root = os.path.dirname(os.path.abspath(__file__))
        cpgs_dir = os.path.join(project_root, "playground", "cpgs")

        total_size = 0
        if os.path.exists(cpgs_dir):
            for dirpath, dirnames, filenames in os.walk(cpgs_dir):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                    except Exception as e:
                        logger.debug(f"Error getting size of {filepath}: {e}")

        return {
            "cache_path": cpgs_dir,
            "size_mb": round(total_size / (1024**2), 2),
            "exists": os.path.exists(cpgs_dir)
        }
    except Exception as e:
        logger.debug(f"Error calculating cache size: {e}")
        return {"error": str(e)}


def _check_joern_container_status() -> dict:
    """Check Joern Docker container status"""
    try:
        if 'joern_server_manager' not in services:
            return {"error": "Joern server manager not initialized"}

        joern_mgr = services['joern_server_manager']
        try:
            container = joern_mgr.docker_client.containers.get(joern_mgr.container_name)
            return {
                "container_name": joern_mgr.container_name,
                "status": container.status,
                "running": container.status == "running"
            }
        except Exception as e:
            logger.debug(f"Error checking container status: {e}")
            return {
                "container_name": joern_mgr.container_name,
                "status": "not_found",
                "running": False,
                "error": str(e)
            }
    except Exception as e:
        logger.debug(f"Error in Joern container status check: {e}")
        return {"error": str(e)}


def _get_active_servers() -> dict:
    """Get information about active Joern servers"""
    try:
        if 'joern_server_manager' not in services:
            return {"error": "Joern server manager not initialized", "count": 0}

        joern_mgr = services['joern_server_manager']
        running_servers = joern_mgr.get_running_servers()

        servers = {}
        for codebase_hash, port in running_servers.items():
            # Check port connectivity
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))
                sock.close()
                is_accessible = result == 0
            except Exception:
                is_accessible = False

            servers[codebase_hash] = {
                "port": port,
                "accessible": is_accessible
            }

        return {
            "count": len(servers),
            "servers": servers
        }
    except Exception as e:
        logger.debug(f"Error getting active servers: {e}")
        return {"error": str(e), "count": 0}


def _get_port_utilization() -> dict:
    """Get port manager utilization information"""
    try:
        if 'port_manager' not in services:
            return {"error": "Port manager not initialized"}

        port_mgr = services['port_manager']
        allocations = port_mgr.get_all_allocations()
        allocated_count = len(allocations)
        available_count = port_mgr.available_count()

        return {
            "allocated_count": allocated_count,
            "available_count": available_count,
            "total_pool_size": allocated_count + available_count,
            "utilization_percent": round((allocated_count / (allocated_count + available_count) * 100)) if (allocated_count + available_count) > 0 else 0
        }
    except Exception as e:
        logger.debug(f"Error getting port utilization: {e}")
        return {"error": str(e)}


def _get_system_memory_available_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().available / (1024 ** 3), 2)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 ** 2), 2)
    except Exception:
        pass
    return -1


def _get_sleeping_servers_count() -> int:
    try:
        if "codebase_tracker" not in services:
            return 0
        tracker = services["codebase_tracker"]
        hashes = tracker.list_codebases()
        count = 0
        for h in hashes:
            info = tracker.get_codebase(h)
            if info and info.metadata.get("status") == "sleeping":
                count += 1
        return count
    except Exception:
        return 0


# Health check endpoint
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for monitoring server status"""
    try:
        project_root = os.path.dirname(os.path.abspath(__file__))

        joern_mgr = services.get("joern_server_manager")
        health_status = {
            "status": "healthy",
            "service": "codebadger",
            "version": VERSION,
            "joern_container": _check_joern_container_status(),
            "active_servers": _get_active_servers(),
            "active_joern_servers": len(joern_mgr.get_running_servers()) if joern_mgr else 0,
            "sleeping_joern_servers": _get_sleeping_servers_count(),
            "lru_eviction_count": joern_mgr._lru_eviction_count if joern_mgr else 0,
            "cpg_build_queue_depth": services["cpg_queue"].depth if "cpg_queue" in services else 0,
            "system_memory_available_gb": _get_system_memory_available_gb(),
            "port_utilization": _get_port_utilization(),
            "disk_usage": _get_disk_usage(project_root),
            "cache_info": _get_cache_size(),
        }

        # Determine overall health status
        container_status = health_status["joern_container"]
        if "error" in container_status or not container_status.get("running", False):
            health_status["status"] = "degraded"

        return JSONResponse(health_status)
    except Exception as e:
        logger.error(f"Error in health check: {e}", exc_info=True)
        return JSONResponse({
            "status": "unhealthy",
            "service": "codebadger",
            "version": VERSION,
            "error": str(e)
        }, status_code=500)


# Root endpoint
@mcp.custom_route("/", methods=["GET"])
async def root(request):
    """Root endpoint providing basic server information"""
    return JSONResponse({
        "service": "codebadger",
        "description": "CodeBadger for static code analysis using Code Property Graph technology",
        "version": VERSION,
        "endpoints": {
            "health": "/health",
            "mcp": "/mcp"
        }
    })


if __name__ == "__main__":
    config_data = load_config("config.yaml")
    host = config_data.server.host
    port = config_data.server.port

    logger.info(f"Starting CodeBadger Server with HTTP transport on {host}:{port}")

    _apply_transforms(mcp)
    _http_middleware = [Middleware(ConcurrencyLimitMiddleware, max_concurrent=_max_mcp)]
    mcp.run_http_async(host=host, port=port, middleware=_http_middleware)