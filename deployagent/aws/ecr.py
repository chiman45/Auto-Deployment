"""boto3 wrappers for Amazon ECR — login, build, and push."""
from __future__ import annotations

import base64
import subprocess

import boto3
from botocore.exceptions import ClientError

from ..errors.retry import aws_retry


class ECRClient:
    def __init__(self, region: str):
        self._client = boto3.client("ecr", region_name=region)
        self._region = region

    @aws_retry
    def _get_auth(self) -> tuple[str, str]:
        """Return (password, registry_endpoint) for docker login."""
        resp = self._client.get_authorization_token()
        auth_data = resp["authorizationData"][0]
        password = base64.b64decode(auth_data["authorizationToken"]).decode().split(":", 1)[1]
        endpoint = auth_data["proxyEndpoint"]
        return password, endpoint

    def login(self) -> None:
        password, endpoint = self._get_auth()
        subprocess.run(
            ["docker", "login", "--username", "AWS", "--password-stdin", endpoint],
            input=password,
            text=True,
            check=True,
            capture_output=True,
        )

    def build_and_push(
        self,
        repository: str,
        tag: str,
        dockerfile: str,
        build_context: str,
    ) -> str:
        """Build the Docker image, login to ECR, push, and return the full image URI."""
        full_uri = f"{repository}:{tag}"
        subprocess.run(
            ["docker", "build", "-t", full_uri, "-f", dockerfile, build_context],
            check=True,
        )
        self.login()
        subprocess.run(["docker", "push", full_uri], check=True)
        return full_uri

    @aws_retry
    def describe_image(self, repository_name: str, tag: str) -> dict | None:
        """Return image metadata, or None if the image/repo does not exist."""
        try:
            resp = self._client.describe_images(
                repositoryName=repository_name,
                imageIds=[{"imageTag": tag}],
            )
            imgs = resp.get("imageDetails", [])
            return imgs[0] if imgs else None
        except ClientError as exc:
            if exc.response["Error"]["Code"] in (
                "ImageNotFoundException",
                "RepositoryNotFoundException",
            ):
                return None
            raise
