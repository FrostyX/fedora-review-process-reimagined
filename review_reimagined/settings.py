from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    copr_owner: str = "@fedora-review"
    forgejo_instance: str = "http://forgejo:3000"
    forgejo_namespace: str = "packaging"
    forgejo_repo: str = "package-review"
    forgejo_token: str | None = None
