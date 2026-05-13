"""Reusable docker-py helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import docker
from docker.models.containers import Container

from btc_relay_module_nxc_impckt.logger import get_logger

logger = get_logger()


def get_client() -> docker.DockerClient:
    return docker.from_env()


def ensure_image(client: docker.DockerClient, image_tag: str, build_context: Optional[Path] = None) -> None:
    """Build image if not present locally."""
    try:
        client.images.get(image_tag)
        logger.info(f"Docker image {image_tag} already exists")
    except docker.errors.ImageNotFound:
        logger.info(f"Building Docker image {image_tag} ...")
        if build_context and build_context.exists():
            subprocess.run(
                ["docker", "build", "-t", image_tag, str(build_context)],
                check=True,
                capture_output=True,
            )
        else:
            raise RuntimeError(f"Image {image_tag} not found and no build context provided")


def run_ephemeral(
    client: docker.DockerClient,
    image: str,
    command: List[str],
    network_mode: str = "host",
    volumes: Optional[Dict[str, Dict[str, str]]] = None,
    environment: Optional[Dict[str, str]] = None,
    working_dir: str = "/workspace",
) -> str:
    """Run a one-off container and return stdout as string."""
    logger.debug(f"docker run --rm {image} {' '.join(command)}")
    container: Container = client.containers.run(
        image,
        command=command,
        network_mode=network_mode,
        volumes=volumes or {},
        environment=environment or {},
        working_dir=working_dir,
        detach=False,
        auto_remove=True,
        stdout=True,
        stderr=True,
    )
    # When auto_remove=False and detach=False, run() returns bytes directly
    if isinstance(container, bytes):
        return container.decode("utf-8", errors="replace")
    return str(container)


def run_detached(
    client: docker.DockerClient,
    image: str,
    command: List[str],
    name: str,
    network_mode: str = "host",
    volumes: Optional[Dict[str, Dict[str, str]]] = None,
    environment: Optional[Dict[str, str]] = None,
) -> Container:
    """Run a detached container and return the Container object."""
    logger.info(f"Starting detached container {name}: {image} {' '.join(command)}")
    container: Container = client.containers.run(
        image,
        command=command,
        name=name,
        network_mode=network_mode,
        volumes=volumes or {},
        environment=environment or {},
        detach=True,
        auto_remove=False,
        stdout=True,
        stderr=True,
    )
    return container


def stop_container(client: docker.DockerClient, name: str, timeout: int = 10) -> None:
    try:
        container = client.containers.get(name)
        container.stop(timeout=timeout)
        container.remove(force=True)
        logger.info(f"Stopped and removed container {name}")
    except docker.errors.NotFound:
        logger.debug(f"Container {name} not found, nothing to stop")
