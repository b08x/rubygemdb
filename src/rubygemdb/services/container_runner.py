"""Container runner service for ephemeral gem AST and doc extraction.

Detects Podman (priority) or Docker (fallback), ensures the rubygemdb-indexer:latest
image is available (building automatically if missing), and mounts host gem caches
to extract AST symbol hierarchies and structured decode documentation into a
normalized graph.json artifact.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rubygemdb.core.config import settings

logger = logging.getLogger("rubygemdb.container_runner")

IMAGE_NAME = "rubygemdb-indexer:latest"


class ContainerRunner:
    """Manages container runtime detection, image building, and graph extraction execution."""

    def __init__(
        self,
        runtime: Optional[str] = None,
        dockerfile_path: Optional[Path] = None,
        build_context: Optional[Path] = None,
    ):
        self.runtime = runtime or self.detect_runtime()
        self.dockerfile_path = dockerfile_path or (settings.project_root / "docker" / "indexer.Dockerfile")
        self.build_context = build_context or (settings.project_root / "docker")

    @staticmethod
    def detect_runtime() -> str:
        """Detect available container runtime, prioritizing podman over docker."""
        if shutil.which("podman"):
            return "podman"
        if shutil.which("docker"):
            return "docker"
        raise RuntimeError("No container runtime found. Podman or Docker is required for GraphRAG indexing.")

    def image_exists(self, image_name: str = IMAGE_NAME) -> bool:
        """Check if the indexer container image is already present locally."""
        try:
            cmd = [self.runtime, "images", "-q", image_name]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return bool(result.stdout.strip())
        except Exception as e:
            logger.debug("Error checking container image %s: %s", image_name, e)
            return False

    def build_image(
        self,
        image_name: str = IMAGE_NAME,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Build the indexer container image from docker/indexer.Dockerfile."""
        if not self.dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile not found at {self.dockerfile_path}")

        msg = f"Building container image {image_name} using {self.runtime}..."
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

        cmd = [
            self.runtime,
            "build",
            "-t",
            image_name,
            "-f",
            str(self.dockerfile_path),
            str(self.build_context),
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        output_lines: List[str] = []
        if process.stdout:
            for line in process.stdout:
                output_lines.append(line)
                clean_line = line.strip()
                if clean_line and progress_callback and ("STEP" in clean_line or "Successfully" in clean_line):
                    progress_callback(f"[{self.runtime}] {clean_line}")

        exit_code = process.wait()
        if exit_code != 0:
            full_log = "".join(output_lines)
            raise RuntimeError(f"Failed to build container image {image_name} (exit {exit_code}):\n{full_log}")

        logger.info("Successfully built container image %s", image_name)
        if progress_callback:
            progress_callback(f"Successfully built {image_name}.")

    def ensure_image(
        self,
        image_name: str = IMAGE_NAME,
        force_rebuild: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Ensure container image exists, building it if absent or forced."""
        if force_rebuild or not self.image_exists(image_name):
            self.build_image(image_name, progress_callback=progress_callback)
        else:
            msg = f"Container image '{image_name}' is ready ({self.runtime})."
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)

    def run_indexer(
        self,
        gem_paths: Dict[str, Path],
        output_dir: Path,
        force_rebuild: bool = False,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute containerized graph extraction over mounted gem repositories.

        Args:
            gem_paths: Mapping of gem name to host filesystem directory.
            output_dir: Host directory where graph.json artifact will be written.
            force_rebuild: Force rebuilding the container image before execution.
            progress_callback: Optional callback for status and progress messages.

        Returns:
            Parsed JSON graph artifact dictionary with nodes, edges, and gems.
        """
        if not gem_paths:
            raise ValueError("gem_paths cannot be empty")

        self.ensure_image(image_name=IMAGE_NAME, force_rebuild=force_rebuild, progress_callback=progress_callback)
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd: List[str] = [self.runtime, "run", "--rm"]

        # Mount output directory read-write with SELinux shared label
        resolved_out = output_dir.resolve()
        cmd.extend(["-v", f"{resolved_out}:/output:rw,z"])

        # Mount each gem directory read-only
        container_gem_args: List[str] = []
        for name, host_path in gem_paths.items():
            resolved_gem = host_path.resolve()
            container_path = f"/gems/{name}"
            cmd.extend(["-v", f"{resolved_gem}:{container_path}:ro,z"])
            container_gem_args.append(f"{name}={container_path}")

        # Target image and arguments
        cmd.append(IMAGE_NAME)
        cmd.extend(["-o", "/output/graph.json"])
        cmd.extend(container_gem_args)

        msg = f"Spawning indexer container with {len(gem_paths)} gem(s)..."
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        stdout_lines: List[str] = []
        if process.stdout:
            for line in process.stdout:
                stdout_lines.append(line)
                clean_line = line.strip()
                if clean_line and progress_callback:
                    progress_callback(clean_line)

        process.wait()
        stderr_content = process.stderr.read() if process.stderr else ""
        if process.returncode != 0:
            err_msg = f"Container indexer failed with exit code {process.returncode}:\n{stderr_content.strip()}"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        graph_file = output_dir / "graph.json"
        if not graph_file.exists():
            raise FileNotFoundError(f"Expected graph.json was not generated at {graph_file}")

        try:
            with open(graph_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise ValueError(f"Failed to parse generated graph.json: {e}")

        if not isinstance(data, dict) or "nodes" not in data or "edges" not in data:
            raise ValueError("Invalid graph.json schema: missing 'nodes' or 'edges'")

        logger.info(
            "Graph extraction complete: %d nodes, %d edges",
            len(data.get("nodes", [])),
            len(data.get("edges", [])),
        )
        return data
