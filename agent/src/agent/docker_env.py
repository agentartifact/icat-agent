from __future__ import annotations
import subprocess
import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_swebench_image_name(instance_id: str, image_name: str = None) -> str:
    """
    Get the SWE-bench Docker image name for an instance.

    Following SWE-agent's convention (sweagent/run/batch_instances.py:171-178):
    - If image_name is provided in the instance data, use it directly
    - Otherwise, construct it from instance_id using the _1776_ convention

    Args:
        instance_id: SWE-bench instance ID (e.g., 'astropy__astropy-13033')
        image_name: Optional pre-specified image name from instance data

    Returns:
        Docker image name (e.g., 'swebench/sweb.eval.x86_64.astropy_1776_astropy-13033:latest')
    """
    if image_name is not None:
        # Use the provided image name directly (some datasets include this)
        return image_name

    # Docker doesn't allow double underscore, so we replace them with a magic token
    # This follows SWE-agent's convention: iid.replace("__", "_1776_")
    id_docker_compatible = instance_id.replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()


class DockerEnvironment:
    """
    Manages a Docker container for running commands in a SWE-bench environment.

    The container has the repository pre-cloned at /testbed with all dependencies installed.
    Commands are automatically run with the 'testbed' conda environment activated.
    """

    # Conda activation prefix for swebench containers
    CONDA_ACTIVATE = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && "

    def __init__(self, instance_id: str, timeout: int = 300, image_name: str = None, repo_path: str = None):
        """
        Initialize Docker environment for an instance.

        Args:
            instance_id: SWE-bench instance ID
            timeout: Default timeout for commands in seconds
            image_name: Optional pre-specified Docker image name (if not provided,
                        it will be constructed from instance_id following SWE-agent convention)
            repo_path: Root path of the repo inside the container (/testbed for SWE-bench, /app for Pro)
        """
        self.instance_id = instance_id
        self.image_name = get_swebench_image_name(instance_id, image_name)
        self.container_id: Optional[str] = None
        self.timeout = timeout
        self.repo_path = repo_path or "/testbed"  # /testbed for SWE-bench, /app for Pro
        # Pro images don't use conda
        self.use_conda = (self.repo_path != "/app")

    def start(self) -> bool:
        """
        Start the Docker container.

        Returns:
            True if container started successfully, False otherwise
        """
        try:
            # Check if image exists locally first, then pull if not
            inspect_result = subprocess.run(
                ["docker", "image", "inspect", self.image_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            if inspect_result.returncode == 0:
                logger.info(f"[Docker] Image found locally: {self.image_name}")
            else:
                logger.info(f"[Docker] Pulling image: {self.image_name}")
                pull_result = subprocess.run(
                    ["docker", "pull", self.image_name],
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                if pull_result.returncode != 0:
                    logger.error(f"[Docker] Failed to pull image: {pull_result.stderr}")
                    return False

            # Start container in detached mode
            # --entrypoint="" overrides image ENTRYPOINT (e.g. /bin/bash in
            # jefzda/sweap-images for SWE-bench Pro) so that the keep-alive
            # command runs directly instead of being misinterpreted by bash.
            logger.info(f"[Docker] Starting container for {self.instance_id}")
            run_result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--platform", "linux/amd64",
                    "--entrypoint", "",
                    "-w", self.repo_path,
                    self.image_name,
                    "tail", "-f", "/dev/null"  # Keep container running
                ],
                capture_output=True,
                text=True,
                timeout=60
            )

            if run_result.returncode != 0:
                logger.error(f"[Docker] Failed to start container: {run_result.stderr}")
                return False

            self.container_id = run_result.stdout.strip()
            logger.info(f"[Docker] Container started: {self.container_id[:12]}")

            # Wait for container to be ready
            time.sleep(1)

            return True

        except subprocess.TimeoutExpired:
            logger.error("[Docker] Timeout starting container")
            return False
        except Exception as e:
            logger.error(f"[Docker] Error starting container: {e}")
            return False

    def run_command_with_stdin(
        self,
        command: str,
        input_data: str,
        timeout: Optional[int] = None,
        workdir: Optional[str] = None,
        activate_conda: bool = True
    ) -> Tuple[int, str, str]:
        """
        Run a command inside the Docker container, piping input_data via stdin.

        This avoids the OS ARG_MAX limit (~2MB) that causes 'Argument list too long'
        errors when large data is passed as command-line arguments.

        Args:
            command: Command to execute (should read from stdin, e.g. 'base64 -d > file')
            input_data: Data to pipe via stdin
            timeout: Timeout in seconds (uses default if not specified)
            workdir: Working directory (uses repo_path if not specified)
            activate_conda: Whether to activate the testbed conda environment (default True)

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if not self.container_id:
            logger.error("[Docker] Container not started")
            return (-1, "", "Container not started")

        timeout = timeout or self.timeout
        workdir = workdir or self.repo_path

        if activate_conda and self.use_conda:
            full_command = f"{self.CONDA_ACTIVATE}{command}"
        else:
            full_command = command

        try:
            result = subprocess.run(
                [
                    "docker", "exec", "-i",
                    "-w", workdir,
                    self.container_id,
                    "bash", "-c", full_command
                ],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            return (result.returncode, result.stdout, result.stderr)

        except subprocess.TimeoutExpired:
            logger.warning(f"[Docker] Command timed out after {timeout}s: {command[:100]}")
            return (-1, "", f"Command timed out after {timeout}s")
        except Exception as e:
            logger.error(f"[Docker] Error running command: {e}")
            return (-1, "", str(e))

    def run_command(
        self,
        command: str,
        timeout: Optional[int] = None,
        workdir: Optional[str] = None,
        activate_conda: bool = True
    ) -> Tuple[int, str, str]:
        """
        Run a command inside the Docker container.

        Args:
            command: Command to execute
            timeout: Timeout in seconds (uses default if not specified)
            workdir: Working directory (uses repo_path if not specified)
            activate_conda: Whether to activate the testbed conda environment (default True)

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if not self.container_id:
            logger.error("[Docker] Container not started")
            return (-1, "", "Container not started")

        timeout = timeout or self.timeout
        workdir = workdir or self.repo_path

        # Prepend conda activation to ensure proper environment
        if activate_conda and self.use_conda:
            full_command = f"{self.CONDA_ACTIVATE}{command}"
        else:
            full_command = command

        try:
            result = subprocess.run(
                [
                    "docker", "exec",
                    "-w", workdir,
                    self.container_id,
                    "bash", "-c", full_command
                ],
                capture_output=True,
                text=True,
                timeout=timeout
            )

            return (result.returncode, result.stdout, result.stderr)

        except subprocess.TimeoutExpired:
            logger.warning(f"[Docker] Command timed out after {timeout}s: {command[:100]}")
            return (-1, "", f"Command timed out after {timeout}s")
        except Exception as e:
            logger.error(f"[Docker] Error running command: {e}")
            return (-1, "", str(e))

    def run_tests(self, test_command: str, timeout: int = 600) -> Tuple[bool, str]:
        """
        Run tests inside the container.

        Args:
            test_command: Test command to run (e.g., 'pytest tests/test_foo.py')
            timeout: Test timeout in seconds

        Returns:
            Tuple of (success, output)
        """
        logger.info(f"[Docker] Running tests: {test_command}")
        returncode, stdout, stderr = self.run_command(test_command, timeout=timeout)

        output = stdout + stderr
        success = returncode == 0

        if success:
            logger.info("[Docker] Tests passed")
        else:
            logger.warning(f"[Docker] Tests failed (exit code: {returncode})")

        return (success, output)

    def apply_patch(self, patch_content: str) -> Tuple[bool, str]:
        """
        Apply a patch to the repository.

        Args:
            patch_content: The patch content in unified diff format

        Returns:
            Tuple of (success, output)
        """
        if not patch_content.strip():
            return (False, "Empty patch content")

        # Write patch to a temp file in container
        escaped_patch = patch_content.replace("'", "'\\''")
        write_cmd = f"echo '{escaped_patch}' > /tmp/patch.diff"
        returncode, _, stderr = self.run_command(write_cmd)

        if returncode != 0:
            return (False, f"Failed to write patch: {stderr}")

        # Apply the patch
        apply_cmd = "git apply /tmp/patch.diff"
        returncode, stdout, stderr = self.run_command(apply_cmd)

        if returncode != 0:
            # Try with --3way for better conflict handling
            apply_cmd = "git apply --3way /tmp/patch.diff"
            returncode, stdout, stderr = self.run_command(apply_cmd)

        output = stdout + stderr
        success = returncode == 0

        if success:
            logger.info("[Docker] Patch applied successfully")
        else:
            logger.warning(f"[Docker] Failed to apply patch: {output}")

        return (success, output)

    def reset_repo(self) -> bool:
        """
        Reset the repository to clean state.

        Returns:
            True if reset successful
        """
        returncode, _, stderr = self.run_command("git checkout -- . && git clean -fd")
        if returncode != 0:
            logger.warning(f"[Docker] Failed to reset repo: {stderr}")
            return False
        return True

    def get_file_content(self, filepath: str) -> Optional[str]:
        """
        Read a file from the container.

        Args:
            filepath: Path to file (relative to repo or absolute)

        Returns:
            File content or None if failed
        """
        if not filepath.startswith("/"):
            filepath = f"{self.repo_path}/{filepath}"

        returncode, stdout, _ = self.run_command(f"cat '{filepath}'")
        if returncode != 0:
            return None
        return stdout

    def write_file(self, filepath: str, content: str) -> bool:
        """
        Write content to a file in the container.

        Args:
            filepath: Path to file (relative to repo or absolute)
            content: Content to write

        Returns:
            True if successful
        """
        if not filepath.startswith("/"):
            filepath = f"{self.repo_path}/{filepath}"

        # Use base64 + stdin piping to handle special characters and large files
        # (avoids OS ARG_MAX limit that causes 'Argument list too long' errors)
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        cmd = f"base64 -d > '{filepath}'"

        returncode, _, stderr = self.run_command_with_stdin(cmd, input_data=encoded)
        if returncode != 0:
            logger.error(f"[Docker] Failed to write file: {stderr}")
            return False
        return True

    def stop(self):
        """Stop and remove the Docker container."""
        if self.container_id:
            try:
                subprocess.run(
                    ["docker", "stop", self.container_id],
                    capture_output=True,
                    timeout=30
                )
                subprocess.run(
                    ["docker", "rm", self.container_id],
                    capture_output=True,
                    timeout=30
                )
                logger.info(f"[Docker] Container stopped: {self.container_id[:12]}")
            except Exception as e:
                logger.warning(f"[Docker] Error stopping container: {e}")
            finally:
                self.container_id = None

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False


def run_in_docker(
    instance_id: str,
    command: str,
    timeout: int = 300
) -> Tuple[int, str, str]:
    """
    Convenience function to run a single command in a Docker container.

    Args:
        instance_id: SWE-bench instance ID
        command: Command to execute
        timeout: Timeout in seconds

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    with DockerEnvironment(instance_id) as env:
        return env.run_command(command, timeout=timeout)


def validate_patch_in_docker(
    instance_id: str,
    patch_content: str,
    test_command: str,
    timeout: int = 600
) -> dict:
    """
    Validate a patch by applying it and running tests in Docker.

    Args:
        instance_id: SWE-bench instance ID
        patch_content: Patch in unified diff format
        test_command: Test command to run
        timeout: Test timeout in seconds

    Returns:
        Dict with validation results
    """
    result = {
        "instance_id": instance_id,
        "patch_applied": False,
        "tests_passed": False,
        "output": "",
        "error": None
    }

    try:
        with DockerEnvironment(instance_id, timeout=timeout) as env:
            # Apply patch
            success, output = env.apply_patch(patch_content)
            result["patch_applied"] = success
            result["output"] = output

            if not success:
                result["error"] = "Failed to apply patch"
                return result

            # Run tests
            success, output = env.run_tests(test_command, timeout=timeout)
            result["tests_passed"] = success
            result["output"] = output

    except Exception as e:
        result["error"] = str(e)

    return result
