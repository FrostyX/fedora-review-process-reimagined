from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    copr_owner: str = "@fedora-review"
    forge_instance: str = "http://forgejo:3000"
    forge_namespace: str = "packaging"
    forge_repo: str = "package-review"
    forge_token: str | None = None
